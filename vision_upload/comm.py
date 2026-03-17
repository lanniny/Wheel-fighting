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
        self.active = True           # 是否激活发送
        self._last_send = 0.0
        self._send_interval = 0.033  # 最高 30Hz 发送频率
        self._tx_errors = 0
        self._last_reconnect = 0.0
        self._last_color_send = 0.0

    # ------------------------------------------------------------------
    # 开关
    # ------------------------------------------------------------------
    def open(self):
        """打开串口, 失败时静默返回 False"""
        try:
            import serial
            self.ser = serial.Serial(
                port=config.UART_PORT,
                baudrate=config.UART_BAUD,
                timeout=config.UART_TIMEOUT,
                write_timeout=0.01,
            )
            self._tx_errors = 0
            print(f'UART opened: {config.UART_PORT} @ {config.UART_BAUD}')
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
        """限速重连: 冷却期内不重复尝试"""
        now = time.time()
        if now - self._last_reconnect < self._RECONNECT_INTERVAL:
            return
        self._last_reconnect = now
        print('[UART] Reconnecting...')
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
                    self.my_color = ch
                    return ch
                if ch == 's':
                    self.active = True
                    return ch
                if ch == 'p':
                    self.active = False
                    return ch
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # 发送目标
    # ------------------------------------------------------------------
    def send_target(self, target_type: str, cx=0, cy=0, area=0, direction=0.0):
        """
        发送一帧目标数据给 STM32, 含频率限制和自动重连。

        参数:
            target_type : 'E' / 'N' / 'F' / 'X' / 'B'
            cx, cy      : 目标中心像素坐标
            area        : 目标面积 (像素)
            direction   : 归一化方向 [-1.0, +1.0], 内部转换为 [-100,+100] 整数
        """
        if not self.active:
            return

        # 频率限制
        now = time.time()
        if now - self._last_send < self._send_interval:
            return

        # 串口不可用则尝试重连
        if not self.ser:
            self._try_reconnect()
            return

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
            self._tx_errors = 0          # 成功则清零错误计数
        except Exception as e:
            self._tx_errors += 1
            if self._tx_errors >= self._TX_ERROR_THRESHOLD:
                print(f'[UART] TX error #{self._tx_errors}: {e}, reconnecting')
                self._try_reconnect()

    # ------------------------------------------------------------------
    # 颜色心跳
    # ------------------------------------------------------------------
    def send_color_heartbeat(self):
        """
        定期向 STM32 重发己方颜色字符。
        防止 STM32 上电序列中未接收到初始颜色指令。
        """
        if not self.ser:
            return
        now = time.time()
        if now - self._last_color_send < self._COLOR_HEARTBEAT_INTERVAL:
            return
        self._last_color_send = now
        try:
            self.ser.write(self.my_color.encode())
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 高层接口
    # ------------------------------------------------------------------
    def send_from_detection(self, friends, enemies, neutrals, bombs=None):
        """
        根据优先级发送最重要目标:
          炸弹(B) > 敌方(E) > 中立(N) > 无目标(X)

        bombs: 炸弹目标列表 (可选, 当前为保留接口)
        """
        if bombs:
            t = bombs[0]
            self.send_target('B', t.cx, t.cy, t.area, t.direction)
        elif enemies:
            t = enemies[0]
            self.send_target('E', t.cx, t.cy, t.area, t.direction)
        elif neutrals:
            t = neutrals[0]
            self.send_target('N', t.cx, t.cy, t.area, t.direction)
        else:
            self.send_target('X')
