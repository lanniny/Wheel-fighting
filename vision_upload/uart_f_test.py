#!/usr/bin/env python3
"""UART F-frame continuous sender — debug tool.

Continuously sends $F,320,240,5000,+00*CS frames to STM32
and prints any data received back. Used to verify STM32 UART RX.

Usage:
    python3 uart_f_test.py [--port /dev/ttyUSB0] [--interval 0.1]
"""
import serial
import serial.tools.list_ports
import time
import sys
import glob
import argparse


def find_uart():
    for pattern in ['/dev/ttyUSB*', '/dev/ttyACM*', '/dev/ttyAS*']:
        ports = sorted(glob.glob(pattern))
        if ports:
            return ports[0]
    return '/dev/ttyUSB0'


def checksum(data: str) -> str:
    cs = 0
    for b in data.encode():
        cs ^= b
    return f'{cs:02X}'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', default=None)
    parser.add_argument('--interval', type=float, default=0.1)
    parser.add_argument('--baud', type=int, default=115200)
    args = parser.parse_args()

    port = args.port or find_uart()
    print(f'[TEST] Opening {port} @ {args.baud}bps, interval={args.interval}s')

    try:
        ser = serial.Serial(port, args.baud, timeout=0.05, write_timeout=0.1)
    except Exception as e:
        print(f'[TEST] Failed to open {port}: {e}')
        sys.exit(1)

    print(f'[TEST] Port opened. Sending $F frames every {args.interval}s')
    print(f'[TEST] Press Ctrl+C to stop\n')

    tx_count = 0
    rx_count = 0
    rx_buf = b''
    start_time = time.time()

    try:
        while True:
            # --- TX: send F frame ---
            body = 'F,320,240,5000,+0'
            cs = checksum(body)
            msg = f'${body}*{cs}\n'
            try:
                ser.write(msg.encode())
                tx_count += 1
                elapsed = time.time() - start_time
                if tx_count % 10 == 1:
                    print(f'[TX #{tx_count:>5}] {msg.strip()}  ({elapsed:.1f}s)')
            except Exception as e:
                print(f'[TX ERROR] {e}')

            # --- RX: read any response ---
            try:
                data = ser.read(256)
                if data:
                    rx_buf += data
                    rx_count += 1
                    # print each complete line or raw bytes
                    while b'\n' in rx_buf:
                        line, rx_buf = rx_buf.split(b'\n', 1)
                        line_str = line.decode('ascii', errors='replace').strip()
                        if line_str:
                            print(f'  [RX] {line_str}')
                    # print remaining raw bytes if any printable
                    if rx_buf:
                        printable = rx_buf.decode('ascii', errors='replace').strip()
                        if printable and len(rx_buf) > 10:
                            print(f'  [RX raw] {printable!r} ({len(rx_buf)} bytes)')
                            rx_buf = b''
            except Exception as e:
                print(f'[RX ERROR] {e}')

            time.sleep(args.interval)

    except KeyboardInterrupt:
        elapsed = time.time() - start_time
        print(f'\n[TEST] Stopped. TX={tx_count} frames in {elapsed:.1f}s, '
              f'RX events={rx_count}')
        if rx_buf:
            print(f'[TEST] Remaining RX buffer: {rx_buf!r}')
    finally:
        ser.close()
        print('[TEST] Port closed.')


if __name__ == '__main__':
    main()
