#!/usr/bin/env python3
"""
UART通信模块 - 与STM32下位机的串口通信
协议格式: $type,cx,cy,area,dir*CS\n
"""

import time
import threading
import logging

logger = logging.getLogger(__name__)

try:
    import serial
except ImportError:
    logger.warning("pyserial not installed, UART will be disabled")
    serial = None


def xor_checksum(data: str) -> int:
    cs = 0
    for ch in data:
        cs ^= ord(ch)
    return cs


class UARTComm:
    """STM32 UART通信管理器 (线程安全)"""

    def __init__(self, port='/dev/ttyS4', baudrate=115200, timeout=0.01):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._ser = None
        self._lock = threading.RLock()
        self._team_color = 'b'
        self._color_callback = None
        self._last_send_time = 0.0
        self._last_target_type = None
        self._connect()

    def _connect(self):
        with self._lock:
            self._connect_locked()

    def _connect_locked(self):
        if serial is None:
            return
        try:
            self._ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.timeout
            )
            logger.info("UART connected: %s @ %d", self.port, self.baudrate)
        except serial.SerialException as e:
            logger.error("UART connect failed: %s", e)
            self._ser = None

    def _reconnect_locked(self):
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
        self._ser = None
        time.sleep(0.5)
        self._connect_locked()

    @property
    def is_connected(self) -> bool:
        return self._ser is not None and self._ser.is_open

    @property
    def team_color(self) -> str:
        return self._team_color

    def set_team_color(self, color: str):
        self._team_color = color

    def set_color_callback(self, callback):
        self._color_callback = callback

    def build_frame(self, type_ch: str, cx: int, cy: int, area: int, dir_val: int) -> str:
        type_ch = str(type_ch).upper()[:1]
        dir_val = max(-100, min(100, int(dir_val)))
        area = max(0, int(area))
        body = f"{type_ch},{int(cx)},{int(cy)},{area},{dir_val}"
        cs = xor_checksum(body)
        return f"${body}*{cs:02X}\n"

    def send_target(self, type_ch: str, cx: int, cy: int, area: int, dir_val: int):
        frame = self.build_frame(type_ch, cx, cy, area, dir_val)
        self._send_raw(frame)

    def send_no_target(self):
        self.send_target('X', 0, 0, 0, 0)

    def send_target_throttled(self, type_ch, cx, cy, area, dir_val,
                               min_interval_ms=50):
        """带频率控制的发送: 类型变化立即发送, 否则限制最小间隔"""
        now = time.time()
        elapsed_ms = (now - self._last_send_time) * 1000

        # 目标类型变化 -> 立即发送
        if type_ch != self._last_target_type:
            self.send_target(type_ch, cx, cy, area, dir_val)
            self._last_send_time = now
            self._last_target_type = type_ch
            return

        # 未达到最小间隔 -> 跳过
        if elapsed_ms < min_interval_ms:
            return

        self.send_target(type_ch, cx, cy, area, dir_val)
        self._last_send_time = now

    def _send_raw(self, data: str):
        if serial is None:
            return
        payload = data.encode('ascii')
        with self._lock:
            if not self.is_connected:
                self._reconnect_locked()
                if not self.is_connected:
                    return
            try:
                self._ser.write(payload)
            except serial.SerialException as e:
                logger.warning("UART send error: %s, reconnecting...", e)
                self._reconnect_locked()

    def check_color_command(self):
        if serial is None:
            return
        updated = None
        callback = None
        with self._lock:
            if not self.is_connected:
                return
            try:
                waiting = self._ser.in_waiting
                if waiting <= 0:
                    return
                data = self._ser.read(waiting)
                for byte in data:
                    ch = chr(byte).lower()
                    if ch in ('b', 'y'):
                        self._team_color = ch
                        updated = ch
                        callback = self._color_callback
            except serial.SerialException as e:
                logger.warning("UART read error: %s, reconnecting...", e)
                self._reconnect_locked()
                return
        if updated is not None:
            logger.info("Team color updated: %s", updated)
            if callback:
                callback(updated)

    def close(self):
        with self._lock:
            if self._ser is not None:
                try:
                    self._ser.close()
                except Exception:
                    pass
                self._ser = None
                logger.info("UART closed")
