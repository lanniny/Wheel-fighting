#!/usr/bin/env python3
"""UART diagnostic - test available serial ports"""
import serial
import time
import sys
import glob

# Auto-detect available ports
ports = sorted(glob.glob("/dev/ttyUSB*") + ["/dev/ttyAS1"])
print("Available ports: " + str(ports))

for port in ports:
    print("\n=== Testing " + port + " ===")
    try:
        s = serial.Serial(port, 115200, timeout=0.5, write_timeout=0.5)
        s.reset_input_buffer()

        # Compute correct checksum
        def checksum(data):
            cs = 0
            for b in data.encode():
                cs ^= b
            return "%02X" % cs

        body = "X,0,0,0,0"
        cs = checksum(body)
        frame = ("$" + body + "*" + cs + "\n").encode()

        print("  TX: " + frame.decode().strip())
        for i in range(20):
            s.write(frame)
            time.sleep(0.1)
            avail = s.in_waiting
            if avail > 0:
                data = s.read(avail)
                print("  [%d] RX %d bytes: %r" % (i, len(data), data))

        # Final listen
        print("  Listening 3s more...")
        t0 = time.time()
        while time.time() - t0 < 3:
            avail = s.in_waiting
            if avail > 0:
                data = s.read(avail)
                t = time.time() - t0
                print("  [%.1fs] RX: %r" % (t, data))
            time.sleep(0.1)

        s.close()
    except Exception as e:
        print("  ERROR: " + str(e))

print("\nDone")
