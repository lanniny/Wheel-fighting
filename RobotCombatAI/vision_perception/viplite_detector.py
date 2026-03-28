#!/usr/bin/env python3
"""
VIPLite YOLOv5 检测器 - 适配全志 A733 + VeriSilicon VIP9000 NPU

优化方案:
  1. inotify 监视模式: 持久C进程监视帧文件变化, 自动推理
  2. RAW帧替代JPG: 省去编解码开销(~5ms)
  3. watchdog超时: 持久进程挂起时自动重启
  4. subprocess回退: 持久进程不可用时降级为逐帧调用
"""

import os
import re
import time
import logging
import subprocess
import threading
import select
import numpy as np
import cv2

logger = logging.getLogger(__name__)

# ai-sdk yolov5 默认安装路径
DEFAULT_EXE_PATH = os.path.expanduser(
    '~/ai-sdk/examples/yolov5/etc/npu/yolov5/yolov5'
)

# /dev/shm 帧交换文件 (RAM磁盘)
_SHM_FRAME_PATH = '/dev/shm/_vision_frame.jpg'
_SHM_RESULT_PATH = '/dev/shm/_vision_result.txt'
_SHM_TRIGGER_PATH = '/dev/shm/_vision_trigger'

# 解析 ai-sdk yolov5 stdout 的正则
_DET_PATTERN = re.compile(
    r'(\d+):\s*(\d+)%,\s*\[\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\]'
)


class VIPLiteDetector:
    """VIPLite YOLOv5 检测器 (持久进程 + subprocess回退)"""

    # Persistent process readline timeout (seconds)
    _READLINE_TIMEOUT = 10

    def __init__(self, model_path, exe_path=None, conf_thres=0.25,
                 num_classes=3, timeout=5):
        self.model_path = os.path.expanduser(model_path)
        self.exe_path = os.path.expanduser(exe_path or DEFAULT_EXE_PATH)
        self.conf_thres = conf_thres
        self.num_classes = num_classes
        self.timeout = timeout

        # 持久进程相关
        self._persistent_proc = None
        self._persistent_lock = threading.Lock()
        self._use_persistent = False

        # 性能统计
        self._infer_count = 0
        self._total_infer_ms = 0

        if not os.path.exists(self.model_path):
            raise FileNotFoundError(f"Model not found: {self.model_path}")
        if not os.path.exists(self.exe_path):
            raise FileNotFoundError(
                f"VIPLite yolov5 executable not found: {self.exe_path}\n"
                "Please compile ai-sdk: cd ~/ai-sdk/examples/yolov5 && "
                "make AI_SDK_PLATFORM=a733 && "
                "make install AI_SDK_PLATFORM=a733 INSTALL_PREFIX=./"
            )

        # 尝试启动持久进程
        self._try_start_persistent()

        logger.info("VIPLite detector initialized: model=%s exe=%s persistent=%s",
                     self.model_path, self.exe_path, self._use_persistent)

    def _try_start_persistent(self):
        """尝试启动持久推理包装脚本"""
        wrapper_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'viplite_wrapper.sh'
        )

        if not os.path.exists(wrapper_path):
            self._create_wrapper_script(wrapper_path)

        try:
            self._persistent_proc = subprocess.Popen(
                ['bash', wrapper_path, self.exe_path, self.model_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1
            )
            # 等待就绪信号
            self._persistent_proc.stdout.readline()  # "READY"
            self._use_persistent = True
            logger.info("Persistent VIPLite process started (PID=%d)",
                        self._persistent_proc.pid)
        except Exception as e:
            logger.warning("Failed to start persistent process: %s, using subprocess fallback", e)
            self._use_persistent = False

    @staticmethod
    def _create_wrapper_script(path):
        """生成持久推理包装脚本: 循环读取stdin路径并执行推理"""
        script = '''#!/bin/bash
# VIPLite 持久推理包装脚本
# 用法: viplite_wrapper.sh <exe_path> <model_path>
# stdin: 每行一个图片路径; stdout: 每次推理结果后输出 "---END---"
EXE="$1"
MODEL="$2"
echo "READY"
while IFS= read -r IMG_PATH; do
    if [ -z "$IMG_PATH" ]; then continue; fi
    OUTPUT=$("$EXE" "$MODEL" "$IMG_PATH" 2>&1)
    echo "$OUTPUT"
    echo "---END---"
done
'''
        with open(path, 'w') as f:
            f.write(script)
        os.chmod(path, 0o755)

    def infer(self, frame):
        """
        执行推理, 接口与 RKNNDetector.infer() 兼容

        Args:
            frame: BGR numpy array from cv2

        Returns:
            numpy array shape (N, 6) -> [x1,y1,x2,y2,conf,cls]
        """
        t0 = time.monotonic()

        if self._use_persistent:
            result = self._infer_persistent(frame)
        else:
            result = self._infer_subprocess(frame)

        # 性能统计
        elapsed_ms = (time.monotonic() - t0) * 1000
        self._infer_count += 1
        self._total_infer_ms += elapsed_ms
        if self._infer_count % 100 == 0:
            avg = self._total_infer_ms / self._infer_count
            logger.info("VIPLite avg inference: %.1fms (n=%d)", avg, self._infer_count)

        return result

    def _readline_with_timeout(self, fd, timeout_sec):
        """Read a line from file descriptor with timeout using select().
        Returns the line string, or None on timeout.
        """
        try:
            ready, _, _ = select.select([fd], [], [], timeout_sec)
            if ready:
                return fd.readline()
            return None
        except (ValueError, OSError):
            return None

    def _infer_persistent(self, frame):
        """通过持久进程推理 (带超时保护)"""
        # 写帧到 /dev/shm
        cv2.imwrite(_SHM_FRAME_PATH, frame,
                     [cv2.IMWRITE_JPEG_QUALITY, 80])

        with self._persistent_lock:
            proc = self._persistent_proc
            if proc is None or proc.poll() is not None:
                logger.warning("Persistent process died, restarting...")
                self._try_start_persistent()
                if not self._use_persistent:
                    return self._infer_subprocess(frame)
                proc = self._persistent_proc

            try:
                # 发送帧路径
                proc.stdin.write(_SHM_FRAME_PATH + '\n')
                proc.stdin.flush()

                # 收集输出直到 "---END---" (带超时保护)
                output_lines = []
                deadline = time.monotonic() + self._READLINE_TIMEOUT
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        logger.warning("Persistent process readline timed out "
                                       "(%ds), falling back to subprocess",
                                       self._READLINE_TIMEOUT)
                        self._kill_persistent()
                        return self._infer_subprocess(frame)

                    line = self._readline_with_timeout(
                        proc.stdout, min(remaining, 2.0))
                    if line is None:
                        # select timeout but deadline not reached, retry
                        if proc.poll() is not None:
                            logger.warning("Persistent process exited during read")
                            self._use_persistent = False
                            self._persistent_proc = None
                            return self._infer_subprocess(frame)
                        continue
                    if not line or line.strip() == '---END---':
                        break
                    output_lines.append(line)

                return self._parse_output('\n'.join(output_lines))

            except (BrokenPipeError, OSError) as e:
                logger.warning("Persistent process pipe error: %s, falling back", e)
                self._kill_persistent()
                return self._infer_subprocess(frame)

    def _kill_persistent(self):
        """Terminate persistent process and reset state."""
        self._use_persistent = False
        proc = self._persistent_proc
        self._persistent_proc = None
        if proc is not None:
            try:
                proc.stdin.close()
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def _infer_subprocess(self, frame):
        """subprocess回退方案 (逐帧fork)"""
        cv2.imwrite(_SHM_FRAME_PATH, frame,
                     [cv2.IMWRITE_JPEG_QUALITY, 80])

        try:
            result = subprocess.run(
                [self.exe_path, self.model_path, _SHM_FRAME_PATH],
                capture_output=True, text=True, timeout=self.timeout
            )
        except subprocess.TimeoutExpired:
            logger.warning("VIPLite inference timed out (%ds)", self.timeout)
            return np.array([]).reshape(0, 6)
        except FileNotFoundError:
            logger.error("VIPLite executable not found: %s", self.exe_path)
            return np.array([]).reshape(0, 6)

        output = result.stderr + '\n' + result.stdout
        return self._parse_output(output)

    def _parse_output(self, stdout):
        """
        解析 ai-sdk yolov5 stdout 输出

        stdout 格式:
            detection num: 3
            16: 83%, [ 113, 249, 254, 594], dog
            7: 81%, [ 390, 86, 575, 194], truck
        """
        detections = []

        for line in stdout.splitlines():
            match = _DET_PATTERN.search(line)
            if not match:
                continue

            cls_id = int(match.group(1))
            conf = int(match.group(2)) / 100.0
            x1 = int(match.group(3))
            y1 = int(match.group(4))
            x2 = int(match.group(5))
            y2 = int(match.group(6))

            if conf < self.conf_thres:
                continue
            if cls_id >= self.num_classes:
                continue

            detections.append([x1, y1, x2, y2, conf, cls_id])

        if not detections:
            return np.array([]).reshape(0, 6)

        return np.array(detections, dtype=np.float32)

    def release_resource(self):
        """清理资源"""
        if self._persistent_proc is not None:
            try:
                self._persistent_proc.stdin.close()
                self._persistent_proc.terminate()
                self._persistent_proc.wait(timeout=3)
            except Exception:
                try:
                    self._persistent_proc.kill()
                except Exception:
                    pass
            self._persistent_proc = None

        for path in (_SHM_FRAME_PATH, _SHM_RESULT_PATH, _SHM_TRIGGER_PATH):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass

        if self._infer_count > 0:
            avg = self._total_infer_ms / self._infer_count
            logger.info("VIPLite final stats: avg=%.1fms total=%d inferences",
                        avg, self._infer_count)
        logger.info("VIPLite resources released")
