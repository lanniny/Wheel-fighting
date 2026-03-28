#!/usr/bin/env python3
"""
实时视觉推理管线 - 机器人格斗竞赛
摄像头采集 -> YOLOv5检测 -> 目标选择 -> UART输出
"""

import os
import sys
import time
import signal
import argparse
import logging
import numpy as np

# 添加项目路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '../..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'vision_perception/yolo_detection'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'vision_perception'))

import cv2

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger('vision')

# 全局停止标志
_running = True


def signal_handler(sig, frame):
    global _running
    logger.info("Received signal %d, shutting down...", sig)
    _running = False


class RealtimeVision:
    """实时视觉推理管线"""

    def __init__(self, args):
        self.args = args
        self.cap = None
        self.detector = None
        self.acl_context = None
        self.uart = None
        self.selector = None
        self._cleanup_done = False
        self._frame_failures = 0
        self._max_frame_failures = 10

        try:
            self._init_camera()
            self._init_detector()
            self._init_uart()
            self._init_selector()
        except Exception:
            self.cleanup()
            raise

        self._frame_count = 0
        self._fps_start = time.time()
        self._last_log_time = 0
        self._last_infer_time = 0
        self._min_frame_interval = 1.0 / max(1, self.args.target_fps)
        self._dropped_frames = 0

        # Cached CLAHE object (avoid creating a new one per frame)
        self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4)) \
            if self.args.clahe else None

    @staticmethod
    def _find_camera():
        """自动搜索可用摄像头"""
        import glob
        # 优先搜索 USB 摄像头 (跳过硬件编解码器 video-dec/video-enc)
        candidates = sorted(glob.glob('/dev/video[0-9]*'))
        for dev in candidates:
            cap = cv2.VideoCapture(dev)
            if cap.isOpened():
                ret, _ = cap.read()
                cap.release()
                if ret:
                    logger.info("Auto-detected camera: %s", dev)
                    return dev
        # 回退尝试索引号
        for i in range(5):
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                ret, _ = cap.read()
                cap.release()
                if ret:
                    logger.info("Auto-detected camera index: %d", i)
                    return i
        return None

    def _init_camera(self):
        cam_id = self.args.camera
        if cam_id == 'auto':
            cam_id = self._find_camera()
            if cam_id is None:
                raise RuntimeError("No camera found (auto-search failed)")
        elif cam_id.isdigit():
            cam_id = int(cam_id)

        logger.info("Initializing camera %s...", cam_id)
        if self.cap is not None:
            self.cap.release()
        self.cap = cv2.VideoCapture(cam_id)
        if not self.cap.isOpened():
            raise RuntimeError(f"Cannot open camera: {cam_id}")

        # 设置分辨率
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        logger.info("Camera opened: %dx%d", w, h)

    def _init_detector(self):
        model_path = self.args.model
        if not os.path.isabs(model_path):
            model_path = os.path.join(SCRIPT_DIR, model_path)
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Model not found: {model_path}")

        backend = self.args.backend

        # 自动检测后端
        if backend == 'auto':
            if model_path.endswith('.nb'):
                backend = 'viplite'
            elif model_path.endswith('.rknn'):
                backend = 'rknn'
            elif model_path.endswith('.om'):
                backend = 'acl'
            elif os.path.exists('/dev/vipcore'):
                backend = 'viplite'
            else:
                try:
                    from rknnlite.api import RKNNLite
                    backend = 'rknn'
                except ImportError:
                    backend = 'acl'

        self._backend = backend
        logger.info("Initializing YOLOv5 detector (backend=%s)...", backend)

        if backend == 'viplite':
            from viplite_detector import VIPLiteDetector
            self.detector = VIPLiteDetector(
                model_path,
                exe_path=self.args.viplite_exe,
                conf_thres=self.args.conf_threshold,
                num_classes=3
            )
        elif backend == 'rknn':
            from rknn_detector import RKNNDetector
            self.detector = RKNNDetector(
                model_path,
                conf_thres=self.args.conf_threshold,
                num_classes=3
            )
        elif backend == 'acl':
            from objDet_yolov5 import YoloV5, init_acl, DEVICE_ID
            self.acl_context = init_acl(DEVICE_ID)
            self.detector = YoloV5(model_path)
        else:
            raise ValueError(f"Unknown backend: {backend}")

        logger.info("YOLOv5 model loaded: %s (backend=%s)", model_path, backend)

    def _init_uart(self):
        from uart_comm import UARTComm

        self.uart = UARTComm(
            port=self.args.serial_port,
            baudrate=115200
        )
        self.uart.set_color_callback(self._on_color_change)

        # 设置初始颜色
        if self.args.team_color:
            self.uart.set_team_color(self.args.team_color)

        if self.uart.is_connected:
            logger.info("UART connected: %s", self.args.serial_port)
        else:
            logger.warning("UART not connected, running in dry-run mode")

    def _init_selector(self):
        from target_selector import TargetSelector

        color = self.args.team_color or self.uart.team_color
        w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.selector = TargetSelector(
            team_color=color,
            frame_width=w,
            frame_height=h
        )
        logger.info("Target selector initialized (team=%s)", color)

    def _on_color_change(self, color: str):
        if self.selector:
            self.selector.update_team_color(color)

    def run(self):
        global _running
        logger.info("=== Vision pipeline started (target_fps=%d, clahe=%s) ===",
                    self.args.target_fps, self.args.clahe)

        while _running:
            # 检查STM32颜色命令
            if self.uart:
                self.uart.check_color_command()

            # 帧率控制: 未到推理间隔时仅grab丢弃旧帧
            now = time.time()
            if now - self._last_infer_time < self._min_frame_interval:
                self.cap.grab()
                self._dropped_frames += 1
                continue

            # 采集帧
            ret, frame = self.cap.read()
            if not ret:
                self._frame_failures += 1
                logger.warning("Camera read failed (%d/%d)",
                              self._frame_failures, self._max_frame_failures)
                if self.uart:
                    self.uart.send_no_target()
                if self._frame_failures >= self._max_frame_failures:
                    logger.warning("Too many failures, reinitializing camera...")
                    try:
                        self._init_camera()
                        self._frame_failures = 0
                    except Exception as e:
                        logger.error("Camera reopen failed: %s", e)
                time.sleep(0.1)
                continue
            self._frame_failures = 0
            self._last_infer_time = now

            # CLAHE 光照鲁棒增强
            if self.args.clahe:
                frame = self._apply_clahe(frame)

            # YOLOv5推理
            try:
                pred = self.detector.infer(frame)
            except Exception as e:
                logger.error("Inference error: %s", e)
                if self.uart:
                    self.uart.send_no_target()
                continue

            # 目标选择
            target = self.selector.select(pred)

            # UART发送 (带频率控制)
            if self.uart:
                self.uart.send_target_throttled(
                    target.type, target.cx, target.cy,
                    target.area, target.dir,
                    min_interval_ms=self.args.uart_interval
                )

            # 帧率统计
            self._frame_count += 1
            now = time.time()
            if now - self._last_log_time >= 5.0:
                elapsed = now - self._fps_start
                fps = self._frame_count / elapsed if elapsed > 0 else 0
                logger.info(
                    "FPS: %.1f | Target: %s dir=%+d area=%d | dropped=%d",
                    fps, target.type, target.dir, target.area,
                    self._dropped_frames
                )
                self._last_log_time = now
                self._dropped_frames = 0

            # 可选: 显示预览（调试用）
            if self.args.display:
                self._draw_preview(frame, target, pred)

    def _apply_clahe(self, frame):
        """CLAHE 光照增强 (自适应直方图均衡, 使用缓存的 CLAHE 对象)"""
        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        lab[:, :, 0] = self._clahe.apply(lab[:, :, 0])
        return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

    def _draw_preview(self, frame, target, detections):
        if detections is not None and len(detections) > 0:
            for det in detections:
                x1, y1, x2, y2 = int(det[0]), int(det[1]), int(det[2]), int(det[3])
                conf = float(det[4])
                cls = int(det[5])
                type_ch = self.selector.map_class(cls)
                color = (0, 255, 0) if type_ch == 'N' else \
                        (0, 0, 255) if type_ch == 'E' else (255, 0, 0)
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                cv2.putText(frame, f"{type_ch} {conf:.2f}",
                           (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX,
                           0.5, color, 1)

        # 绘制当前追踪目标
        info = f"Target: {target.type} dir={target.dir:+d}"
        cv2.putText(frame, info, (10, 30),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

        cv2.imshow('Vision', frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            global _running
            _running = False

    def cleanup(self):
        if self._cleanup_done:
            return
        self._cleanup_done = True
        logger.info("Cleaning up resources...")
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        if self.detector is not None:
            try:
                self.detector.release_resource()
            except Exception as e:
                logger.warning("Detector cleanup error: %s", e)
            self.detector = None
        if self.acl_context is not None and getattr(self, '_backend', '') == 'acl':
            try:
                from objDet_yolov5 import deinit_acl, DEVICE_ID
                deinit_acl(self.acl_context, DEVICE_ID)
            except Exception as e:
                logger.warning("ACL cleanup error: %s", e)
            self.acl_context = None
        if self.uart is not None:
            self.uart.close()
            self.uart = None
        cv2.destroyAllWindows()
        logger.info("Cleanup complete")


def main():
    parser = argparse.ArgumentParser(description='Real-time Vision Pipeline')
    parser.add_argument('--camera', default='auto',
                       help='Camera device ID, path, or "auto" to search (default: auto)')
    parser.add_argument('--serial-port', default='/dev/ttyS4',
                       help='UART serial port (default: /dev/ttyS4)')
    parser.add_argument('--model',
                       default='../../models/yolov5.nb',
                       help='Model path (.nb for VIPLite, .rknn, .om)')
    parser.add_argument('--team-color', choices=['b', 'y'], default=None,
                       help='Team color override (b=blue, y=yellow)')
    parser.add_argument('--backend', choices=['auto', 'viplite', 'rknn', 'acl'],
                       default='auto',
                       help='Inference backend (default: auto-detect)')
    parser.add_argument('--viplite-exe', default=None,
                       help='Path to VIPLite yolov5 executable (auto-detect)')
    parser.add_argument('--conf-threshold', type=float, default=0.25,
                       help='Confidence threshold (default: 0.25)')
    parser.add_argument('--target-fps', type=int, default=15,
                       help='Target inference FPS (default: 15)')
    parser.add_argument('--clahe', action='store_true',
                       help='Enable CLAHE lighting robustness')
    parser.add_argument('--uart-interval', type=int, default=50,
                       help='Min UART send interval in ms (default: 50)')
    parser.add_argument('--display', action='store_true',
                       help='Show preview window (debug)')

    args = parser.parse_args()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    vision = None
    try:
        vision = RealtimeVision(args)
        vision.run()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error("Fatal error: %s", e, exc_info=True)
    finally:
        if vision is not None:
            vision.cleanup()


if __name__ == '__main__':
    main()
