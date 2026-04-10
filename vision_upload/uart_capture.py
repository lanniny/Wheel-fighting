#!/usr/bin/env python3
"""Capture raw UART frames for protocol analysis"""
import serial, time, sys

s = serial.Serial("/dev/ttyUSB0", 115200, timeout=0.5)
s.reset_input_buffer()
buf = b""
t0 = time.time()
while time.time() - t0 < 5:
    if s.in_waiting > 0:
        buf += s.read(s.in_waiting)
    time.sleep(0.02)
s.close()

text = buf.decode("ascii", errors="replace")
lines = text.replace("\r", "").split("\n")
for i, line in enumerate(lines[:40]):
    if line.strip():
        print(f"[{i:3d}] {line.strip()}")
print(f"\nTotal bytes: {len(buf)}, Lines: {len(lines)}")
