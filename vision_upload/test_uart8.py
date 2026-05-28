#!/usr/bin/env python3
import serial, time

port = "/dev/ttyAS8"
print("=== S-UART1 Test (%s, PM3-RX PM4-TX) ===" % port)

s = serial.Serial(port, 115200, timeout=0.1)
s.reset_input_buffer()
s.write(b"b")
print("Sent 'b' to STM32")
print("Reading 8 seconds (longer wait)...")

total = b""
for i in range(160):
    n = s.in_waiting
    if n > 0:
        data = s.read(n)
        total += data
        print("  [%.1fs] got %d bytes: %r" % (i*0.05, len(data), data[:40]))
    time.sleep(0.05)

if total:
    print("SUCCESS: %d bytes received" % len(total))
else:
    print("ZERO bytes in 8s")
    print("")
    print("Diagnostics:")
    print("  Port open: OK")
    print("  TX send: OK (sent 'b')")
    print("  RX receive: FAIL")
    print("")
    print("Please verify:")
    print("  1. STM32 power is ON and firmware running")
    print("  2. STM32 TX (PD5) wire -> Radxa Pin33 (PM3)")
    print("  3. STM32 RX (PD6) wire -> Radxa Pin37 (PM4)")
    print("  4. GND wire connected")
    print("  5. Try SWAPPING the two signal wires")
s.close()
