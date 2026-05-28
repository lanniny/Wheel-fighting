#!/usr/bin/env python3
"""
轮式格斗机器人 - 视觉主程序 v6

v6 改进 (vs v5):
  - STM32 echo 回声确认通道 (echo_confirmed/echo_healthy)
  - 掉台迟滞状态机 (HIGH/LOW + 确认帧)
  - 性能分析器 (cap/det/uart/anno/stream 分阶段耗时)
  - SIGTERM 优雅关闭 (systemd stop)
  - read_commands() 累积命令处理 (修复多命令丢失)
  - 协议 v2 兼容 (config.PROTOCOL_V2 开关)
  - Watchdog: 30s 无有效帧重启相机
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
# B4 收编: 默认值从 config.py 取, env var 仍可覆盖 (systemd Environment=)
LOG_PATH = os.environ.get(
    'VISION_LOG', getattr(config, 'LOG_PATH', '/tmp/vision_det.log'))

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
        print(f'Opening camera: {dev} (V4L2)')
        self.cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
        if not self.cap.isOpened():
            print(f'[WARN] V4L2 failed, trying default backend...')
            self.cap = cv2.VideoCapture(dev)
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
        self._watchdog_timeout = getattr(config, 'WATCHDOG_TIMEOUT', 5.0)
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
        # v2: 状态输出节流 (替代 \r 单行刷新, 修 journalctl blob data)
        # 改用换行 print + 时间节流, systemd 能正常显示
        # B4 收编: 默认值从 config.py 取, env var 仍可覆盖
        self._last_status_print_ts = 0.0
        self._status_print_interval = float(
            os.environ.get(
                'VISION_STATUS_INTERVAL_S',
                str(getattr(config, 'STATUS_PRINT_INTERVAL_S', 5.0))))
        # P1 (2026-05-22): ThermalGuard 占位 (run() 启动时初始化, 需要 detector)
        self._thermal_guard = None
        # TAHSV: Tag-Assisted Adaptive HSV
        self._adaptive_hsv = None
        if getattr(config, 'ADAPTIVE_HSV_ENABLED', True):
            try:
                from adaptive_hsv import AdaptiveHSV
                self._adaptive_hsv = AdaptiveHSV()
            except Exception as e:
                print(f'[adaptive_hsv] init failed: {e}', flush=True)
        # E1: 上次发送的目标类型 (用于检测 X→非X 切换, 强制立即发送)
        self._last_sent_type = 'X'
        # A1: 流媒体帧间隔 (从 config 读取, 替代硬编码 3)
        self._stream_every_n = getattr(config, 'STREAM_EVERY_N_FRAMES', 3)
        # P1 (2026-05-25): 目标丢失保持 (Holdover) — 短暂丢失不立即发 X
        self._holdover_target = None    # 上次有效目标的快照 (type, cx, cy, area, dir)
        self._holdover_frames = 0       # 已保持帧数
        self._holdover_max = getattr(config, 'LOST_TARGET_HOLDOVER_FRAMES', 3)
        self._holdover_decay = getattr(config, 'LOST_TARGET_AREA_DECAY', 0.7)

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
        # v2: 时间节流 + 换行输出 (修 journalctl blob)
        now = time.time()
        if now - self._last_status_print_ts >= self._status_print_interval:
            print(f'[CAL] HSV H={hm:.0f}+-{hs:.0f} S={sm:.0f}+-{ss:.0f} '
                  f'V={vm:.0f}+-{vs:.0f} FPS={self._fps:.1f}', flush=True)
            self._last_status_print_ts = now

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

        print(f'TagPrime: {"ON" if getattr(config, "TAG_PRIMARY", True) else "OFF"}')
        print(f'Adaptive: {"ON" if self._adaptive_hsv else "OFF"}')
        print(f'Holdover: frames={self._holdover_max} decay={self._holdover_decay}')
        print(f'PriScore: area_w={getattr(config, "PRIORITY_AREA_WEIGHT", 0.6)}'
              f' center_w={getattr(config, "PRIORITY_CENTER_WEIGHT", 0.3)}'
              f' stable_w={getattr(config, "PRIORITY_STABLE_WEIGHT", 0.1)}')
        det_logger.info('=== Vision started color=%s priority=%s ===',
                        self.comm.my_color,
                        getattr(config, 'PRIORITY_MODE', 'collect'))

        frame_idx = 0
        frame_dt = 1.0 / max(1, config.TARGET_FPS)
        next_frame = time.perf_counter()

        try:
            while self._running:
                t0 = time.perf_counter()

                # 1. UART 读命令 (必须在摄像头读帧之前, 因为 V4L2 select()
                #    可能阻塞 10 秒, 期间 UART 完全不可用)
                if self.use_uart:
                    for cmd in self.comm.read_commands():
                        self._handle_command(cmd)

                # 2. 读帧 [计时: cap] — V4L2 可能阻塞数秒
                frame = self.camera.read()
                t_cap = time.perf_counter()

                # 3. 帧后再读一次 UART (捕获阻塞期间积累的命令)
                if self.use_uart:
                    for cmd in self.comm.read_commands():
                        self._handle_command(cmd)

                if frame is None:
                    if self.use_uart:
                        self.comm.force_send_now()
                        self.comm.send_target('X')
                    if time.time() - self._last_valid_frame > self._watchdog_timeout:
                        print(f'\n[WATCHDOG] No valid frame for '
                              f'{self._watchdog_timeout:.0f}s, restarting camera...')
                        self.camera.release()
                        # B3修复(2026-05-29): 检查open()返回值, 成功才reset看门狗计时+清Tracker;
                        # 失败则退避(拉长下次重启间隔), 防相机故障时无效反复 release/open。
                        if self.camera.open():
                            self._last_valid_frame = time.time()
                            self._watchdog_fail = 0
                            if self.camera.on_reconnect:
                                self.camera.on_reconnect()
                        else:
                            self._watchdog_fail = getattr(
                                self, '_watchdog_fail', 0) + 1
                            print(f'[WATCHDOG] camera reopen FAILED '
                                  f'(#{self._watchdog_fail}), backing off',
                                  flush=True)
                            # 退避: 把计时往前挪, 使下次 watchdog 在 backoff 秒后再触发
                            backoff = min(5.0 * self._watchdog_fail, 15.0)
                            self._last_valid_frame = (
                                time.time() - self._watchdog_timeout + backoff)
                    time.sleep(0.05)
                    next_frame = time.perf_counter() + frame_dt
                    continue
                self._last_valid_frame = time.time()

                # 3. 掉台回复模式: 黑色(台面)检测
                if self.comm.drop_recovery:
                    # 硬超时兜底(2026-05-29 C2修复): 退出掉台模式的权力交给STM32的'S'命令
                    # (STM32侧已改为 Backup_IsDone 后周期重发S, 可靠送达)。本地超时仅作
                    # "S永久丢失"的硬兜底: 25s→90s(比赛120s内, 正常回台<10s远不触及)。
                    # 原bug(F4): 25s就切回正常检测发F/E/N, 回台>25s时会污染STM32 Backup状态机。
                    # 注: 硬超时前持续按黑检测G/X迟滞辅助, 不切回发F/E/N(避免STM32误确认陷阱)。
                    drop_timeout = getattr(config, 'DROP_RECOVERY_HARD_TIMEOUT', 90.0)
                    if self._drop_start_time <= 0:
                        self._drop_start_time = time.time()
                    drop_elapsed = time.time() - self._drop_start_time

                    if drop_elapsed >= drop_timeout:
                        # 硬超时: STM32的S可能永久丢失, 彻底退出防整局卡死
                        print(f'[DROP] HARD-EXIT after {drop_elapsed:.1f}s '
                              f'(S never received, failsafe)', flush=True)
                        det_logger.info('DROP_HARD_EXIT %.1fs (failsafe)', drop_elapsed)
                        self.comm.drop_recovery = False
                        self._drop_start_time = 0.0
                        self._reset_detection_state()
                        # B4修复: 显式收尾发X + continue, 不再靠本帧继续跑掉台分支"碰巧"只发X
                        if self.use_uart:
                            self.comm.force_send_now()
                            self.comm.send_target('X')
                        self._update_fps()
                        frame_idx += 1
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

                    # C2-F1修复(2026-05-29, 烛对抗复核): ratio持续低于LOW(机器人已稳定在
                    # 台面中央)→自主退出drop, 不死等STM32的'S'。覆盖"S被噪声吞但已实际上台"场景,
                    # 避免最长90s硬超时前只发G/X的瞎眼(F1: 不把视觉安全押在未落地的STM32重发S假设)。
                    stable_exit_s = getattr(config, 'DROP_STABLE_EXIT_S', 10.0)
                    if ratio < th_low:
                        if getattr(self, '_drop_low_start', 0.0) <= 0:
                            self._drop_low_start = time.time()
                        low_dur = time.time() - self._drop_low_start
                    else:
                        self._drop_low_start = 0.0
                        low_dur = 0.0
                    if low_dur >= stable_exit_s:
                        print(f'[DROP] STABLE-EXIT: ratio<LOW {low_dur:.1f}s '
                              f'(recovered, no S needed)', flush=True)
                        det_logger.info('DROP_STABLE_EXIT %.1fs', low_dur)
                        self.comm.drop_recovery = False
                        self._drop_start_time = 0.0
                        self._drop_low_start = 0.0
                        self._reset_detection_state()
                        if self.use_uart:
                            self.comm.force_send_now()
                            self.comm.send_target('X')
                        self._update_fps()
                        frame_idx += 1
                        self._sleep_until(next_frame)
                        next_frame += frame_dt
                        continue

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
                        self.comm.force_send_now()  # 强制本次立即发送
                        self.comm.send_target('X')
                    t_uart = time.perf_counter()

                    self._update_fps()
                    frame_idx += 1
                    dt_ms = (time.perf_counter() - t0) * 1000

                    # 回声状态
                    echo_ok = self.comm.echo_confirmed
                    echo_lag = self.comm.echo_latency_ms

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
                            self.comm.echo_count, echo_lag,
                            drop_elapsed, drop_timeout)

                    # 终端输出 (v2: 时间节流 + 换行, 修 journalctl blob)
                    now_print = time.time()
                    if now_print - self._last_status_print_ts >= self._status_print_interval:
                        status = f'GO[{g_status}]' if self._drop_sending_G else 'wait'
                        echo_str = (f'E:{self.comm.echo_type} {echo_lag:.0f}ms'
                                    if echo_ok else 'E:--')
                        if not self.comm.echo_healthy and self.comm.echo_count > 0:
                            echo_str = 'E:LOST!'
                        print(f'[{self._fps:5.1f}fps {dt_ms:4.1f}ms] '
                              f'DROP {ratio_pct}% '
                              f'cfm={self._drop_confirm_count}/{confirm_n} '
                              f'dir={bdir:+.2f} {status} {echo_str} '
                              f'[{drop_elapsed:.0f}/{drop_timeout:.0f}s]',
                              flush=True)
                        self._last_status_print_ts = now_print

                    # 流媒体: 标注黑色检测结果 (含迟滞状态)
                    if self.stream and self._stream_server and frame_idx % self._stream_every_n == 0:
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
                elif getattr(config, 'TAG_PRIMARY', True):
                    # ── Tag-Primary: Tag + 己方色HSV(仅友方回避) ──
                    # STM32 只处理 F(后退), E/N/X 不区分
                    # 视觉唯一任务: 检测己方块 → 发 F
                    # Tag: 任何可见 Tag → 精确判断类型
                    # HSV: 仅检测己方颜色 → 补充 Tag 角度盲区
                    own_targets = self.detector.detect_own(
                        frame, self.comm.my_color)
                    n_own = len(own_targets)
                    pt = own_targets[0] if own_targets else None

                    if self.tag_detector is not None:
                        tags = self.tag_detector.detect_tags(frame)
                        tags.sort(key=lambda t: int(t.get('area', 0)),
                                  reverse=True)
                        self._last_tags = tags
                        if tags:
                            # 己方(F) Tag 优先: 回避=安全优先, 防撞己方犯规;
                            # 无己方 Tag 才退回最大面积 Tag (2026-05-29)。
                            # tags 已按面积降序, own_tags[0] 即最大的己方 Tag。
                            own_tags = [
                                tg for tg in tags
                                if TagDetector.classify_tag(
                                    tg['id'], self.comm.my_color) == 'F']
                            tag_standalone = (own_tags[0] if own_tags
                                              else tags[0])
                else:
                    # ── Legacy: HSV+Tag 融合 (TAG_PRIMARY=False) ──
                    tag_standalone = None

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
                        is_tag_scan_frame = (
                            frame_idx % self._tag_interval == 0)
                        color_targets = own_targets if own_only else targets
                        if color_targets:
                            top_targets = sorted(
                                color_targets, key=lambda t: t.area,
                                reverse=True)[:2]
                            roi_tags = self.tag_detector.detect_tags_in_rois(
                                frame, top_targets)
                        else:
                            roi_tags = []
                        if is_tag_scan_frame:
                            full_tags = self.tag_detector.detect_tags(frame)
                            roi_ids = {t.get('id') for t in roi_tags}
                            for ft in full_tags:
                                if ft.get('id') not in roi_ids:
                                    roi_tags.append(ft)
                            tags = roi_tags
                            self._last_tags = tags
                        elif roi_tags:
                            tags = roi_tags + [
                                t for t in self._last_tags
                                if t.get('id') not in
                                   {r.get('id') for r in roi_tags}]
                            self._last_tags = tags
                        else:
                            tags = self._last_tags
                        if tags and pt:
                            best = min(
                                tags,
                                key=lambda tg: ((tg['cx'] - pt.cx)**2
                                                + (tg['cy'] - pt.cy)**2))
                            d = ((best['cx'] - pt.cx)**2
                                 + (best['cy'] - pt.cy)**2) ** 0.5
                            if d < match_dist:
                                tag_type_override = TagDetector.classify_tag(
                                    best['id'], self.comm.my_color)
                        if tags:
                            edge_m = getattr(config, 'ORPHAN_TAG_EDGE_MARGIN', 60)
                            orphan_ref = color_targets if color_targets else []
                            for tg in tags:
                                orphan = True
                                for ct in orphan_ref:
                                    od = ((tg['cx'] - ct.cx)**2
                                          + (tg['cy'] - ct.cy)**2) ** 0.5
                                    if od < match_dist:
                                        orphan = False
                                        break
                                if orphan:
                                    tcx, tcy = tg['cx'], tg['cy']
                                    if (tcx < edge_m or
                                            tcx > config.CAMERA_WIDTH - edge_m or
                                            tcy < edge_m or
                                            tcy > config.CAMERA_HEIGHT - edge_m):
                                        continue
                                    tag_standalone = tg
                                    break

                # TAHSV: feed detected Tags to adaptive HSV calibrator
                if self._adaptive_hsv and tags:
                    for tg in tags:
                        self._adaptive_hsv.feed_tag(frame, tg, self.comm.my_color)

                t_det = time.perf_counter()

                # 5. 发送 [计时: uart]
                # E1: 目标类型变化 X→非X 时强制立即发送 (跳过频率限制)
                _has_target = bool(
                    (collect_mode and tags) or
                    tag_type_override or
                    tag_standalone or
                    (own_only and own_targets) or
                    (not own_only and (enemies or neutrals or friends))
                )
                if _has_target and self._last_sent_type == 'X':
                    self.comm.force_send_now()
                self._last_sent_type = 'T' if _has_target else 'X'

                # B1修复(2026-05-29): TAG_PRIMARY下"己方避让"(Tag-F或HSV-F)安全最高优先。
                # 防"敌方Tag被识别 + 己方块仅HSV可见(apriltag没认出)"时, 下面 tag_standalone
                # (非己方)抢先发E、跳过HSV-F避让兜底 → 撞己方块犯规。己方色HSV块够大即优先发F。
                # (己方Tag已会发F, 故仅在 tag_standalone 非己方时才用HSV兜底)
                primary_hsv_friend = None
                if (getattr(config, 'TAG_PRIMARY', True) and not collect_mode
                        and own_targets):
                    if self.comm.my_color == 'y':
                        _min_fa = getattr(config, 'FRIEND_ALERT_AREA_YELLOW', 6000)
                    else:
                        _min_fa = getattr(config, 'FRIEND_ALERT_AREA', 6000)
                    ts_is_own = (
                        tag_standalone is not None
                        and TagDetector.classify_tag(
                            tag_standalone['id'], self.comm.my_color) == 'F')
                    if own_targets[0].area >= _min_fa and not ts_is_own:
                        primary_hsv_friend = own_targets[0]

                # 优先级: Tag分类 > 颜色分类 (Tag是地面真值, 颜色可能误判)
                if collect_mode:
                    if tags:
                        self._send_tag_target(tags[0])
                        self._holdover_target = None
                    else:
                        self.comm.send_target('X')
                elif tag_type_override and pt:
                    self.comm.send_target(tag_type_override,
                                          pt.cx, pt.cy, pt.area, pt.direction)
                    self._holdover_target = (tag_type_override, pt.cx, pt.cy, pt.area, pt.direction)
                    self._holdover_frames = 0
                elif primary_hsv_friend is not None:
                    # B1: 己方HSV块避让优先于非己方Tag (安全优先, 防撞己方犯规)
                    t = primary_hsv_friend
                    self.comm.send_target('F', t.cx, t.cy, t.area, t.direction)
                    self._holdover_target = ('F', t.cx, t.cy, t.area, t.direction)
                    self._holdover_frames = 0
                elif tag_standalone:
                    self._send_tag_target(tag_standalone)
                    self._holdover_target = None
                elif getattr(config, 'TAG_PRIMARY', True):
                    # Tag-Primary: HSV 仅用于友方回避
                    # STM32 只处理 F, 其他类型无意义
                    # 黄色队用更高门槛 (地板暖色调容易误检为黄色)
                    if self.comm.my_color == 'y':
                        min_fa = getattr(config, 'FRIEND_ALERT_AREA_YELLOW', 6000)
                    else:
                        min_fa = getattr(config, 'FRIEND_ALERT_AREA', 6000)
                    if own_targets and own_targets[0].area >= min_fa:
                        t = own_targets[0]
                        self.comm.send_target('F', t.cx, t.cy,
                                              t.area, t.direction)
                        self._holdover_target = ('F', t.cx, t.cy,
                                                 t.area, t.direction)
                        self._holdover_frames = 0
                    elif self._holdover_target and self._holdover_frames < self._holdover_max:
                        ht, hcx, hcy, ha, hd = self._holdover_target
                        ha = int(ha * self._holdover_decay)
                        self._holdover_target = (ht, hcx, hcy, ha, hd)
                        self._holdover_frames += 1
                        self.comm.send_target(ht, hcx, hcy, ha, hd)
                    else:
                        self._holdover_target = None
                        self.comm.send_target('X')
                elif own_only:
                    # Legacy HSV-primary (TAG_PRIMARY=False)
                    if own_targets:
                        self.comm.send_own_detection(own_targets)
                        t0t = own_targets[0]
                        self._holdover_target = ('F', t0t.cx, t0t.cy, t0t.area, t0t.direction)
                        self._holdover_frames = 0
                    elif self._holdover_target and self._holdover_frames < self._holdover_max:
                        ht, hcx, hcy, ha, hd = self._holdover_target
                        ha = int(ha * self._holdover_decay)
                        self._holdover_target = (ht, hcx, hcy, ha, hd)
                        self._holdover_frames += 1
                        self.comm.send_target(ht, hcx, hcy, ha, hd)
                    else:
                        self._holdover_target = None
                        self.comm.send_own_detection(own_targets)
                else:
                    # Legacy HSV-primary dual-color (TAG_PRIMARY=False)
                    if enemies or neutrals or friends:
                        self.comm.send_from_detection(friends, enemies, neutrals)
                        min_fa = getattr(config, 'FRIEND_ALERT_AREA', 5000)
                        if friends and friends[0].area >= min_fa:
                            bt = friends[0]
                            bt_type = 'F'
                        elif getattr(config, 'PRIORITY_MODE', 'collect') != 'collect':
                            bt = (enemies or neutrals)[0] if (enemies or neutrals) else friends[0]
                            bt_type = 'E' if enemies else ('N' if neutrals else 'F')
                        else:
                            bt = (neutrals or enemies)[0] if (neutrals or enemies) else friends[0]
                            bt_type = 'N' if neutrals else ('E' if enemies else 'F')
                        self._holdover_target = (bt_type, bt.cx, bt.cy, bt.area, bt.direction)
                        self._holdover_frames = 0
                    elif self._holdover_target and self._holdover_frames < self._holdover_max:
                        ht, hcx, hcy, ha, hd = self._holdover_target
                        ha = int(ha * self._holdover_decay)
                        self._holdover_target = (ht, hcx, hcy, ha, hd)
                        self._holdover_frames += 1
                        self.comm.send_target(ht, hcx, hcy, ha, hd)
                    else:
                        self._holdover_target = None
                        self.comm.send_from_detection(friends, enemies, neutrals)
                t_uart = time.perf_counter()

                self._tx_count += 1

                # 6. FPS
                self._update_fps()
                frame_idx += 1
                dt_ms = (time.perf_counter() - t0) * 1000

                # 7. 检测日志
                if self._holdover_frames > 0 and self._holdover_target:
                    ht, hcx, hcy, ha, hd = self._holdover_target
                    det_logger.info(
                        'HOLD %s cx=%d dir=%+.2f area=%d f=%d/%d',
                        ht, hcx, hd, ha,
                        self._holdover_frames, self._holdover_max)
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
                            'sent=%s E=%d N=%d F=%d%s %.0fms',
                            pt.cx, pt.cy, int(pt.area), pt.direction,
                            self.comm.last_sent_type,
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

                # 8. 终端状态输出 (v2: 时间节流 + 换行, 修 journalctl blob)
                now_print = time.time()
                if now_print - self._last_status_print_ts >= self._status_print_interval:
                    uart_s = 'OK' if (self.comm.ser
                                      and self.comm.tx_errors == 0) else 'ERR'
                    if own_only:
                        pstr = ''
                        if pt:
                            pstr = (f' >> F({pt.cx},{pt.cy} '
                                    f'dir={pt.direction:+.2f})')
                        print(f'[{self._fps:5.1f}fps {dt_ms:4.1f}ms] '
                              f'UART:{uart_s} TX:{self._tx_count} '
                              f'OWN:{n_own}{pstr}', flush=True)
                    else:
                        tag_s = ''
                        if tag_type_override:
                            tag_s = f' T:{tag_type_override}'
                        elif tag_standalone:
                            tag_s = f' T:id{tag_standalone["id"]}'
                        print(f'[{self._fps:5.1f}fps {dt_ms:4.1f}ms] '
                              f'UART:{uart_s} TX:{self._tx_count} '
                              f'SENT:{self.comm.last_sent_type} '
                              f'E:{len(enemies)} N:{len(neutrals)} '
                              f'F:{len(friends)}{tag_s}', flush=True)
                    self._last_status_print_ts = now_print

                # 9. 流媒体 [计时: anno + stream]
                t_anno_start = time.perf_counter()
                t_anno_end = t_anno_start
                if self.stream and self._stream_server and frame_idx % self._stream_every_n == 0:
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

                # 12. 帧率限速 (防漂移: 处理超时时跳帧而非累积延迟)
                self._sleep_until(next_frame)
                next_frame += frame_dt
                now_pc = time.perf_counter()
                if next_frame < now_pc - frame_dt:
                    next_frame = now_pc  # 丢弃落后的帧时隙

        except KeyboardInterrupt:
            print('\nStopping...')
        finally:
            if self._thermal_guard:
                self._thermal_guard.stop()
            self.camera.release()
            self.comm.close()
            det_logger.info('=== Vision stopped ===')
            print('Vision system stopped.')

    def _clear_tracker(self):
        """清空 Tracker 旧轨迹 (掉台进出 / 模式切换时调用)"""
        tr = getattr(self.detector, '_tracker', None)
        if tr:
            tr.clear()
        self.detector._last_max_area = 0

    def _reset_detection_state(self):
        """B2: 统一重置检测状态 — 模式切换/掉台进出/相机重连时调用"""
        self._drop_sending_G = False
        self._drop_confirm_count = 0
        self._drop_low_start = 0.0  # C2-F1: 稳定退出计时(ratio持续低于LOW的起点)
        self.detector.reset_drop_ema()
        self._last_tags = []
        self._clear_tracker()
        self._last_sent_type = 'X'
        self._holdover_target = None
        self._holdover_frames = 0

    def _on_camera_reconnect(self):
        """相机重连回调: 清除 Tracker 旧轨迹 + 重置 EMA + 恢复动态 WB"""
        self._clear_tracker()
        self._last_tags = []
        # 重连后恢复当前颜色对应的 WB (运行中可能已切换)
        if self.camera.cap and config.AUTO_WB == 0:
            self.camera.cap.set(cv2.CAP_PROP_WB_TEMPERATURE,
                                config.WB_TEMPERATURE)
        print(f'[CAMERA] Reconnected, Tracker cleared, WB={config.WB_TEMPERATURE}K')

    def _send_tag_target(self, tag):
        """将 Tag 检测结果分类并发送给 STM32。

        Tag 是 ground truth (apriltag decision_margin 已过滤误检),
        识别到即按类型发送, 不再设面积门槛 (2026-05-29 修复 F2:
        原先复用颜色块的 FRIEND_ALERT_AREA=6000 过滤 Tag 角点面积 tw*th,
        量纲错配导致稍远的己方 Tag 被降级为 X, F 永远发不出去)。
        """
        t_type = TagDetector.classify_tag(tag['id'], self.comm.my_color)
        direction = (tag['cx'] - config.CAMERA_WIDTH / 2) / (config.CAMERA_WIDTH / 2)
        if getattr(config, 'DIRECTION_FLIP', False):
            direction = -direction
        area = tag.get('area', 0)
        self.comm.send_target(t_type, tag['cx'], tag['cy'], area, direction)
        return t_type

    def _handle_command(self, cmd):
        """处理 STM32 单字节指令, 返回日志后缀字符串"""
        extra = ''
        if cmd == 'N':
            self._drop_start_time = 0.0
            self._reset_detection_state()
            self.comm.active = True
            self.comm.drop_recovery = False
            extra = ' → RESET TO NORMAL DETECT'
        elif cmd == 'D':
            self._drop_start_time = time.time()
            self._reset_detection_state()
            extra = ' → DROP RECOVERY MODE'
        elif cmd in ('s', 'S'):
            self._drop_start_time = 0.0
            self._reset_detection_state()
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

        if not self.comm.echo_enabled:
            # 未启用
            cv2.circle(frame, (15, bar_y + 5), 6, (128, 128, 128), -1)
            cv2.putText(frame, 'ECHO OFF', (28, bar_y + 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (128, 128, 128), 1)
            return frame

        echo_ok = self.comm.echo_confirmed
        echo_healthy = self.comm.echo_healthy
        echo_count = self.comm.echo_count
        lag_ms = self.comm.echo_latency_ms

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
                        help='Disable MJPEG stream (env VISION_STREAM=1 overrides)')
    parser.add_argument('--stream-port', type=int, default=8080,
                        help='MJPEG stream port (default: 8080)')
    parser.add_argument('--tag-backend', choices=['qr', 'aruco', 'apriltag'],
                        default=None,
                        help='Enable tag detection (qr/aruco/apriltag)')
    parser.add_argument('--mode', choices=['fight', 'collect'],
                        default='fight',
                        help='Initial mode: fight(color) or collect(tag)')
    parser.add_argument('--backend', choices=['color', 'npu', 'onnx', 'fused'],
                        default=os.environ.get('VISION_BACKEND', 'color'),
                        help='Detection backend: color=HSV (default), '
                             'npu=YOLOv5 .nb on VIP9000, '
                             'onnx=YOLOv5 ONNX CPU 推理 (过渡方案)')
    parser.add_argument('--npu-model', default='/home/radxa/models/best.nb',
                        help='Path to .nb model when --backend=npu')
    parser.add_argument('--onnx-model', default='/home/radxa/models/best_320_int8.onnx',
                        help='Path to .onnx model when --backend=onnx')
    parser.add_argument('--onnx-conf', type=float, default=0.4,
                        help='ONNX backend confidence threshold')
    parser.add_argument('--onnx-iou', type=float, default=0.45,
                        help='ONNX backend NMS IoU threshold')
    args = parser.parse_args()

    if args.priority_mode:
        config.PRIORITY_MODE = args.priority_mode

    # 根据队伍颜色自动设置 WB (除非环境变量已覆盖)
    if config.WB_TEMPERATURE == 0:
        config.WB_TEMPERATURE = (config.WB_YELLOW if args.color == 'y'
                                 else config.WB_BLUE)
        print(f'[AUTO-WB] color={args.color} → WB={config.WB_TEMPERATURE}K')

    # v2 (B4): 启动时打印一次完整配置快照, 便于赛场快速诊断
    if hasattr(config, 'snapshot'):
        try:
            config.snapshot()
        except Exception as e:
            print(f'[WARN] config.snapshot() failed: {e}')

    # VISION_STREAM=1 env var overrides --no-stream (runtime toggle)
    use_stream = os.environ.get('VISION_STREAM', '') == '1' or not args.no_stream
    vision = VisionSystem(
        debug=args.debug,
        calibrate=args.calibrate,
        use_uart=not args.no_uart,
        stream=use_stream,
        stream_port=args.stream_port,
        tag_backend=args.tag_backend,
    )
    vision.comm.my_color = args.color
    vision.comm.mode = args.mode

    # P2 紧急覆盖: VISION_BACKEND_OVERRIDE=hsv 强制走 HSV-only (不初始化 ONNX)
    # 优先级最高 - 用于赛场一键回退, 通过 systemctl edit 注入
    backend_override = getattr(config, 'VISION_BACKEND_OVERRIDE', '').lower()
    if backend_override == 'hsv':
        print('[backend] *** EMERGENCY OVERRIDE: VISION_BACKEND_OVERRIDE=hsv ***')
        print('[backend] forcing HSV-only (ONNX/Fused 跳过初始化)')
        args.backend = 'color'

    # Backend 切换 (lane D 集成 + ONNX 过渡方案 + Fused 融合方案)
    if args.backend == 'fused':
        try:
            from fused_detector import FusedDetector  # noqa: E402
            print(f'[backend] FUSED (HSV + ONNX + Tag 加权融合) enabled')
            vision.detector = FusedDetector(
                onnx_model_path=args.onnx_model,
                onnx_conf_thresh=args.onnx_conf,
                onnx_iou_thresh=args.onnx_iou,
            )
        except Exception as e:
            print(f'[backend] fused init failed ({e}), falling back to color')
    elif args.backend in ('npu', 'onnx'):
        try:
            # 优先从 tools/deploy 导入 (PC 开发); 板端把 *_inference.py 拷到 sys.path
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools', 'deploy'))
            sys.path.insert(0, '/home/radxa/tools_deploy')
            if args.backend == 'npu':
                from viplite_inference import NpuDetector  # noqa: E402
                print(f'[backend] NPU enabled, model={args.npu_model}')
                vision.detector = NpuDetector(model_path=args.npu_model)
            else:  # onnx
                # 优先本目录的 onnx_detector (高精度版, ColorDetector 子类, 完整兼容)
                # fallback 到 tools/deploy/onnx_inference (过渡方案, NpuTarget 接口)
                try:
                    from onnx_detector import OnnxDetector  # vision_upload/
                    print(f'[backend] ONNX (CPU INT8) enabled, '
                          f'model={args.onnx_model}, conf={args.onnx_conf}')
                    vision.detector = OnnxDetector(
                        model_path=args.onnx_model,
                        conf_thresh=args.onnx_conf,
                        iou_thresh=args.onnx_iou,
                        enable_tracking=True,
                    )
                except ImportError:
                    from onnx_inference import OnnxDetector  # tools/deploy/
                    print(f'[backend] ONNX (legacy) enabled, model={args.onnx_model}')
                    vision.detector = OnnxDetector(
                        model_path=args.onnx_model,
                        conf_thresh=args.onnx_conf,
                        iou_thresh=args.onnx_iou,
                    )
        except ImportError as e:
            print(f'[backend] {args.backend} import failed ({e}), falling back to color')
        except Exception as e:
            print(f'[backend] {args.backend} init failed ({e}), falling back to color')

    # P1: 启动 ThermalGuard (需要在 detector 替换后 + run() 启动前)
    # ColorDetector 没有 set_throttled, guard 只监控不动作 (FusedDetector 完整支持)
    try:
        from thermal_guard import ThermalGuard  # noqa: E402
        vision._thermal_guard = ThermalGuard(vision.detector)
        vision._thermal_guard.start()
    except Exception as e:
        print(f'[thermal_guard] init failed ({e}), 跳过温度守护', flush=True)

    vision.run()


if __name__ == '__main__':
    main()
