#!/usr/bin/env python3
"""临时串口 RX 监听: 独占打开串口, 把收到的原始字节实时打印 (HEX + ASCII)。
用法: python3 uart_rx_monitor.py [秒数] [串口]
默认监听 15 秒, 串口优先 /dev/ttySTM32。
"""
import sys
import time
import glob

try:
    import serial
except ImportError:
    print("ERROR: pyserial 未安装")
    sys.exit(1)


def find_port():
    for p in ("/dev/ttySTM32", "/dev/ttyUSB0", "/dev/ttyUSB1"):
        import os
        if os.path.exists(p):
            return p
    cands = glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")
    return cands[0] if cands else None


def main():
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0
    port = sys.argv[2] if len(sys.argv) > 2 else find_port()
    if not port:
        print("ERROR: 找不到串口设备")
        sys.exit(1)

    try:
        ser = serial.Serial(port, 115200, timeout=0.1)
    except Exception as e:
        print(f"ERROR: 打开 {port} 失败: {e}")
        sys.exit(1)

    print(f"[LISTEN] {port} @115200, {duration:.0f}s ...")
    end = time.time() + duration
    total = 0
    while time.time() < end:
        data = ser.read(64)
        if data:
            total += len(data)
            ts = time.strftime("%H:%M:%S")
            hexs = " ".join("%02X" % b for b in data)
            asc = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
            print(f"{ts} RX[{len(data):2d}] HEX: {hexs} | ASCII: {asc}", flush=True)
    ser.close()
    print(f"[DONE] 共收到 {total} 字节" + (" (无数据)" if total == 0 else ""))


if __name__ == "__main__":
    main()
