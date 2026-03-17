"""
UART 通信模块 v2 - 带校验和 + 双向命令 + 自动重连
协议:
  发送(to STM32):  $type,cx,cy,area,dir*CS\n
    type: E=敌方 N=中立 F=友方 X=无目标
    cx,cy: 目标中心坐标
    area: 面积
    dir: 方向偏移 [-100, +100] 负=左 正=右
    CS: 异或校验(十六进制)
    例: $E,320,240,5000,+25*4A\n

  接收(from STM32): 单字符指令
    'b' = 己方蓝色
    'y' = 己方黄色
    's' = 开始识别
    'p' = 暂停识别
"""
import time
import config


class UartComm:
    def __init__(self):
        self.ser = None
        self.my_color = 'b'
        self.active = True      # 是否激活发送
        self._last_send = 0
        self._send_interval = 0.033  # 最高30Hz发送频率

    def open(self):
        try:
            import serial
            self.ser = serial.Serial(
                port=config.UART_PORT,
                baudrate=config.UART_BAUD,
                timeout=config.UART_TIMEOUT,
                write_timeout=0.01,
            )
            print(f'UART opened: {config.UART_PORT} @ {config.UART_BAUD}')
            return True
        except Exception as e:
            print(f'UART open failed: {e}')
            self.ser = None
            return False

    def _checksum(self, data):
        """异或校验"""
        cs = 0
        for b in data.encode():
            cs ^= b
        return f'{cs:02X}'

    def read_command(self):
        """非阻塞读取 STM32 指令"""
        if not self.ser:
            return None
        try:
            if self.ser.in_waiting > 0:
                data = self.ser.read(self.ser.in_waiting)
                for byte in reversed(data):
                    ch = chr(byte)
                    if ch in ('b', 'y'):
                        self.my_color = ch
                        return ch
                    elif ch == 's':
                        self.active = True
                        return ch
                    elif ch == 'p':
                        self.active = False
                        return ch
        except Exception:
            pass
        return None

    def send_target(self, target_type, cx=0, cy=0, area=0, direction=0):
        """发送目标信息, 带校验和和频率限制"""
        if not self.ser or not self.active:
            return

        now = time.time()
        if now - self._last_send < self._send_interval:
            return
        self._last_send = now

        try:
            dir_int = max(-100, min(100, int(direction * 100)))
            if target_type == 'X':
                body = 'X,0,0,0,0'
            else:
                body = f'{target_type},{cx},{cy},{int(area)},{dir_int:+d}'
            cs = self._checksum(body)
            msg = f'${body}*{cs}\n'
            self.ser.write(msg.encode())
        except Exception:
            pass

    def send_from_detection(self, friends, enemies, neutrals):
        """根据检测结果发送最优先目标"""
        if enemies:
            t = enemies[0]
            self.send_target('E', t.cx, t.cy, t.area, t.direction)
        elif neutrals:
            t = neutrals[0]
            self.send_target('N', t.cx, t.cy, t.area, t.direction)
        else:
            self.send_target('X')

    def close(self):
        if self.ser:
            self.ser.close()
            self.ser = None
