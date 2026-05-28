#!/usr/bin/env python3
"""闭环验证: 扮演视觉系统持续发 $type 帧, 同时读 STM32 回传的 state 帧。
观察 STM32 的 rx/ok 是否实时增长、vis 是否随发送类型变化。
用法: python3 uart_f_probe.py [秒数] [类型F/E/N/B] [串口]
"""
import sys
import time
import glob
import os

try:
    import serial
except ImportError:
    print("ERROR: pyserial 未安装")
    sys.exit(1)


def find_port():
    for p in ("/dev/ttySTM32", "/dev/ttyUSB0", "/dev/ttyUSB1"):
        if os.path.exists(p):
            return p
    c = glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")
    return c[0] if c else None


def checksum(body):
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    return cs


def build(t="F", cx=320, cy=240, area=5000, d=0):
    sign = "+" if d >= 0 else "-"
    body = f"{t},{cx},{cy},{area},{sign}{abs(d)}"
    return f"${body}*{checksum(body):02X}\n"


def main():
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
    typ = sys.argv[2] if len(sys.argv) > 2 else "F"
    port = sys.argv[3] if len(sys.argv) > 3 else find_port()
    if not port:
        print("ERROR: 找不到串口设备")
        sys.exit(1)

    try:
        ser = serial.Serial(port, 115200, timeout=0.05)
    except Exception as e:
        print(f"ERROR: 打开 {port} 失败: {e}")
        sys.exit(1)

    frame = build(typ).encode()
    print(f"[PROBE] {port} 每50ms发送 {frame!r} 持续{dur:.0f}s, 同时读STM32回传")
    end = time.time() + dur
    last_tx = 0.0
    tx_count = 0
    rxbuf = b""
    while time.time() < end:
        now = time.time()
        if now - last_tx >= 0.05:
            ser.write(frame)
            tx_count += 1
            last_tx = now
        data = ser.read(128)
        if data:
            rxbuf += data
            while b"\n" in rxbuf:
                line, rxbuf = rxbuf.split(b"\n", 1)
                s = line.decode("ascii", "replace").strip()
                if s:
                    print(time.strftime("%H:%M:%S"), "STM32>", s, flush=True)
    ser.close()
    print(f"[DONE] 共发送 {tx_count} 帧 ${typ}")


if __name__ == "__main__":
    main()
