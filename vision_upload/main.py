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
import signal
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
        self.on_reconnect = None  # 重连回调 (清Tracker等)

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
            if self.on_reconnect:
                self.on_reconnect()
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
                 stream=True, stream_port=8080, tag_backend=None,
):
        self.debug = debug
        self.calibrate = calibrate
        self.use_uart = use_uart
        self.stream = stream
        self.camera = Camera()
        self.detector = ColorDetector(enable_tracking=True)
        # 相机重连时清除 Tracker 旧轨迹 (防止幽灵目标)
        self.camera.on_reconnect = self._on_camera_reconnect
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
        # 掉台回复迟滞状态机
        self._drop_sending_G = False    # 当前是否处于发G状态
        self._drop_confirm_count = 0    # G确认帧计数器
        self._drop_start_time = 0.0     # 掉台开始时间

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
        # 启动顺序优化: UART 先连接 (让 STM32 尽早收到数据),
        # 相机初始化较慢, 放在 UART 之后
        if self.use_uart:
            self.comm.open()

        if not self.camera.open():
            print('Waiting for camera...')

        if self.stream:
            self._stream_server = _start_stream_server(self._stream_port)

        # SIGTERM 优雅关闭 (systemd stop)
        self._running = True
        def _sigterm_handler(signum, frame_):
            print('\n[SIGTERM] Shutting down...')
            self._running = False
        signal.signal(signal.SIGTERM, _sigterm_handler)

        print('Vision system v6 running. Ctrl+C to stop.')
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
            while self._running:
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
                    self._handle_command(cmd)

                # 3. 掉台回复模式: 黑色(台面)检测
                if self.comm.drop_recovery:
                    # 超时保护: 防止永久卡在掉台回复模式
                    drop_timeout = getattr(config, 'DROP_RECOVERY_TIMEOUT', 25.0)
                    if self._drop_start_time <= 0:
                        self._drop_start_time = time.time()
                    drop_elapsed = time.time() - self._drop_start_time

                    if drop_elapsed >= drop_timeout:
                        det_logger.info('DROP_TIMEOUT %.1fs elapsed, keep sending X until STM32 sends S', drop_elapsed)
                        print(f'\n[DROP] Timeout {drop_elapsed:.1f}s, keep X (wait for S)')
                        # 不退出 drop_recovery! 持续发 X 直到 STM32 发 'S'
                        # 否则视觉切回正常模式发 E/N/F 会污染 Backup 类型消抖
                        self._drop_sending_G = False
                        self._drop_confirm_count = 0
                        self.comm._last_send = 0.0
                        self.comm.send_target('X')
                        self._sleep_until(next_frame)
                        next_frame += frame_dt
                        continue

                    ratio, bcx, bcy, bdir, dbg = self.detector.detect_black_ratio(frame)
                    t_det = time.perf_counter()

                    # 迟滞 + 确认帧机制:
                    #  - 未发G: ratio 连续 N帧 > HIGH → 开始发G
                    #  - 已发G: ratio < LOW → 停止发G (立即, 不需确认)
                    th_high = getattr(config, 'DROP_BLACK_RATIO_HIGH', 0.50)
                    th_low = getattr(config, 'DROP_BLACK_RATIO_LOW', 0.30)
                    confirm_n = getattr(config, 'DROP_G_CONFIRM_FRAMES', 3)

                    if self._drop_sending_G:
                        # 已在发G: 低于下限则立即退出
                        if ratio < th_low:
                            self._drop_sending_G = False
                            self._drop_confirm_count = 0
                    else:
                        # 未发G: 需连续N帧超上限
                        if ratio >= th_high:
                            self._drop_confirm_count += 1
                            if self._drop_confirm_count >= confirm_n:
                                self._drop_sending_G = True
                        else:
                            self._drop_confirm_count = 0

                    ratio_pct = int(ratio * 100)
                    if self._drop_sending_G:
                        self.comm.send_target('G', bcx, bcy, ratio_pct, bdir)
                    else:
                        # CRITICAL: 掉台等G阶段发X必须≥20Hz(50ms),
                        # 否则默认5Hz(200ms)触及STM32的200ms超时,
                        # 导致Vision_IsTimeout()=1, Backup无法用视觉辅助冲台
                        self.comm._last_send = 0.0  # 强制本次立即发送
                        self.comm.send_target('X')
                    t_uart = time.perf_counter()

                    self._update_fps()
                    frame_idx += 1
                    dt_ms = (time.perf_counter() - t0) * 1000

                    # 回声状态
                    echo_ok = self.comm.echo_confirmed
                    echo_lag = self.comm._echo_latency

                    # 日志
                    g_status = 'G' if self._drop_sending_G else 'X'
                    det_logger.info(
                        'DROP send=%s ratio=%d%% blob=%d%% cx=%d dir=%+.2f '
                        'cfm=%d/%d echo=%s %.0fms',
                        g_status, ratio_pct,
                        int(dbg['blob_ratio'] * 100),
                        bcx, bdir,
                        self._drop_confirm_count, confirm_n,
                        f'{self.comm.echo_type}' if echo_ok else '-',
                        dt_ms)
                    if frame_idx % 30 == 0:
                        det_logger.info(
                            'DROP_DBG Vmean=%.0f Vstd=%.1f Sstd=%.1f '
                            'Vp25=%.0f Vth=%d raw=%d%% blob=%d%% '
                            'bonus=%.0f%% inst=%d%% thH=%d%% thL=%d%% '
                            'hyst=%s echo=%d lag=%.0fms t=%.0f/%.0fs',
                            dbg['v_mean'], dbg['v_std'], dbg['s_std'],
                            dbg['v_p25'], dbg['v_th'],
                            int(dbg['raw_ratio'] * 100),
                            int(dbg['blob_ratio'] * 100),
                            dbg['bonus'] * 100,
                            int(dbg.get('instant_ratio', ratio) * 100),
                            int(th_high * 100), int(th_low * 100),
                            g_status,
                            self.comm._echo_count, echo_lag,
                            drop_elapsed, drop_timeout)

                    # 终端输出
                    if frame_idx % 10 == 0:
                        status = f'GO[{g_status}]' if self._drop_sending_G else 'wait'
                        echo_str = (f'E:{self.comm.echo_type} {echo_lag:.0f}ms'
                                    if echo_ok else 'E:--')
                        if not self.comm.echo_healthy and self.comm._echo_count > 0:
                            echo_str = 'E:LOST!'
                        sys.stdout.write(
                            f'\r[{self._fps:5.1f}fps {dt_ms:4.1f}ms] '
                            f'DROP {ratio_pct}% '
                            f'cfm={self._drop_confirm_count}/{confirm_n} '
                            f'dir={bdir:+.2f} {status} {echo_str} '
                            f'[{drop_elapsed:.0f}/{drop_timeout:.0f}s]      ')
                        sys.stdout.flush()

                    # 流媒体: 标注黑色检测结果 (含迟滞状态)
                    if self.stream and self._stream_server and frame_idx % 3 == 0:
                        annotated = frame.copy()
                        h, w = frame.shape[:2]
                        mx, my = w // 6, h // 6
                        color = (0, 255, 0) if self._drop_sending_G else (0, 0, 255)
                        cv2.rectangle(annotated, (mx, my), (w - mx, h - my),
                                      color, 2)
                        cv2.drawMarker(annotated, (bcx, bcy), color,
                                       cv2.MARKER_CROSS, 20, 2)
                        echo_tag = ('ACK' if echo_ok else
                                    'LOST' if not self.comm.echo_healthy else '...')
                        # 迟滞状态可视化
                        hyst_str = (f'SEND G cfm={self._drop_confirm_count}'
                                    if self._drop_sending_G
                                    else f'WAIT cfm={self._drop_confirm_count}/{confirm_n}')
                        info = (f'DROP {ratio_pct}% blob={int(dbg["blob_ratio"]*100)}% '
                                f'dir={bdir:+.2f} {hyst_str} '
                                f'E:{echo_tag} [{drop_elapsed:.0f}s]')
                        cv2.putText(annotated, info, (10, 25),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.50, color, 2)
                        # 迟滞阈值条 (画面底部)
                        bar_w = w - 2 * mx
                        bar_y = h - my - 40
                        cv2.rectangle(annotated, (mx, bar_y), (mx + bar_w, bar_y + 12),
                                      (50, 50, 50), -1)
                        # 低阈值标线
                        low_x = mx + int(bar_w * th_low)
                        cv2.line(annotated, (low_x, bar_y), (low_x, bar_y + 12),
                                 (0, 200, 255), 2)
                        # 高阈值标线
                        high_x = mx + int(bar_w * th_high)
                        cv2.line(annotated, (high_x, bar_y), (high_x, bar_y + 12),
                                 (0, 255, 0), 2)
                        # 当前ratio指示
                        cur_x = mx + int(bar_w * min(1.0, ratio))
                        cv2.circle(annotated, (cur_x, bar_y + 6), 5, color, -1)
                        # 回声确认状态栏
                        annotated = self._draw_echo_status(annotated)
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
                tag_standalone = None     # Tag 独立发现 (颜色未命中)

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
                    # ── 格斗模式: 颜色+Tag 融合 ("任一命中即生效") ──
                    tag_standalone = None  # Tag独立目标 (颜色未命中时)

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

                    if self.tag_detector is not None:
                        match_dist = getattr(config, 'TAG_ROI_MATCH_DIST', 80)

                        # 定期全帧 Tag 扫描 (发现白色/中立等无色块)
                        is_tag_scan_frame = (
                            frame_idx % self._tag_interval == 0)

                        if targets and pt:
                            # 策略A: 有优先颜色目标 → ROI Tag精确分类
                            top_targets = sorted(
                                targets, key=lambda t: t.area,
                                reverse=True)[:2]
                            roi_tags = self.tag_detector.detect_tags_in_rois(
                                frame, top_targets)
                            # 补充: 全帧扫描发现ROI外的Tag (如白色中立块)
                            if is_tag_scan_frame:
                                full_tags = self.tag_detector.detect_tags(frame)
                                roi_ids = {t.get('id') for t in roi_tags}
                                for ft in full_tags:
                                    if ft.get('id') not in roi_ids:
                                        roi_tags.append(ft)
                                tags = roi_tags
                                self._last_tags = tags
                            elif roi_tags:
                                # ROI有结果: 合并到缓存
                                tags = roi_tags + [
                                    t for t in self._last_tags
                                    if t.get('id') not in
                                       {r.get('id') for r in roi_tags}]
                                self._last_tags = tags
                            else:
                                # ROI无结果: 保留上次缓存
                                tags = self._last_tags
                            # 匹配: 为优先目标和孤儿Tag分别处理
                            if tags:
                                best_match = min(
                                    tags,
                                    key=lambda tg: ((tg['cx'] - pt.cx)**2
                                                    + (tg['cy'] - pt.cy)**2))
                                d = ((best_match['cx'] - pt.cx)**2
                                     + (best_match['cy'] - pt.cy)**2)**0.5
                                if d < match_dist:
                                    tag_type_override = TagDetector.classify_tag(
                                        best_match['id'], self.comm.my_color)
                                # 孤儿Tag: 不匹配任何颜色目标的Tag
                                # (如白色中立块, 颜色检测不到但Tag能看到)
                                for tg in tags:
                                    orphan = True
                                    for ct in targets:
                                        od = ((tg['cx'] - ct.cx)**2
                                              + (tg['cy'] - ct.cy)**2)**0.5
                                        if od < match_dist:
                                            orphan = False
                                            break
                                    if orphan:
                                        ot = TagDetector.classify_tag(
                                            tg['id'], self.comm.my_color)
                                        if ot in ('E', 'N'):
                                            tag_standalone = tg
                                            break  # 取第一个孤儿E/N
                        else:
                            # 策略B: 无优先颜色目标 → 全帧Tag发现
                            if is_tag_scan_frame:
                                tags = self.tag_detector.detect_tags(frame)
                                self._last_tags = tags
                            else:
                                tags = self._last_tags
                            # Tag独立发现目标 (白色/中立块等)
                            if tags:
                                best = max(tags, key=lambda t: t.get('area', 0))
                                tag_standalone = best

                t_det = time.perf_counter()

                # 5. 发送 [计时: uart]
                if collect_mode:
                    if tags:
                        self._send_tag_target(tags[0])
                    else:
                        self.comm.send_target('X')
                elif tag_standalone and pt and tag_standalone.get('area', 0) > pt.area * 2:
                    # 孤儿Tag面积远大于颜色噪声 → Tag目标优先
                    self._send_tag_target(tag_standalone)
                elif tag_type_override and pt:
                    # 颜色+Tag融合: Tag精确分类优先
                    self.comm.send_target(tag_type_override,
                                          pt.cx, pt.cy, pt.area, pt.direction)
                elif tag_standalone:
                    # Tag独立发现: 颜色未命中但Tag检测到目标
                    self._send_tag_target(tag_standalone)
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
                    tag_info = ''
                    if tag_type_override:
                        tag_info = f' tag={tag_type_override}'
                    elif tag_standalone:
                        tag_info = f' tag_only=id{tag_standalone["id"]}'
                    if pt:
                        det_logger.info(
                            'DET cx=%d cy=%d area=%d dir=%+.2f '
                            'E=%d N=%d F=%d%s %.0fms',
                            pt.cx, pt.cy, int(pt.area), pt.direction,
                            len(enemies), len(neutrals), len(friends),
                            tag_info, dt_ms)
                    elif tag_standalone:
                        det_logger.info(
                            'TAG_ONLY id=%s cx=%d cy=%d area=%d %.0fms',
                            tag_standalone['id'], tag_standalone['cx'],
                            tag_standalone['cy'], tag_standalone.get('area', 0),
                            dt_ms)
                    elif frame_idx % 30 == 0:
                        det_logger.info('DET X E=%d N=%d F=%d tags=%d',
                                        len(enemies), len(neutrals),
                                        len(friends), len(tags))

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
                        tag_s = ''
                        if tag_type_override:
                            tag_s = f' T:{tag_type_override}'
                        elif tag_standalone:
                            tag_s = f' T:id{tag_standalone["id"]}'
                        sys.stdout.write(
                            f'\r[{self._fps:5.1f}fps {dt_ms:4.1f}ms] '
                            f'UART:{uart_s} TX:{self._tx_count} '
                            f'E:{len(enemies)} N:{len(neutrals)} '
                            f'F:{len(friends)}{tag_s}      ')
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
                        tag_str = ''
                        if tag_type_override:
                            tag_str = f' TAG:{tag_type_override}'
                        elif tag_standalone:
                            tag_str = f' TAG_ONLY:id{tag_standalone["id"]}'
                        info = (f'FPS:{self._fps:.0f} {dt_ms:.0f}ms '
                                f'MY:{self.comm.my_color} '
                                f'E:{len(enemies)} N:{len(neutrals)} '
                                f'F:{len(friends)}{tag_str}')
                    cv2.putText(annotated, info, (10, 25),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                                (0, 255, 0), 2)
                    # Tag 标注: 在画面上画 Tag 检测框和 ID (青色, 粗线)
                    for tg in tags:
                        tcx, tcy = tg.get('cx', 0), tg.get('cy', 0)
                        ta = tg.get('area', 0)
                        ts = max(15, int(ta**0.5) // 2)
                        cv2.rectangle(annotated,
                                      (tcx - ts, tcy - ts),
                                      (tcx + ts, tcy + ts),
                                      (255, 255, 0), 3)
                        cv2.putText(annotated,
                                    f'TAG:{tg.get("id", "?")}',
                                    (tcx - ts, tcy - ts - 8),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                                    (255, 255, 0), 2)
                    # Tag独立目标: 画十字 + 引导箭头 (青色)
                    if tag_standalone:
                        sx, sy = tag_standalone['cx'], tag_standalone['cy']
                        cv2.drawMarker(annotated, (sx, sy),
                                       (255, 255, 0), cv2.MARKER_TILTED_CROSS, 24, 3)
                        fcx = config.CAMERA_WIDTH // 2
                        fcy = config.CAMERA_HEIGHT // 2
                        cv2.arrowedLine(annotated, (fcx, fcy), (sx, sy),
                                        (255, 255, 0), 2, tipLength=0.05)
                    # 回声确认状态栏 (第二行)
                    annotated = self._draw_echo_status(annotated)
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

    def _on_camera_reconnect(self):
        """相机重连回调: 清除 Tracker 旧轨迹 + 重置 EMA"""
        if self.detector._tracker:
            self.detector._tracker._tracks.clear()
            self.detector._tracker._next_id = 0
        self.detector._last_max_area = 0
        self.detector._last_bboxes = []
        print('[CAMERA] Tracker state cleared after reconnect')

    def _send_tag_target(self, tag):
        """将 Tag 检测结果分类并发送给 STM32 (消除重复的 classify+direction+send 模式)"""
        t_type = TagDetector.classify_tag(tag['id'], self.comm.my_color)
        direction = (tag['cx'] - config.CAMERA_WIDTH / 2) / (config.CAMERA_WIDTH / 2)
        self.comm.send_target(t_type, tag['cx'], tag['cy'],
                              tag.get('area', 0), direction)
        return t_type

    def _handle_command(self, cmd):
        """处理 STM32 单字节指令, 返回日志后缀字符串"""
        extra = ''
        if cmd == 'N':
            self._drop_start_time = 0.0
            self._drop_sending_G = False
            self._drop_confirm_count = 0
            self.detector.reset_drop_ema()
            self.comm.active = True
            self.comm.drop_recovery = False
            extra = ' → RESET TO NORMAL DETECT'
        elif cmd == 'D':
            self._drop_start_time = time.time()
            self._drop_sending_G = False
            self._drop_confirm_count = 0
            self.detector.reset_drop_ema()
            extra = ' → DROP RECOVERY MODE'
        elif cmd in ('s', 'S'):
            self._drop_start_time = 0.0
            self._drop_sending_G = False
            self._drop_confirm_count = 0
            self.detector.reset_drop_ema()
            if not self.comm.drop_recovery:
                extra = ' → NORMAL DETECT MODE'

        print(f'\n[UART] Cmd: {cmd!r} my_color={self.comm.my_color}{extra}')
        det_logger.info('UART_CMD %s color=%s%s', cmd, self.comm.my_color, extra)

        # 颜色切换 → 动态调整白平衡
        if getattr(self.comm, '_color_changed', False):
            self.comm._color_changed = False
            new_wb = (config.WB_YELLOW if self.comm.my_color == 'y'
                      else config.WB_BLUE)
            if new_wb != config.WB_TEMPERATURE:
                config.WB_TEMPERATURE = new_wb
                if self.camera.cap:
                    self.camera.cap.set(cv2.CAP_PROP_WB_TEMPERATURE, new_wb)
                print(f'[AUTO-WB] color={self.comm.my_color} → WB={new_wb}K')
                det_logger.info('WB_SWITCH %dK color=%s', new_wb, self.comm.my_color)

    def _draw_echo_status(self, frame):
        """在画面底部绘制 STM32 回声确认状态栏。

        显示:
          - 绿色圆点 + 'STM32 ACK' + 延迟: 确认收到
          - 黄色圆点 + 'STM32 ...'       : 等待回声 (还没收到过)
          - 红色圆点 + 'STM32 LOST'      : 回声丢失 (超时)
          - 灰色圆点 + 'ECHO OFF'        : 回声功能未启用
        """
        h, w = frame.shape[:2]
        bar_y = h - 30  # 状态栏 y 位置

        if not self.comm._echo_enabled:
            # 未启用
            cv2.circle(frame, (15, bar_y + 5), 6, (128, 128, 128), -1)
            cv2.putText(frame, 'ECHO OFF', (28, bar_y + 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (128, 128, 128), 1)
            return frame

        echo_ok = self.comm.echo_confirmed
        echo_healthy = self.comm.echo_healthy
        echo_count = self.comm._echo_count
        lag_ms = self.comm._echo_latency

        if echo_ok:
            # 确认收到 — 绿色
            color = (0, 255, 0)
            cv2.circle(frame, (15, bar_y + 5), 8, color, -1)
            text = f'STM32 ACK [{self.comm.echo_type}] {lag_ms:.0f}ms  #{echo_count}'
            cv2.putText(frame, text, (30, bar_y + 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
        elif echo_count == 0:
            # 等待首个回声 — 黄色
            color = (0, 200, 255)
            cv2.circle(frame, (15, bar_y + 5), 8, color, -1)
            cv2.putText(frame, 'STM32 waiting...', (30, bar_y + 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
        elif not echo_healthy:
            # 回声丢失 — 红色闪烁
            color = (0, 0, 255)
            cv2.circle(frame, (15, bar_y + 5), 8, color, -1)
            age = self.comm.echo_age
            text = f'STM32 LOST! last={age:.1f}s ago  #{echo_count}'
            cv2.putText(frame, text, (30, bar_y + 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
        else:
            # 有过回声但当前帧未确认 — 淡绿
            color = (0, 180, 0)
            cv2.circle(frame, (15, bar_y + 5), 6, color, -1)
            text = f'STM32 ok [{self.comm.echo_type}] {lag_ms:.0f}ms  #{echo_count}'
            cv2.putText(frame, text, (30, bar_y + 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)

        return frame

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
