#!/usr/bin/env python3
"""
轮式格斗机器人 - 视觉主程序 v5

v5 改进:
  - 内嵌 MJPEG 流媒体服务器 (--stream, 默认开启, 端口8080)
  - 检测日志写入文件 (/tmp/vision_det.log), 滚动保留最近1000行
  - 空间排斥检测 + 白色严格过滤 (detector v2)

用法:
    python3 main.py                         # 正常运行 + 串流 + 日志
    python3 main.py --no-stream             # 不启动流媒体
    python3 main.py --stream-port 9090      # 自定义流媒体端口
    python3 main.py --no-uart               # 不连串口
    python3 main.py --calibrate             # 标定模式
    python3 main.py --color y               # 己方黄色
    python3 main.py --priority-mode attack  # 攻击模式
"""
import sys
import os
import time
import argparse
import threading
import logging
from logging.handlers import RotatingFileHandler
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

import cv2

import config
from detector import ColorDetector
from comm import UartComm

# ============ Detection Logger ============
LOG_PATH = os.environ.get('VISION_LOG', '/tmp/vision_det.log')

det_logger = logging.getLogger('det')
det_logger.setLevel(logging.INFO)
_handler = RotatingFileHandler(LOG_PATH, maxBytes=200_000, backupCount=2)
_handler.setFormatter(logging.Formatter('%(asctime)s %(message)s', datefmt='%H:%M:%S'))
det_logger.addHandler(_handler)


# ============ MJPEG Streaming Server ============
_stream_jpg = None
_stream_lock = threading.Lock()
_stream_event = threading.Event()


class _MJPEGHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<!DOCTYPE html><html><head>'
                             b'<title>Robot Vision</title>'
                             b'<style>body{margin:0;background:#111;'
                             b'display:flex;justify-content:center;'
                             b'align-items:center;height:100vh}'
                             b'img{max-width:100%;border:2px solid #0f0}'
                             b'</style></head><body>'
                             b'<img src="/stream"></body></html>')
        elif self.path == '/stream':
            self.send_response(200)
            self.send_header('Content-Type',
                             'multipart/x-mixed-replace; boundary=--jpgbound')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            try:
                while True:
                    _stream_event.wait(timeout=2.0)
                    _stream_event.clear()
                    with _stream_lock:
                        data = _stream_jpg
                    if data is None:
                        continue
                    self.wfile.write(b'--jpgbound\r\n')
                    self.wfile.write(b'Content-Type: image/jpeg\r\n')
                    self.wfile.write(
                        f'Content-Length: {len(data)}\r\n'.encode())
                    self.wfile.write(b'\r\n')
                    self.wfile.write(data)
                    self.wfile.write(b'\r\n')
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
        else:
            self.send_error(404)

    def log_message(self, fmt, *args):
        pass  # suppress access logs


class _ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def _start_stream_server(port):
    """Start MJPEG server in a daemon thread."""
    server = _ThreadedHTTPServer(('0.0.0.0', port), _MJPEGHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f'Stream: http://0.0.0.0:{port}')
    return server


def _publish_frame(frame):
    """Encode and publish a frame to the MJPEG stream."""
    global _stream_jpg
    ok, jpg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
    if ok:
        with _stream_lock:
            _stream_jpg = jpg.tobytes()
        _stream_event.set()


# ============ Camera ============
class Camera:
    """摄像头管理, 支持断线自动重连 + 曝光锁定"""

    def __init__(self):
        self.cap = None
        self._reconnect_interval = 2.0
        self._last_reconnect = 0.0

    def open(self):
        dev = config.CAMERA_DEVICE
        print(f'Opening camera: {dev}')
        self.cap = cv2.VideoCapture(dev)
        if not self.cap.isOpened():
            print(f'ERROR: cannot open {dev}')
            return False
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_HEIGHT)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        ColorDetector.set_camera_props(self.cap)
        for _ in range(5):
            self.cap.read()
        print(f'Camera ready: {config.CAMERA_WIDTH}x{config.CAMERA_HEIGHT}')
        return True

    def read(self):
        if self.cap is None or not self.cap.isOpened():
            return self._try_reconnect()
        ret, frame = self.cap.read()
        if ret:
            return frame
        print('\nCamera read failed, reconnecting...')
        self.release()
        return self._try_reconnect()

    def _try_reconnect(self):
        now = time.time()
        if now - self._last_reconnect < self._reconnect_interval:
            return None
        self._last_reconnect = now
        config.CAMERA_DEVICE = config.find_camera()
        if self.open():
            print('Camera reconnected!')
            ret, frame = self.cap.read()
            return frame if ret else None
        return None

    def release(self):
        if self.cap:
            self.cap.release()
            self.cap = None


# ============ Vision System ============
class VisionSystem:
    def __init__(self, debug=False, calibrate=False, use_uart=True,
                 stream=True, stream_port=8080):
        self.debug = debug
        self.calibrate = calibrate
        self.use_uart = use_uart
        self.stream = stream
        self.camera = Camera()
        self.detector = ColorDetector(enable_tracking=True)
        self.comm = UartComm()
        self._frame_count = 0
        self._fps_timer = time.time()
        self._fps = 0.0
        self._tx_count = 0
        self._debug_thread = None
        self._last_valid_frame = time.time()
        self._watchdog_timeout = getattr(config, 'WATCHDOG_TIMEOUT', 30.0)
        self._stream_server = None
        self._stream_port = stream_port

    def _update_fps(self):
        self._frame_count += 1
        elapsed = time.time() - self._fps_timer
        if elapsed >= 1.0:
            self._fps = self._frame_count / elapsed
            self._frame_count = 0
            self._fps_timer = time.time()

    def _calibrate_frame(self, frame):
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        h, w = frame.shape[:2]
        roi = hsv[h // 2 - 25:h // 2 + 25, w // 2 - 25:w // 2 + 25]
        hm, sm, vm = roi[:, :, 0].mean(), roi[:, :, 1].mean(), roi[:, :, 2].mean()
        hs, ss, vs = roi[:, :, 0].std(), roi[:, :, 1].std(), roi[:, :, 2].std()
        sys.stdout.write(
            f'\rCenter HSV: H={hm:.0f}+-{hs:.0f}  '
            f'S={sm:.0f}+-{ss:.0f}  '
            f'V={vm:.0f}+-{vs:.0f}  '
            f'FPS={self._fps:.1f}   ')
        sys.stdout.flush()

    def _async_save_debug(self, frame):
        if self._debug_thread and self._debug_thread.is_alive():
            return
        def _write(f):
            cv2.imwrite('/tmp/vision_debug.jpg', f)
        self._debug_thread = threading.Thread(target=_write, args=(frame,),
                                              daemon=True)
        self._debug_thread.start()

    def run(self):
        if not self.camera.open():
            print('Waiting for camera...')

        if self.use_uart:
            self.comm.open()

        if self.stream:
            self._stream_server = _start_stream_server(self._stream_port)

        print('Vision system v5 running. Ctrl+C to stop.')
        print(f'Mode    : {"calibrate" if self.calibrate else "debug" if self.debug else "normal"}')
        print(f'Color   : {self.comm.my_color}')
        print(f'UART    : {"on" if self.use_uart else "off"}')
        print(f'Stream  : {"on :" + str(self._stream_port) if self.stream else "off"}')
        print(f'CLAHE   : {"on" if getattr(config, "USE_CLAHE", True) else "off"}')
        print(f'Priority: {getattr(config, "PRIORITY_MODE", "collect")}')
        print(f'Log     : {LOG_PATH}')
        print('-' * 55)

        det_logger.info('=== Vision started color=%s priority=%s ===',
                        self.comm.my_color,
                        getattr(config, 'PRIORITY_MODE', 'collect'))

        frame_idx = 0
        frame_dt = 1.0 / max(1, config.TARGET_FPS)
        next_frame = time.perf_counter()

        try:
            while True:
                t0 = time.perf_counter()

                # 1. 读帧
                frame = self.camera.read()
                if frame is None:
                    if time.time() - self._last_valid_frame > self._watchdog_timeout:
                        print(f'\n[WATCHDOG] No valid frame for '
                              f'{self._watchdog_timeout:.0f}s, restarting camera...')
                        self.camera.release()
                        self.camera.open()
                        self._last_valid_frame = time.time()
                    time.sleep(0.05)
                    next_frame = time.perf_counter() + frame_dt
                    continue
                self._last_valid_frame = time.time()

                # 2. 读STM32指令
                cmd = self.comm.read_command()
                if cmd:
                    print(f'\n[UART] Cmd: {cmd!r} my_color={self.comm.my_color}')
                    det_logger.info('UART_CMD %s color=%s', cmd, self.comm.my_color)

                # 3. 标定模式
                if self.calibrate:
                    self._calibrate_frame(frame)
                    if self.stream:
                        _publish_frame(frame)
                    self._update_fps()
                    self._sleep_until(next_frame)
                    next_frame += frame_dt
                    continue

                # 4. 检测
                targets = self.detector.detect(frame)
                friends, enemies, neutrals = self.detector.classify(
                    targets, self.comm.my_color)

                # 5. 发送给STM32
                self.comm.send_from_detection(friends, enemies, neutrals)
                self._tx_count += 1

                # 6. FPS
                self._update_fps()
                frame_idx += 1
                dt_ms = (time.perf_counter() - t0) * 1000

                # 7. 检测日志 (每帧写入)
                ptype, pt = self.detector.get_priority_target(
                    friends, enemies, neutrals)
                if pt:
                    det_logger.info(
                        'DET %s cx=%d cy=%d area=%d dir=%+.2f '
                        'dist=%s E=%d N=%d F=%d %.0fms',
                        ptype, pt.cx, pt.cy, int(pt.area),
                        pt.direction, pt.distance_level,
                        len(enemies), len(neutrals), len(friends), dt_ms)
                elif frame_idx % 30 == 0:
                    det_logger.info('DET X (no target) E=%d N=%d F=%d',
                                    len(enemies), len(neutrals), len(friends))

                # 8. 终端状态输出
                if frame_idx % 15 == 0:
                    pstr = ''
                    if pt:
                        pstr = (f' >> {ptype}({pt.cx},{pt.cy} '
                                f'dir={pt.direction:+.2f})')
                    uart_s = 'OK' if (self.comm.ser
                                      and self.comm._tx_errors == 0) else 'ERR'
                    sys.stdout.write(
                        f'\r[{self._fps:5.1f}fps {dt_ms:4.1f}ms] '
                        f'UART:{uart_s} TX:{self._tx_count} '
                        f'E:{len(enemies)} N:{len(neutrals)} F:{len(friends)}'
                        f'{pstr}      ')
                    sys.stdout.flush()

                # 9. 流媒体: 带标注帧
                if self.stream and frame_idx % 2 == 0:
                    annotated = self.detector.draw_targets(
                        frame.copy(), targets, self.comm.my_color)
                    info = (f'FPS:{self._fps:.0f} {dt_ms:.0f}ms '
                            f'MY:{self.comm.my_color} '
                            f'E:{len(enemies)} N:{len(neutrals)} '
                            f'F:{len(friends)}')
                    cv2.putText(annotated, info, (10, 25),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                                (0, 255, 0), 2)
                    _publish_frame(annotated)

                # 10. 调试帧保存
                if self.debug and frame_idx % 30 == 0:
                    dbg = self.detector.draw_targets(
                        frame.copy(), targets, self.comm.my_color)
                    self._async_save_debug(dbg)

                # 11. 帧率限速
                self._sleep_until(next_frame)
                next_frame += frame_dt

        except KeyboardInterrupt:
            print('\nStopping...')
        finally:
            self.camera.release()
            self.comm.close()
            det_logger.info('=== Vision stopped ===')
            print('Vision system stopped.')

    @staticmethod
    def _sleep_until(target):
        remaining = target - time.perf_counter()
        if remaining > 0.001:
            time.sleep(remaining)


def main():
    parser = argparse.ArgumentParser(description='Robot Vision System v5')
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--calibrate', action='store_true')
    parser.add_argument('--no-uart', action='store_true')
    parser.add_argument('--color', choices=['b', 'y'], default='b')
    parser.add_argument('--priority-mode', choices=['collect', 'attack'],
                        default=None)
    parser.add_argument('--no-stream', action='store_true',
                        help='Disable MJPEG stream server')
    parser.add_argument('--stream-port', type=int, default=8080,
                        help='MJPEG stream port (default: 8080)')
    args = parser.parse_args()

    if args.priority_mode:
        config.PRIORITY_MODE = args.priority_mode

    vision = VisionSystem(
        debug=args.debug,
        calibrate=args.calibrate,
        use_uart=not args.no_uart,
        stream=not args.no_stream,
        stream_port=args.stream_port,
    )
    vision.comm.my_color = args.color
    vision.run()


if __name__ == '__main__':
    main()
