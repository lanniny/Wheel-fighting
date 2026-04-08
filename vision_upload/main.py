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
from detector import ColorDetector, TagDetector
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
    allow_reuse_address = True


def _start_stream_server(port):
    """Start MJPEG server in a daemon thread, retry on port conflict."""
    for attempt in range(3):
        try:
            server = _ThreadedHTTPServer(('0.0.0.0', port), _MJPEGHandler)
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            print(f'Stream: http://0.0.0.0:{port}')
            return server
        except OSError as e:
            if attempt < 2:
                print(f'[WARN] Stream port {port} busy, retry in 3s...')
                time.sleep(3)
            else:
                print(f'[WARN] Stream server failed: {e}, continuing without stream')
                return None


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
            print(f'[WARN] Default backend failed, trying V4L2...')
            self.cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
        if not self.cap.isOpened():
            print(f'ERROR: cannot open {dev}')
            return False
        # 统一相机参数设置 (AE/WB/FOURCC/分辨率/FPS)
        config.setup_camera(self.cap)
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
                 stream=True, stream_port=8080, tag_backend=None):
        self.debug = debug
        self.calibrate = calibrate
        self.use_uart = use_uart
        self.stream = stream
        self.camera = Camera()
        self.detector = ColorDetector(enable_tracking=True)
        # Tag 检测: 显式指定后端 或 config 启用时自动创建
        if tag_backend:
            self.tag_detector = TagDetector(tag_backend)
        elif getattr(config, 'TAG_DETECT_ENABLED', False):
            self.tag_detector = TagDetector()
        else:
            self.tag_detector = None
        self._tag_interval = getattr(config, 'TAG_DETECT_INTERVAL', 3)
        self._last_tags = []  # 缓存最近一次 Tag 检测结果
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
        # 性能分析累加器
        self._perf_cap = 0.0
        self._perf_det = 0.0
        self._perf_uart = 0.0
        self._perf_anno = 0.0
        self._perf_stream = 0.0
        self._perf_count = 0

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
        print(f'CLAHE   : {"on" if getattr(config, "USE_CLAHE", True) else "off"}'
              f' (clip={getattr(config, "CLAHE_CLIP_LIMIT", 1.5)}'
              f' tile={getattr(config, "CLAHE_TILE_SIZE", 4)}'
              f' S_ch={"on" if getattr(config, "CLAHE_S_CHANNEL", True) else "off"})')
        print(f'AdaptV  : {"on" if getattr(config, "ADAPTIVE_V_THRESHOLD", True) else "off"}')
        print(f'Tracker : smooth={getattr(config, "TRACKER_SMOOTHING", 0.15)}'
              f' maxdist={getattr(config, "TRACKER_MAX_DIST", 150)}'
              f' predict={"on" if getattr(config, "TRACKER_PREDICT", True) else "off"}')
        own_only = getattr(config, 'DETECT_OWN_ONLY', True)
        print(f'Detect  : {"own-only" if own_only else "dual-color"}')
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

                # 1. 读帧 [计时: cap]
                frame = self.camera.read()
                t_cap = time.perf_counter()
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
                    extra = ''
                    if cmd == 'D':
                        extra = ' → DROP RECOVERY MODE'
                    elif cmd == 's' and not self.comm.drop_recovery:
                        extra = ' → NORMAL DETECT MODE'
                    print(f'\n[UART] Cmd: {cmd!r} my_color={self.comm.my_color}{extra}')
                    det_logger.info('UART_CMD %s color=%s%s', cmd, self.comm.my_color, extra)

                    # 颜色切换 → 动态调整白平衡
                    if getattr(self.comm, '_color_changed', False):
                        self.comm._color_changed = False
                        new_wb = (config.WB_YELLOW
                                  if self.comm.my_color == 'y'
                                  else config.WB_BLUE)
                        if new_wb != config.WB_TEMPERATURE:
                            config.WB_TEMPERATURE = new_wb
                            if self.camera.cap:
                                self.camera.cap.set(
                                    cv2.CAP_PROP_WB_TEMPERATURE, new_wb)
                            print(f'[AUTO-WB] color={self.comm.my_color}'
                                  f' → WB={new_wb}K')
                            det_logger.info('WB_SWITCH %dK color=%s',
                                            new_wb, self.comm.my_color)

                # 3. 掉台回复模式: 黑色(台面)检测
                if self.comm.drop_recovery:
                    ratio, bcx, bcy, bdir = self.detector.detect_black_ratio(frame)
                    t_det = time.perf_counter()

                    threshold = getattr(config, 'DROP_BLACK_RATIO_THRESHOLD', 0.55)
                    ratio_pct = int(ratio * 100)
                    if ratio >= threshold:
                        self.comm.send_target('G', bcx, bcy, ratio_pct, bdir)
                    else:
                        self.comm.send_target('X')
                    t_uart = time.perf_counter()

                    self._update_fps()
                    frame_idx += 1
                    dt_ms = (time.perf_counter() - t0) * 1000

                    # 日志
                    det_logger.info('DROP ratio=%d%% cx=%d dir=%+.2f %s %.0fms',
                                    ratio_pct, bcx, bdir,
                                    'GO!' if ratio >= threshold else 'wait',
                                    dt_ms)

                    # 终端输出
                    if frame_idx % 10 == 0:
                        status = 'GO!' if ratio >= threshold else 'wait'
                        sys.stdout.write(
                            f'\r[{self._fps:5.1f}fps {dt_ms:4.1f}ms] '
                            f'DROP black={ratio_pct}% dir={bdir:+.2f} '
                            f'{status}      ')
                        sys.stdout.flush()

                    # 流媒体: 标注黑色检测结果
                    if self.stream and self._stream_server and frame_idx % 3 == 0:
                        annotated = frame.copy()
                        h, w = frame.shape[:2]
                        mx, my = w // 6, h // 6
                        color = (0, 255, 0) if ratio >= threshold else (0, 0, 255)
                        cv2.rectangle(annotated, (mx, my), (w - mx, h - my),
                                      color, 2)
                        cv2.drawMarker(annotated, (bcx, bcy), color,
                                       cv2.MARKER_CROSS, 20, 2)
                        info = (f'DROP {ratio_pct}% dir={bdir:+.2f} '
                                f'{"GO!" if ratio >= threshold else "wait"}')
                        cv2.putText(annotated, info, (10, 25),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
                        _publish_frame(annotated)

                    self._sleep_until(next_frame)
                    next_frame += frame_dt
                    continue

                # 4. 标定模式
                if self.calibrate:
                    self._calibrate_frame(frame)
                    if self.stream:
                        _publish_frame(frame)
                    self._update_fps()
                    self._sleep_until(next_frame)
                    next_frame += frame_dt
                    continue

                # 4. 检测 [计时: det]
                tags = []
                own_targets = []
                targets = []
                friends, enemies, neutrals = [], [], []
                pt = None
                tag_type_override = None  # Tag 覆盖颜色分类

                collect_mode = (
                    self.tag_detector is not None
                    and getattr(self.comm, 'mode', 'fight') == 'collect'
                )

                if collect_mode:
                    # 收集模式: 仅 Tag 检测
                    n_own = 0 if own_only else -1
                    if frame_idx % 5 == 0:
                        tags = self.tag_detector.detect_tags(frame)
                        tags.sort(key=lambda t: int(t.get('area', 0)),
                                  reverse=True)
                        self._last_tags = tags
                    else:
                        tags = self._last_tags
                else:
                    # 格斗模式: 颜色检测(每帧) + Tag 辅助(降频)
                    if own_only:
                        own_targets = self.detector.detect_own(
                            frame, self.comm.my_color)
                        n_own = len(own_targets)
                        pt = own_targets[0] if own_targets else None
                    else:
                        targets = self.detector.detect(frame)
                        friends, enemies, neutrals = self.detector.classify(
                            targets, self.comm.my_color)
                        n_own = -1
                        _, pt = self.detector.get_priority_target(
                            friends, enemies, neutrals)

                    # Tag 辅助检测: 每 N 帧做一次, 用 Tag ID 覆盖颜色分类
                    if (self.tag_detector is not None
                            and frame_idx % self._tag_interval == 0):
                        tags = self.tag_detector.detect_tags(frame)
                        self._last_tags = tags
                    else:
                        tags = self._last_tags

                    # Tag→类型覆盖: 匹配最近的颜色目标
                    if tags and pt:
                        from detector import TagDetector as _TD
                        best_tag = tags[0]
                        tag_cx = best_tag.get('cx', 0)
                        tag_cy = best_tag.get('cy', 0)
                        dist = ((tag_cx - pt.cx)**2 + (tag_cy - pt.cy)**2)**0.5
                        if dist < 80:
                            tag_type_override = _TD.classify_tag(
                                best_tag.get('id', 0), self.comm.my_color)

                t_det = time.perf_counter()

                # 5. 发送 [计时: uart]
                if collect_mode:
                    if tags:
                        # 收集模式: Tag 结果转为类型发送
                        from detector import TagDetector as _TD
                        best = tags[0]
                        t_type = _TD.classify_tag(
                            best.get('id', 0), self.comm.my_color)
                        direction = ((best.get('cx', 0) - config.CAMERA_WIDTH / 2)
                                     / (config.CAMERA_WIDTH / 2))
                        self.comm.send_target(t_type, best.get('cx', 0),
                                              best.get('cy', 0),
                                              best.get('area', 0), direction)
                    else:
                        self.comm.send_target('X')
                elif tag_type_override and pt:
                    # Tag 覆盖: 用精确类型发送
                    self.comm.send_target(tag_type_override,
                                          pt.cx, pt.cy, pt.area, pt.direction)
                elif own_only:
                    self.comm.send_own_detection(own_targets)
                else:
                    self.comm.send_from_detection(friends, enemies, neutrals)
                t_uart = time.perf_counter()

                self._tx_count += 1

                # 6. FPS
                self._update_fps()
                frame_idx += 1
                dt_ms = (time.perf_counter() - t0) * 1000

                # 7. 检测日志
                if own_only:
                    if pt:
                        det_logger.info(
                            'OWN cx=%d cy=%d area=%d dir=%+.2f '
                            'dist=%s %.0fms',
                            pt.cx, pt.cy, int(pt.area),
                            pt.direction, pt.distance_level, dt_ms)
                    elif frame_idx % 30 == 0:
                        det_logger.info('OWN X (no target)')
                else:
                    if pt:
                        det_logger.info(
                            'DET cx=%d cy=%d area=%d dir=%+.2f '
                            'E=%d N=%d F=%d %.0fms',
                            pt.cx, pt.cy, int(pt.area), pt.direction,
                            len(enemies), len(neutrals), len(friends),
                            dt_ms)
                    elif frame_idx % 30 == 0:
                        det_logger.info('DET X E=%d N=%d F=%d',
                                        len(enemies), len(neutrals),
                                        len(friends))
                if tags:
                    det_logger.info('TAG %s', tags)

                # 8. 终端状态输出
                if frame_idx % 15 == 0:
                    uart_s = 'OK' if (self.comm.ser
                                      and self.comm._tx_errors == 0) else 'ERR'
                    if own_only:
                        pstr = ''
                        if pt:
                            pstr = (f' >> F({pt.cx},{pt.cy} '
                                    f'dir={pt.direction:+.2f})')
                        sys.stdout.write(
                            f'\r[{self._fps:5.1f}fps {dt_ms:4.1f}ms] '
                            f'UART:{uart_s} TX:{self._tx_count} '
                            f'OWN:{n_own}{pstr}      ')
                    else:
                        sys.stdout.write(
                            f'\r[{self._fps:5.1f}fps {dt_ms:4.1f}ms] '
                            f'UART:{uart_s} TX:{self._tx_count} '
                            f'E:{len(enemies)} N:{len(neutrals)} '
                            f'F:{len(friends)}      ')
                    sys.stdout.flush()

                # 9. 流媒体 [计时: anno + stream]
                t_anno_start = time.perf_counter()
                t_anno_end = t_anno_start
                if self.stream and self._stream_server and frame_idx % 3 == 0:
                    if own_only:
                        annotated = self.detector.draw_targets(
                            frame.copy(), own_targets, self.comm.my_color)
                        info = (f'FPS:{self._fps:.0f} {dt_ms:.0f}ms '
                                f'MY:{self.comm.my_color} OWN:{n_own}')
                    else:
                        annotated = self.detector.draw_targets(
                            frame.copy(), targets, self.comm.my_color)
                        info = (f'FPS:{self._fps:.0f} {dt_ms:.0f}ms '
                                f'MY:{self.comm.my_color} '
                                f'E:{len(enemies)} N:{len(neutrals)} '
                                f'F:{len(friends)}')
                    cv2.putText(annotated, info, (10, 25),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                                (0, 255, 0), 2)
                    t_anno_end = time.perf_counter()
                    _publish_frame(annotated)
                t_stream = time.perf_counter()

                # 10. 调试帧保存
                if self.debug and frame_idx % 30 == 0:
                    dbg_targets = own_targets if own_only else targets
                    dbg = self.detector.draw_targets(
                        frame.copy(), dbg_targets, self.comm.my_color)
                    self._async_save_debug(dbg)

                # 11. 性能分析 (每30帧输出一次分阶段耗时)
                self._perf_cap += (t_cap - t0)
                self._perf_det += (t_det - t_cap)
                self._perf_uart += (t_uart - t_det)
                self._perf_anno += (t_anno_end - t_anno_start)
                self._perf_stream += (t_stream - t_anno_end)
                self._perf_count += 1
                if self._perf_count >= 30:
                    n = self._perf_count
                    det_logger.info(
                        'PERF cap=%.1fms det=%.1fms uart=%.1fms '
                        'anno=%.1fms stream=%.1fms total=%.1fms',
                        self._perf_cap / n * 1000,
                        self._perf_det / n * 1000,
                        self._perf_uart / n * 1000,
                        self._perf_anno / n * 1000,
                        self._perf_stream / n * 1000,
                        (self._perf_cap + self._perf_det + self._perf_uart
                         + self._perf_anno + self._perf_stream) / n * 1000)
                    self._perf_cap = self._perf_det = 0.0
                    self._perf_uart = self._perf_anno = 0.0
                    self._perf_stream = 0.0
                    self._perf_count = 0

                # 12. 帧率限速
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
    parser = argparse.ArgumentParser(description='Robot Vision System v6')
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
    parser.add_argument('--tag-backend', choices=['qr', 'aruco', 'apriltag'],
                        default=None,
                        help='Enable tag detection (qr/aruco/apriltag)')
    parser.add_argument('--mode', choices=['fight', 'collect'],
                        default='fight',
                        help='Initial mode: fight(color) or collect(tag)')
    args = parser.parse_args()

    if args.priority_mode:
        config.PRIORITY_MODE = args.priority_mode

    # 根据队伍颜色自动设置 WB (除非环境变量已覆盖)
    if config.WB_TEMPERATURE == 0:
        config.WB_TEMPERATURE = (config.WB_YELLOW if args.color == 'y'
                                 else config.WB_BLUE)
        print(f'[AUTO-WB] color={args.color} → WB={config.WB_TEMPERATURE}K')

    vision = VisionSystem(
        debug=args.debug,
        calibrate=args.calibrate,
        use_uart=not args.no_uart,
        stream=not args.no_stream,
        stream_port=args.stream_port,
        tag_backend=args.tag_backend,
    )
    vision.comm.my_color = args.color
    vision.comm.mode = args.mode
    vision.run()


if __name__ == '__main__':
    main()
