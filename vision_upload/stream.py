#!/usr/bin/env python3
"""
MJPEG 实时视频流服务器 (带检测标注)
浏览器打开 http://localhost:8080 查看
"""
import sys
import time
import cv2
import numpy as np
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
import threading

import config
from detector import ColorDetector

latest_jpg = None
jpg_lock = threading.Lock()
jpg_event = threading.Event()


class MJPEGHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(b'''<!DOCTYPE html><html><head>
            <title>Robot Vision</title>
            <style>body{margin:0;background:#111;display:flex;justify-content:center;align-items:center;height:100vh}
            img{max-width:100%;border:2px solid #0f0}</style>
            </head><body><img src="/stream"></body></html>''')
        elif self.path == '/stream':
            self.send_response(200)
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=--jpgbound')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            try:
                while True:
                    jpg_event.wait(timeout=1.0)
                    jpg_event.clear()
                    with jpg_lock:
                        data = latest_jpg
                    if data is None:
                        continue
                    self.wfile.write(b'--jpgbound\r\n')
                    self.wfile.write(b'Content-Type: image/jpeg\r\n')
                    self.wfile.write(f'Content-Length: {len(data)}\r\n'.encode())
                    self.wfile.write(b'\r\n')
                    self.wfile.write(data)
                    self.wfile.write(b'\r\n')
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
        else:
            self.send_error(404)

    def log_message(self, fmt, *args):
        pass


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def capture_loop(my_color):
    global latest_jpg

    dev = config.CAMERA_DEVICE
    print(f'Opening camera: {dev}', flush=True)

    cap = cv2.VideoCapture(dev)
    if not cap.isOpened():
        print(f'ERROR: cannot open {dev}', file=sys.stderr, flush=True)
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_HEIGHT)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    for _ in range(5):
        cap.read()

    det = ColorDetector()
    fps_count = 0
    fps_timer = time.time()
    fps = 0.0
    print('Capture running', flush=True)

    while True:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.01)
            continue

        t0 = time.time()
        targets = det.detect(frame)
        dt_ms = (time.time() - t0) * 1000

        annotated = det.draw_targets(frame, targets, my_color)

        fps_count += 1
        now = time.time()
        if now - fps_timer >= 1.0:
            fps = fps_count / (now - fps_timer)
            fps_count = 0
            fps_timer = now

        friends, enemies, neutrals = det.classify(targets, my_color)
        info = f'FPS:{fps:.0f} det:{dt_ms:.0f}ms MY:{my_color} E:{len(enemies)} N:{len(neutrals)} F:{len(friends)}'
        cv2.putText(annotated, info, (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)

        ok, jpg = cv2.imencode('.jpg', annotated, [cv2.IMWRITE_JPEG_QUALITY, 65])
        if ok:
            with jpg_lock:
                latest_jpg = jpg.tobytes()
            jpg_event.set()

        elapsed = time.time() - t0
        wait = 1.0 / config.TARGET_FPS - elapsed
        if wait > 0:
            time.sleep(wait)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--color', default='b', choices=['b', 'y'])
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()

    t = threading.Thread(target=capture_loop, args=(args.color,), daemon=True)
    t.start()

    server = ThreadedHTTPServer(('0.0.0.0', args.port), MJPEGHandler)
    print(f'Stream: http://0.0.0.0:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()

if __name__ == '__main__':
    main()
