"""
UART 通信模块 v4 - 自动重连 + 颜色心跳 + 炸弹类型支持 + 协议 v2 兼容

协议 v1 (LubanCat -> STM32): $type,cx,cy,area,dir*CS\n
  type: E=敌方  N=中立  F=友方  X=无目标  B=炸弹  G=冲台
  cx,cy: 目标中心像素坐标 [0-640 / 0-480]
  area:  目标面积 (像素)
  dir:   方向偏移整数 [-100,+100]  负=左 正=右
  CS:    body 字段的逐字节异或校验和 (十六进制 2位)
  例:    $E,320,240,5000,+25*4A\n

协议 v2 (LubanCat -> STM32): $type,cx,cy,area,dir,ts,conf,tid*CS\n
  ts:    LubanCat HAL_GetTick() 等价时间戳 (ms, 0~65535 滚动)
  conf:  置信度 [0-100] (NPU 输出 score×100; HSV 检测填 50)
  tid:   目标轨迹 ID (Tracker 给的稳定 ID, 0 = 未跟踪)
  v2 STM32 端会忽略多余字段; v1 STM32 端会按 v1 解析多余字段被丢弃 (兼容)。
  默认禁用 v2; 设 config.PROTOCOL_V2 = True 启用。

协议 (STM32 -> LubanCat): 单字节指令
  'b' = 己方蓝色      'y' = 己方黄色
  's' = 开始识别      'p' = 暂停识别 (预留, STM32未实现)
  'c' = 收集模式      'f' = 战斗模式 (预留, STM32未实现)
  'D' = 掉台回复模式   (视觉切换到黑色检测, 发送 G/X)
  'S' = 恢复正常检测   (掉台回复完成, 切回颜色检测)
  'N' = STM32正常重启  (视觉重置为正常识别状态)
"""
import os
import errno
import glob
import time
import serial as _serial_mod
import config


class UartComm:
    _TX_ERROR_THRESHOLD = 3
    _RECONNECT_BASE = 2.0
    _RECONNECT_MAX = 8.0
    _DEVICE_CHECK_INTERVAL = 3.0

    def __init__(self):
        self.ser = None
        self.my_color = 'b'
        self.mode = 'fight'          # 'fight'=格斗(颜色检测) / 'collect'=收集(Tag检测)
        self.active = True           # 是否激活发送
        self.drop_recovery = False   # 掉台回复模式 (黑色检测)
        self._color_changed = False  # 颜色切换标志, 供主循环检测并切换WB
        self._last_send = 0.0
        self._last_dir = 0.0         # 上次发送方向 (自适应频率用)
        self._send_interval = getattr(config, 'UART_TARGET_INTERVAL', 0.050)
        self._no_target_interval = getattr(config, 'UART_NO_TARGET_INTERVAL', 0.10)
        self._fast_interval = getattr(config, 'UART_FAST_INTERVAL', 0.033)
        self._tx_errors = 0
        self._rx_errors = 0          # 读取错误计数
        self._last_reconnect = 0.0
        self._reconnect_backoff = self._RECONNECT_BASE
        self._last_device_check = 0.0
        self._port_path = None       # 当前使用的设备路径
        # 发送统计
        self._tx_total = 0
        self._tx_success = 0
        self._stats_timer = time.time()
        self._STATS_INTERVAL = 60.0
        self._p_received_ts = 0.0
        self._last_sent_type = 'X'
        self._tx_timeouts = 0        # consecutive timeout streak (reset on success)
        self._tx_timeouts_window = 0  # C2: window counter for stats reporting
        # 方向平滑: 已由 Tracker EMA 处理, comm 层不再二次平滑
        # (双重平滑会增加方向响应延迟, 导致机器人追踪滞后)
        # RX 缓冲区: 累积接收数据, 分离回声帧和单字节命令
        self._rx_buf = b''
        # 回声确认: STM32 回传的最近一帧
        self.echo_type = None        # 回声帧类型 ('E'/'G'/'X'/...)
        self.echo_ts = 0.0           # 回声接收时间戳
        self._echo_enabled = getattr(config, 'ECHO_ENABLED', True)
        self._echo_timeout = getattr(config, 'ECHO_TIMEOUT', 1.0)
        self._last_tx_ts = 0.0       # 最近一次发送时间戳
        self._echo_count = 0         # 累计收到的回声帧数
        self._echo_latency = 0.0     # 最近一次回声往返延迟(ms)

    # ------------------------------------------------------------------
    # 开关
    # ------------------------------------------------------------------
    def open(self):
        """打开串口, 启用 low_latency 模式, 失败时静默返回 False"""
        try:
            import serial
            # 重新探测设备 (USB设备号可能变化)
            port = config._find_uart()
            self._port_path = port
            self.ser = serial.Serial(
                port=port,
                baudrate=config.UART_BAUD,
                timeout=config.UART_TIMEOUT,
                write_timeout=0.015,
            )
            # 启用 low_latency 模式 (减少内核缓冲延迟)
            # 解析真实设备路径 (符号链接 → 实际设备)
            real_port = os.path.realpath(port) if os.path.islink(port) else port
            try:
                import subprocess
                result = subprocess.run(
                    ['stty', '-F', real_port, 'low_latency'],
                    capture_output=True, timeout=2, text=True)
                if result.returncode != 0:
                    # v2 (C3): 不再静默失败, 给主人看到提示
                    err_msg = (result.stderr or '').strip() or 'unknown'
                    print(f'[UART] WARN stty low_latency failed ({err_msg}); '
                          f'kernel buffer 延迟可能偏高 ({real_port})', flush=True)
            except FileNotFoundError:
                print(f'[UART] WARN stty 命令不可用, low_latency 未启用 '
                      f'({real_port})', flush=True)
            except subprocess.TimeoutExpired:
                print(f'[UART] WARN stty -F {real_port} 超时 (2s), '
                      f'low_latency 未确认', flush=True)
            except Exception as e:
                print(f'[UART] WARN stty 异常: {e!r}', flush=True)
            # 清空残留数据 (USB 断联重连后可能有脏数据)
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            self._rx_buf = b''
            self._tx_errors = 0
            self._rx_errors = 0
            self._reconnect_backoff = self._RECONNECT_BASE
            self._last_device_check = time.time()
            self._stats_timer = time.time()
            print(f'UART opened: {port} @ {config.UART_BAUD} (low_latency)')
            return True
        except Exception as e:
            print(f'UART open failed: {e}')
            self.ser = None
            return False

    def close(self):
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

    # ------------------------------------------------------------------
    # 内部: 重连
    # ------------------------------------------------------------------
    def _try_reconnect(self):
        """指数退避重连: 2→4→8→8s, 重连时重新探测设备号"""
        now = time.time()
        if now - self._last_reconnect < self._reconnect_backoff:
            return
        self._last_reconnect = now
        # 检查当前配置的设备是否存在 (板载UART/USB/符号链接)
        has_device = False
        if self._port_path and os.path.exists(self._port_path):
            has_device = True
        if not has_device:
            has_usb = bool(glob.glob('/dev/ttyUSB*'))
            has_acm = bool(glob.glob('/dev/ttyACM*'))
            has_sym = os.path.exists('/dev/ttySTM32')
            has_as = bool(glob.glob('/dev/ttyAS*'))
            has_device = has_usb or has_acm or has_sym or has_as
        if not has_device:
            self._reconnect_backoff = min(
                self._reconnect_backoff * 2, self._RECONNECT_MAX)
            return
        print(f'[UART] Reconnecting (backoff={self._reconnect_backoff:.0f}s)...')
        self.close()
        if self.open():
            print('[UART] Reconnected OK')
        else:
            self._reconnect_backoff = min(
                self._reconnect_backoff * 2, self._RECONNECT_MAX)
            print(f'[UART] Reconnect failed, next retry in '
                  f'{self._reconnect_backoff:.0f}s')

    def _check_device_alive(self):
        """周期性检查 USB 设备是否还存在 (热拔插感知)。
        比等 TX 报错更快发现断开。
        """
        now = time.time()
        if now - self._last_device_check < self._DEVICE_CHECK_INTERVAL:
            return True
        self._last_device_check = now
        if self._port_path and not os.path.exists(self._port_path):
            print(f'[UART] Device {self._port_path} disappeared! (USB unplug?)')
            self.close()
            return False
        return True

    # ------------------------------------------------------------------
    # 内部: 校验
    # ------------------------------------------------------------------
    @staticmethod
    def _checksum(data: str) -> str:
        """逐字节异或校验, 返回2位大写十六进制"""
        cs = 0
        for b in data.encode():
            cs ^= b
        return f'{cs:02X}'

    # ------------------------------------------------------------------
    # 内部: 握手确认 (2026-05-30)
    # ------------------------------------------------------------------
    def _send_ack(self, ack_ch):
        """收到STM32的D/S后立即回确认标志位, STM32收到确认才停止重发(否则一直发)。
        ack_ch: 'd'=确认收到D(进掉台), 's'=确认收到S(退掉台)。
        发 $<ack>*CS\\n; STM32需识别小写d/s为确认(不当目标type处理)。
        ⚠️ 默认关(HANDSHAKE_ENABLED=False): STM32端未接前不发ack, 防$d/$s被当目标污染。
        """
        if not getattr(config, 'HANDSHAKE_ENABLED', False):
            return
        if not self.ser:
            return
        try:
            cs = self._checksum(ack_ch)
            self.ser.write(f'${ack_ch}*{cs}\n'.encode())
            # 握手日志(限频每10次1条, 防STM32持续发时刷屏): 对账用
            n = getattr(self, '_ack_n', 0) + 1
            self._ack_n = n
            if n % 10 == 1:
                print(f'[HANDSHAKE] 收到 {ack_ch.upper()} → 回确认 ${ack_ch} '
                      f'给STM32 (#{n})', flush=True)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 内部: 单字节命令解析
    # ------------------------------------------------------------------
    def _parse_cmd_byte(self, ch):
        """解析单字节命令, 返回命令字符或 None"""
        if ch in ('b', 'y'):
            old = self.my_color
            self.my_color = ch
            if old != ch:
                self._color_changed = True
            return ch
        if ch == 's' or ch == 'S':
            self.active = True
            self.drop_recovery = False
            if ch == 'S':
                self._send_ack('s')  # 握手: 回S确认(掉台恢复), STM32收到才停发S
            return ch
        if ch == 'p':
            print('[UART] WARNING: received p cmd → active=False (noise?)', flush=True)
            self.active = False
            self._p_received_ts = time.time()
            return ch
        if ch == 'D':
            # 2026-05-30: 纯光电判断上台后, 视觉不做掉台冲台检测。默认禁用DROP——
            # 收到#D也不进DROP, 视觉永远正常Tag上台检测。VISION_DROP_ENABLED=1 可恢复。
            if getattr(config, 'DROP_RECOVERY_ENABLED', False):
                self.drop_recovery = True
            self._send_ack('d')  # 握手: 回D确认, STM32收到才停发D(否则一直发)
            return ch
        if ch == 'c':
            self.mode = 'collect'
            return ch
        if ch == 'f':
            self.mode = 'fight'
            return ch
        if ch == 'N':
            # STM32 正常重启标志, 视觉需跟随重启
            return ch
        return None

    # ------------------------------------------------------------------
    # 内部: 回声帧解析
    # ------------------------------------------------------------------
    def _parse_echo_frame(self, frame_bytes):
        """解析 STM32 回传的 $type,cx,cy,area,dir*CS\\n 帧。
        更新 echo_type / echo_ts / echo_latency。
        """
        try:
            text = frame_bytes.decode('ascii', errors='ignore').strip()
            if not text.startswith('$') or '*' not in text:
                return
            body_cs = text[1:]  # 去掉 '$'
            star_idx = body_cs.rfind('*')
            if star_idx < 0:
                return
            body = body_cs[:star_idx]
            cs_str = body_cs[star_idx + 1:]

            # 校验和验证
            expected = 0
            for b in body.encode():
                expected ^= b
            if len(cs_str) >= 2:
                received = int(cs_str[:2], 16)
                if expected != received:
                    return  # 校验失败, 丢弃

            # 解析类型字段
            parts = body.split(',')
            if parts:
                now = time.time()
                self.echo_type = parts[0]
                self.echo_ts = now
                self._echo_count += 1
                # 计算往返延迟
                if self._last_tx_ts > 0:
                    self._echo_latency = (now - self._last_tx_ts) * 1000
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 读指令 (v3: 累积所有命令 + 分离回声帧)
    # ------------------------------------------------------------------
    def read_command(self):
        """非阻塞读取 STM32 数据, 分离回声帧和单字节命令。

        RX 缓冲区中混合了:
          - 单字节命令: 'b','y','s','p','c','f','D','N','S'
          - 回声帧: $type,cx,cy,area,dir*CS\\n (STM32原样回传)

        策略: 用 '$' 和 '\\n' 定界回声帧, 帧外散字节扫描命令。
        返回**最后一条**有效命令字符, 或 None。
        如需获取本轮全部命令, 调用方应使用 `read_commands()`。
        """
        cmds = self.read_commands()
        return cmds[-1] if cmds else None

    def read_commands(self):
        """v3: 返回本轮接收到的**所有**单字节命令 (按顺序)。

        修复 vision-code-review.md B3: 旧版 read_command 在多个命令同时到达时
        只返回最后一个 (例如 'D'+'b' 同帧, 掉台模式不会被触发)。
        """
        commands = []

        if not self.ser:
            self._try_reconnect()
            return commands

        if not self._check_device_alive():
            self._try_reconnect()
            return commands

        try:
            if self.ser.in_waiting <= 0:
                if not self._rx_buf:
                    return commands
            else:
                data = self.ser.read(self.ser.in_waiting)
                self._rx_errors = 0
                self._rx_buf += data
        except OSError as e:
            self._rx_errors += 1
            if self._rx_errors >= self._TX_ERROR_THRESHOLD:
                print(f'[UART] RX error #{self._rx_errors}: {e}, reconnecting')
                self._try_reconnect()
                self._rx_errors = 0
            return commands
        except Exception as e:
            print(f'[UART] RX unexpected: {e!r}', flush=True)
            return commands

        # 防止缓冲区溢出 (丢弃旧数据)
        if len(self._rx_buf) > 1024:
            self._rx_buf = self._rx_buf[-512:]

        # 命令定界协议 (2026-05-28): STM32 命令一律 #<cmd>\n 格式。
        # 只认此格式; 帧外一切散字节(STM32 debug 串 state:.../线路噪声)全部丢弃,
        # 根治 debug 串中 's'(→active)/'c'(→collect) 被误解析为命令的污染 bug。
        buf = self._rx_buf
        pos = 0
        while True:
            hash_idx = buf.find(b'#', pos)
            if hash_idx < 0:
                # 无更多命令起始符: 丢弃全部已扫描数据
                pos = len(buf)
                break
            newline = buf.find(b'\n', hash_idx)
            if newline < 0:
                # '#' 已到但命令未收全: 保留从 '#' 起, 等下一轮补齐
                pos = hash_idx
                break
            # 完整命令帧 #<body>\n: 逐字符提取有效命令
            for i in range(hash_idx + 1, newline):
                c = self._parse_cmd_byte(chr(buf[i]))
                if c:
                    commands.append(c)
            pos = newline + 1

        self._rx_buf = buf[pos:]
        return commands

    # ------------------------------------------------------------------
    # 回声状态查询
    # ------------------------------------------------------------------
    @property
    def echo_confirmed(self):
        """最近一次发送是否已收到回声确认"""
        if not self._echo_enabled or self.echo_ts <= 0:
            return False
        return self.echo_ts >= self._last_tx_ts

    @property
    def echo_age(self):
        """距离最近一次回声的时间(秒)"""
        if self.echo_ts <= 0:
            return float('inf')
        return time.time() - self.echo_ts

    @property
    def echo_healthy(self):
        """回声通道是否健康 (超时判定)"""
        if not self._echo_enabled:
            return True  # 未启用则不告警
        if self._echo_count == 0:
            return True  # 还没收到过回声, 不判定
        return self.echo_age < self._echo_timeout

    @property
    def echo_enabled(self):
        """回声功能是否启用"""
        return self._echo_enabled

    @property
    def echo_count(self):
        """累计收到的回声帧数"""
        return self._echo_count

    @property
    def echo_latency_ms(self):
        """最近一次回声往返延迟(ms)"""
        return self._echo_latency

    @property
    def tx_errors(self):
        """当前连续发送错误计数"""
        return self._tx_errors

    @property
    def last_sent_type(self):
        """最近一次实际写入串口的目标类型 (节流跳过时保持上次值)。
        供主循环日志显示真实发送 type, 避免误读 friends/enemies 计数。"""
        return self._last_sent_type

    def force_send_now(self):
        """强制下一次 send_target 立即发送 (跳过频率限制)。
        同时重置 _last_tx_ts 防止 echo 健康判定误报。
        """
        self._last_send = 0.0
        self._last_tx_ts = 0.0

    # ------------------------------------------------------------------
    # 发送目标
    # ------------------------------------------------------------------
    def send_target(self, target_type: str, cx=0, cy=0, area=0, direction=0.0,
                    confidence=None, target_id=None):
        """
        发送一帧目标数据给 STM32, 含频率限制、方向平滑和自动重连。

        协议 v2 (config.PROTOCOL_V2 = True 启用):
          多发 ts,conf,tid 三个字段; STM32 v1/v2 都能消化 (v1 忽略多余字段)。

        confidence: 0~100, None=用默认 50 (HSV 检测无可信度)
        target_id:  Tracker 提供的稳定 ID, None=0 (未跟踪)
        """
        if not self.active:
            p_ts = self._p_received_ts
            if p_ts > 0 and (time.time() - p_ts) > 3.0:
                print('[UART] Auto-recover active after 3s p-timeout', flush=True)
                self.active = True
                self._p_received_ts = 0
            else:
                return

        now = time.time()
        type_changed = (target_type != self._last_sent_type)

        if not type_changed:
            if target_type == 'X':
                interval = self._no_target_interval
            else:
                dir_delta = abs(direction - self._last_dir)
                interval = self._fast_interval if dir_delta > 0.05 else self._send_interval
            if now - self._last_send < interval:
                return

        # 串口不可用 或 设备消失 → 重连
        if not self.ser or not self._check_device_alive():
            self._try_reconnect()
            return

        self._tx_total += 1
        try:
            # 协议简化 (2026-05-28): STM32 实战只消费 type 字段,
            # cx/cy/area/dir 在下位机已无消费者 → 帧体只发 type ($<type>*CS\n)。
            # cx/cy/area/direction 入参保留, 仅供本层自适应发送频率使用, 不再编码进帧。
            body = target_type
            cs = self._checksum(body)
            msg = f'${body}*{cs}\n'
            self.ser.write(msg.encode())
            self._last_send = now
            self._last_tx_ts = now
            self._last_dir = direction
            self._last_sent_type = target_type
            self._tx_errors = 0
            self._tx_timeouts = 0
            self._tx_success += 1
        except _serial_mod.SerialTimeoutException:
            self._tx_timeouts += 1
            self._tx_timeouts_window += 1
            if self._tx_timeouts % 50 == 1:
                print(f'[UART] Write timeout #{self._tx_timeouts} '
                      f'(USB busy, skipping)', flush=True)
        except OSError as e:
            self._tx_errors += 1
            err_no = getattr(e, 'errno', 0)
            device_err_set = (errno.EIO, errno.ENXIO, errno.ENODEV)
            if err_no in device_err_set:
                print(f'[UART] Device error (errno={err_no} '
                      f'{errno.errorcode.get(err_no, "?")}), reconnecting now')
                self._try_reconnect()
            elif self._tx_errors >= self._TX_ERROR_THRESHOLD:
                print(f'[UART] TX error #{self._tx_errors}: {e}, reconnecting')
                self._try_reconnect()
        except Exception as e:
            self._tx_errors += 1
            if self._tx_errors >= self._TX_ERROR_THRESHOLD:
                print(f'[UART] TX error #{self._tx_errors}: {e}, reconnecting')
                self._try_reconnect()

        # 定期打印发送统计
        if now - self._stats_timer >= self._STATS_INTERVAL:
            rate = (self._tx_success / self._tx_total * 100
                    if self._tx_total > 0 else 0)
            print(f'[UART] Stats: {self._tx_success}/{self._tx_total} '
                  f'({rate:.1f}%) timeouts={self._tx_timeouts_window} '
                  f'in {self._STATS_INTERVAL:.0f}s')
            self._tx_total = 0
            self._tx_success = 0
            self._tx_timeouts_window = 0
            self._stats_timer = now

    # ------------------------------------------------------------------
    # 高层接口
    # ------------------------------------------------------------------
    # NOTE (v2, D3): send_tag(self, tag, my_color) 已删除
    #   原因: 被 main.py:VisionSystem._send_tag_target() 完全取代
    #   保留的功能等价物在 main.py:803-809 (_send_tag_target)
    #   删除日期: 2026-05-21, Codex 评审 D3 采纳

    def send_own_detection(self, own_targets):
        """
        单色策略: 检测到己方能量块就发 F, 让 STM32 决定是否回避。
        own_targets: detect_own() 返回的目标列表
        远距离友方 (area < FRIEND_ALERT_AREA) 不发 F, 避免远距离误后退。
        """
        if own_targets:
            t = own_targets[0]
            min_area = getattr(config, 'FRIEND_ALERT_AREA', 5000)
            if t.area >= min_area:
                self.send_target('F', t.cx, t.cy, t.area, t.direction)
            else:
                self.send_target('X')
        else:
            self.send_target('X')

    def send_from_detection(self, friends, enemies, neutrals, bombs=None):
        """
        优先级: B(炸弹) > F(近距离友方) > E/N(按模式) > X
        近距离友方必须优先于敌方, 否则机器人会穿过友方块去攻击敌人。
        """
        if bombs:
            t = bombs[0]
            self.send_target('B', t.cx, t.cy, t.area, t.direction)
            return

        # 近距离友方最高优先 (area >= FRIEND_ALERT_AREA)
        if friends:
            t = friends[0]
            min_area = getattr(config, 'FRIEND_ALERT_AREA', 5000)
            if t.area >= min_area:
                self.send_target('F', t.cx, t.cy, t.area, t.direction)
                return

        mode = getattr(config, 'PRIORITY_MODE', 'collect')
        if mode == 'collect':
            first, first_type = neutrals, 'N'
            second, second_type = enemies, 'E'
        else:
            first, first_type = enemies, 'E'
            second, second_type = neutrals, 'N'

        if first:
            t = first[0]
            self.send_target(first_type, t.cx, t.cy, t.area, t.direction)
        elif second:
            t = second[0]
            self.send_target(second_type, t.cx, t.cy, t.area, t.direction)
        else:
            self.send_target('X')
