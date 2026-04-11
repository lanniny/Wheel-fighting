"""
UART 通信模块 v3 - 自动重连 + 颜色心跳 + 炸弹类型支持

协议 (LubanCat -> STM32):  $type,cx,cy,area,dir*CS\n
  type: E=敌方  N=中立  F=友方  X=无目标  B=炸弹
  cx,cy: 目标中心像素坐标 [0-640 / 0-480]
  area:  目标面积 (像素)
  dir:   方向偏移整数 [-100,+100]  负=左 正=右
  CS:    body 字段的逐字节异或校验和 (十六进制 2位)
  例:   $E,320,240,5000,+25*4A\n


协议 (STM32 -> LubanCat): 单字节指令
  'b' = 己方蓝色      'y' = 己方黄色
  's' = 开始识别      'p' = 暂停识别 (预留, STM32未实现)
  'c' = 收集模式      'f' = 战斗模式 (预留, STM32未实现)
  'D' = 掉台回复模式   (视觉切换到黑色检测, 发送 G/X)
  'S' = 恢复正常检测   (掉台回复完成, 切回颜色检测)
  'N' = STM32正常重启  (视觉重置为正常识别状态)
"""
import os
import time
import config


class UartComm:
    # 连续写错误超过此阈值则触发重连
    _TX_ERROR_THRESHOLD = 3
    # 重连冷却: 指数退避 (2→4→8→8s)
    _RECONNECT_BASE = 2.0
    _RECONNECT_MAX = 8.0
    # 颜色心跳周期 (s): 定期重发己方颜色，防止STM32丢失初始颜色
    _COLOR_HEARTBEAT_INTERVAL = 5.0
    # 设备存在性检查间隔 (s): 比发送频率低, 减少 stat() 系统调用
    _DEVICE_CHECK_INTERVAL = 3.0

    def __init__(self):
        self.ser = None
        self.my_color = 'b'
        self.mode = 'fight'          # 'fight'=格斗(颜色检测) / 'collect'=收集(Tag检测)
        self.active = True           # 是否激活发送
        self.drop_recovery = False   # 掉台回复模式 (黑色检测)
        self._color_changed = False  # 颜色切换标志, 供主循环检测并切换WB
        self._last_send = 0.0
        self._send_interval = 0.050  # 最高 20Hz 发送频率
        self._no_target_interval = 0.2  # 无目标时降到 5Hz, 节省带宽
        self._fast_interval = 0.033  # 快速运动时 30Hz
        self._tx_errors = 0
        self._rx_errors = 0          # 读取错误计数
        self._last_reconnect = 0.0
        self._reconnect_backoff = self._RECONNECT_BASE
        self._last_color_send = 0.0
        self._last_device_check = 0.0
        self._port_path = None       # 当前使用的设备路径
        # 发送统计
        self._tx_total = 0
        self._tx_success = 0
        self._stats_timer = 0.0
        self._STATS_INTERVAL = 60.0
        # 方向平滑: 滑动窗口
        self._dir_window = []
        self._dir_smooth_size = getattr(
            __import__('config'), 'DIRECTION_SMOOTH_WINDOW', 3)
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
                write_timeout=0.05,
            )
            # 启用 low_latency 模式 (减少内核缓冲延迟)
            # 解析真实设备路径 (符号链接 → 实际设备)
            real_port = os.path.realpath(port) if os.path.islink(port) else port
            try:
                import subprocess
                subprocess.run(['stty', '-F', real_port, 'low_latency'],
                               capture_output=True, timeout=2)
            except Exception:
                pass
            # 清空残留数据 (USB 断联重连后可能有脏数据)
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            self._tx_errors = 0
            self._rx_errors = 0
            self._reconnect_backoff = self._RECONNECT_BASE  # 重连成功, 重置退避
            self._last_device_check = time.time()
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
        print(f'[UART] Reconnecting (backoff={self._reconnect_backoff:.0f}s)...')
        self.close()
        if self.open():
            print('[UART] Reconnected OK')
        else:
            # 指数退避: 2→4→8→8s
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
            return 's'
        if ch == 'p':
            self.active = False
            return ch
        if ch == 'D':
            self.drop_recovery = True
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
    # 读指令 (v2: 分离回声帧 + 单字节命令)
    # ------------------------------------------------------------------
    def read_command(self):
        """非阻塞读取 STM32 数据, 分离回声帧和单字节命令。

        RX 缓冲区中混合了:
          - 单字节命令: 'b','y','s','p','c','f','D'
          - 回声帧: $type,cx,cy,area,dir*CS\\n (STM32原样回传)

        策略: 用 '$' 和 '\\n' 定界回声帧, 帧外散字节扫描命令。
        返回最后一条有效命令字符, 或 None。
        """
        if not self.ser:
            self._try_reconnect()
            return None

        if not self._check_device_alive():
            self._try_reconnect()
            return None

        try:
            if self.ser.in_waiting <= 0:
                # 即使没新数据, 也处理残余缓冲
                if not self._rx_buf:
                    return None
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
            return None
        except Exception:
            return None

        # 防止缓冲区溢出 (丢弃旧数据)
        if len(self._rx_buf) > 1024:
            self._rx_buf = self._rx_buf[-512:]

        cmd = None
        new_buf = b''
        buf = self._rx_buf
        pos = 0

        while pos < len(buf):
            dollar = buf.find(b'$', pos)

            if dollar < 0:
                # 无帧头: 剩余全是散字节, 扫描命令
                for i in range(pos, len(buf)):
                    c = self._parse_cmd_byte(chr(buf[i]))
                    if c:
                        cmd = c
                break

            # '$' 前的散字节: 扫描命令
            for i in range(pos, dollar):
                c = self._parse_cmd_byte(chr(buf[i]))
                if c:
                    cmd = c

            # 从 '$' 开始找 '\n' (帧结尾)
            newline = buf.find(b'\n', dollar)
            if newline < 0:
                # 帧不完整, 保留到下次
                new_buf = buf[dollar:]
                break

            # 提取完整帧并解析回声
            frame = buf[dollar:newline + 1]
            if self._echo_enabled:
                self._parse_echo_frame(frame)

            pos = newline + 1

        self._rx_buf = new_buf
        return cmd

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

    # ------------------------------------------------------------------
    # 发送目标
    # ------------------------------------------------------------------
    def send_target(self, target_type: str, cx=0, cy=0, area=0, direction=0.0):
        """
        发送一帧目标数据给 STM32, 含频率限制、方向平滑和自动重连。
        """
        if not self.active:
            return

        # 方向平滑: 滑动窗口平均
        if target_type != 'X':
            self._dir_window.append(direction)
            if len(self._dir_window) > self._dir_smooth_size:
                self._dir_window = self._dir_window[-self._dir_smooth_size:]
            direction = sum(self._dir_window) / len(self._dir_window)
        else:
            self._dir_window.clear()

        # 自适应发送频率: 目标运动快→30Hz, 慢→20Hz, 无目标→5Hz
        now = time.time()
        if target_type == 'X':
            interval = self._no_target_interval
        elif len(self._dir_window) >= 2:
            dir_delta = abs(self._dir_window[-1] - self._dir_window[-2])
            interval = self._fast_interval if dir_delta > 0.05 else self._send_interval
        else:
            interval = self._send_interval

        if now - self._last_send < interval:
            return

        # 串口不可用 或 设备消失 → 重连
        if not self.ser or not self._check_device_alive():
            self._try_reconnect()
            return

        self._tx_total += 1
        try:
            dir_int = max(-100, min(100, round(direction * 100)))
            if target_type == 'X':
                body = 'X,0,0,0,0'
            else:
                body = f'{target_type},{cx},{cy},{int(area)},{dir_int:+d}'
            cs = self._checksum(body)
            msg = f'${body}*{cs}\n'
            self.ser.write(msg.encode())
            self._last_send = now
            self._last_tx_ts = now
            self._tx_errors = 0
            self._tx_success += 1
        except OSError as e:
            # USB 断开: errno 5/6/19, 立即重连
            self._tx_errors += 1
            err_no = getattr(e, 'errno', 0)
            if err_no in (5, 6, 19):  # EIO, ENXIO, ENODEV
                print(f'[UART] Device error (errno={err_no}), reconnecting now')
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
                  f'({rate:.1f}%) in {self._STATS_INTERVAL:.0f}s')
            self._tx_total = 0
            self._tx_success = 0
            self._stats_timer = now

    # ------------------------------------------------------------------
    # 高层接口
    # ------------------------------------------------------------------
    def send_tag(self, tag, my_color='b'):
        """
        发送 Tag 检测结果给 STM32 (标准5字段帧)。
        tag: {'id': int|str, 'cx': int, 'cy': int, 'area': int}

        旧方案 $T,tag_id,cx,cy,area,dir*CS\\n 有6字段,
        STM32 sscanf 只解析5字段 → 帧被丢弃 (CRITICAL BUG)。
        修复: 将 tag_id 翻译为标准类型 E/N/F, 用 send_target() 发送。
        """
        if not self.active:
            return

        cx = tag.get('cx', 0)
        cy = tag.get('cy', 0)
        area = tag.get('area', 0)
        direction = (cx - config.CAMERA_WIDTH / 2) / (config.CAMERA_WIDTH / 2)
        tag_id_raw = tag.get('id', 0)
        try:
            tag_id = int(tag_id_raw)
        except (TypeError, ValueError):
            print(f'[UART] Drop invalid tag id: {tag_id_raw!r}')
            return

        # Tag ID → 标准类型: 兼容 STM32 的 5字段解析器
        from detector import TagDetector
        t_type = TagDetector.classify_tag(tag_id, my_color)
        self.send_target(t_type, cx, cy, area, direction)

    def send_own_detection(self, own_targets):
        """
        单色策略: 检测到己方能量块就发 F, 让 STM32 决定是否回避。
        own_targets: detect_own() 返回的目标列表
        """
        if own_targets:
            t = own_targets[0]
            self.send_target('F', t.cx, t.cy, t.area, t.direction)
        else:
            self.send_target('X')

    def send_from_detection(self, friends, enemies, neutrals, bombs=None):
        """
        根据优先级发送最重要目标, 尊重 config.PRIORITY_MODE:
          炸弹(B) 始终最优先
          collect 模式: N(中立) > E(敌方)
          attack  模式: E(敌方) > N(中立)
          无目标 → X

        bombs: 炸弹目标列表 (可选, 当前为保留接口)
        """
        if bombs:
            t = bombs[0]
            self.send_target('B', t.cx, t.cy, t.area, t.direction)
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
        elif friends:
            # 双色模式: 友方一律发 F, 让 STM32 根据 area 决定回避力度
            t = friends[0]
            self.send_target('F', t.cx, t.cy, t.area, t.direction)
        else:
            self.send_target('X')
