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
  's' = 开始识别      'p' = 暂停识别
  'c' = 收集模式      'f' = 战斗模式
"""
import time
import config


class UartComm:
    # 连续写错误超过此阈值则触发重连
    _TX_ERROR_THRESHOLD = 3
    # 重连冷却时间 (s)
    _RECONNECT_INTERVAL = 2.0
    # 颜色心跳周期 (s): 定期重发己方颜色，防止STM32丢失初始颜色
    _COLOR_HEARTBEAT_INTERVAL = 5.0

    def __init__(self):
        self.ser = None
        self.my_color = 'b'
        self.mode = 'fight'          # 'fight'=格斗(颜色检测) / 'collect'=收集(Tag检测)
        self.active = True           # 是否激活发送
        self._color_changed = False  # 颜色切换标志, 供主循环检测并切换WB
        self._last_send = 0.0
        self._send_interval = 0.050  # 最高 20Hz 发送频率
        self._no_target_interval = 0.2  # 无目标时降到 5Hz, 节省带宽
        self._fast_interval = 0.033  # 快速运动时 30Hz
        self._tx_errors = 0
        self._last_reconnect = 0.0
        self._last_color_send = 0.0
        # 发送统计
        self._tx_total = 0
        self._tx_success = 0
        self._stats_timer = 0.0
        self._STATS_INTERVAL = 60.0
        # 方向平滑: 滑动窗口
        self._dir_window = []
        self._dir_smooth_size = getattr(
            __import__('config'), 'DIRECTION_SMOOTH_WINDOW', 3)

    # ------------------------------------------------------------------
    # 开关
    # ------------------------------------------------------------------
    def open(self):
        """打开串口, 启用 low_latency 模式, 失败时静默返回 False"""
        try:
            import serial
            # 重新探测设备 (USB设备号可能变化)
            port = config._find_uart()
            self.ser = serial.Serial(
                port=port,
                baudrate=config.UART_BAUD,
                timeout=config.UART_TIMEOUT,
                write_timeout=0.05,
            )
            # 启用 low_latency 模式 (减少内核缓冲延迟)
            try:
                import subprocess
                subprocess.run(['stty', '-F', port, 'low_latency'],
                               capture_output=True, timeout=2)
            except Exception:
                pass
            # 清空残留数据
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            self._tx_errors = 0
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
        """限速重连: 冷却期内不重复尝试, 重连时重新探测设备号"""
        now = time.time()
        if now - self._last_reconnect < self._RECONNECT_INTERVAL:
            return
        self._last_reconnect = now
        print('[UART] Reconnecting (re-detecting device)...')
        self.close()
        if self.open():
            print('[UART] Reconnected OK')
        else:
            print('[UART] Reconnect failed, will retry')

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
    # 读指令
    # ------------------------------------------------------------------
    def read_command(self):
        """非阻塞读取 STM32 单字节指令, 返回指令字符或 None"""
        if not self.ser:
            return None
        try:
            if self.ser.in_waiting <= 0:
                return None
            data = self.ser.read(self.ser.in_waiting)
            # 取最后一条有效指令（覆盖式: 最新的生效）
            for byte in reversed(data):
                ch = chr(byte)
                if ch in ('b', 'y'):
                    old = self.my_color
                    self.my_color = ch
                    if old != ch:
                        self._color_changed = True
                    return ch
                if ch == 's':
                    self.active = True
                    return ch
                if ch == 'p':
                    self.active = False
                    return ch
                if ch == 'c':
                    self.mode = 'collect'
                    return ch
                if ch == 'f':
                    self.mode = 'fight'
                    return ch
        except Exception:
            pass
        return None

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

        # 串口不可用则尝试重连
        if not self.ser:
            self._try_reconnect()
            return

        self._tx_total += 1
        try:
            dir_int = max(-100, min(100, int(direction * 100)))
            if target_type == 'X':
                body = 'X,0,0,0,0'
            else:
                body = f'{target_type},{cx},{cy},{int(area)},{dir_int:+d}'
            cs = self._checksum(body)
            msg = f'${body}*{cs}\n'
            self.ser.write(msg.encode())
            self._last_send = now
            self._tx_errors = 0
            self._tx_success += 1
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
    # 颜色心跳 (已废弃)
    # ------------------------------------------------------------------
    def send_color_heartbeat(self):
        """
        [已废弃] 原设计定期向 STM32 重发己方颜色字符。
        但实际协议中颜色由 STM32 通过 Vision_SendColor() 下发给上位机，
        STM32 的 vision_parser 仅解析 $...*CS 格式帧，单字节颜色会被忽略。
        保留方法签名以兼容调用方，但不再执行任何操作。
        """
        pass

    # ------------------------------------------------------------------
    # 高层接口
    # ------------------------------------------------------------------
    def send_tag(self, tag):
        """
        发送 Tag 检测结果给 STM32。
        tag: {'id': int|str, 'cx': int, 'cy': int, 'area': int}
        协议: $T,tag_id,cx,cy,area,dir*CS\n
        """
        if not self.active:
            return
        now = time.time()
        if now - self._last_send < self._send_interval:
            return
        if not self.ser:
            self._try_reconnect()
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

        self._tx_total += 1
        try:
            dir_int = max(-100, min(100, int(direction * 100)))
            body = f'T,{tag_id},{cx},{cy},{int(area)},{dir_int:+d}'
            cs = self._checksum(body)
            msg = f'${body}*{cs}\n'
            self.ser.write(msg.encode())
            self._last_send = now
            self._tx_errors = 0
            self._tx_success += 1
        except Exception as e:
            self._tx_errors += 1
            if self._tx_errors >= self._TX_ERROR_THRESHOLD:
                print(f'[UART] TX error #{self._tx_errors}: {e}, reconnecting')
                self._try_reconnect()

    def send_own_detection(self, own_targets):
        """
        单色策略: 仅当己方能量块极近时发 F(后退), 远距离不回避。
        own_targets: detect_own() 返回的目标列表
        """
        if own_targets:
            t = own_targets[0]
            alert_area = getattr(config, 'FRIEND_ALERT_AREA', 15000)
            if t.area >= alert_area:
                self.send_target('F', t.cx, t.cy, t.area, t.direction)
            else:
                self.send_target('X')
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
            # 双色模式: 仅当友方极近时才发 F, 远距离发 X 让 STM32 依赖红外
            alert_area = getattr(config, 'FRIEND_ALERT_AREA', 15000)
            t = friends[0]
            if t.area >= alert_area:
                self.send_target('F', t.cx, t.cy, t.area, t.direction)
            else:
                self.send_target('X')
        else:
            self.send_target('X')
