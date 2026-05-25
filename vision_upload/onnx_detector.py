"""onnx_detector.py — vision_upload 的 ONNX 高精度后端 (替换 HSV 颜色检测).

设计:
  - 继承 ColorDetector → 自动复用 detect_black_ratio / reset_drop_ema /
    draw_targets / classify / get_priority_target 等 HSV-only 接口
  - override detect() 和 detect_own() 用 onnxruntime 跑 INT8 ONNX 推理
  - 实测 best_320_int8.onnx + ARM CPU 4 线程: ~150ms/帧 (6.6 FPS), 精度 97%
  - 远高于 HSV 颜色检测精度, 但帧率显著下降, 适合精度优先场景

使用方式 (main.py):
  if args.backend == 'onnx':
      from onnx_detector import OnnxDetector
      self.detector = OnnxDetector(model_path=config.ONNX_MODEL_PATH)
  else:
      self.detector = ColorDetector(enable_tracking=True)

类别映射 (data.yaml):
  0: blue_block       → color='blue'
  1: yellow_block     → color='yellow'
  2: neutral_block    → color='white'  (HSV 系统命名)
  3: bomb             → skip (危险, 不进入颜色 pipeline)
  4: apriltag_marker  → skip (由 TagDetector 处理)

性能注意:
  ONNX 6.6 FPS vs HSV ~30 FPS. 如果机器人控制循环依赖高帧率, 建议:
    - 调高 conf_thresh (默认 0.25) 减少 NMS 候选
    - 使用更小输入 (320 vs 640)
    - 或保留 HSV + 仅在关键帧调用 ONNX

v2 改进 (2026-05-21, Codex 评审采纳):
  - A2: 推理统计改 ring buffer (最近 100 帧) + 单帧峰值监控 (_max_infer_ms /
        p95)；避免累积平均掩盖真实卡顿
  - A3: _submit_async 内 frame.copy() 防 V4L2 MMAP BUFFERSIZE=1 数据竞争
  - A4: _async_targets 加时间戳, 超过 ASYNC_TARGET_MAX_AGE_S 后视为失效
        返回 []; 防止 worker 死掉时主循环持续追幽灵目标
"""
import os
import sys
import time
import threading
from pathlib import Path

import cv2
import numpy as np

import config
from detector import ColorDetector, Target, Tracker

try:
    import onnxruntime as ort
except ImportError:
    ort = None


# YOLOv8 类别 → vision_upload 颜色 (HSV 系统命名)
_CLS_TO_COLOR = {
    0: 'blue',     # blue_block
    1: 'yellow',   # yellow_block
    2: 'white',    # neutral_block
    3: None,       # bomb (跳过)
    4: None,       # apriltag_marker (Tag 检测处理)
}


class OnnxDetector(ColorDetector):
    """ONNX 推理后端检测器, 接口完全兼容 ColorDetector."""

    def __init__(self,
                 model_path=None,
                 conf_thresh=None,
                 iou_thresh=0.45,
                 num_threads=None,
                 enable_tracking=True,
                 input_size=None,
                 async_mode=True):
        # 优先级链: 参数 > 环境变量 > config 默认值 (B4 收编)
        # threads 默认 4 (用 2x A76 大核 + 2x A55 小核, async worker 绑核到 A76)
        if num_threads is None:
            num_threads = int(os.environ.get(
                'VISION_ONNX_THREADS',
                str(getattr(config, 'ONNX_THREADS', 4))))
        # 调用父类构造但禁用某些 HSV 状态 (我们不用)
        super().__init__(enable_tracking=enable_tracking)

        if ort is None:
            raise RuntimeError(
                'onnxruntime 未安装, 请: pip install onnxruntime\n'
                '或回退到 HSV: 启动加 --backend hsv')

        # 模型路径优先级: 参数 > 环境变量 > config > 默认
        if model_path is None:
            model_path = os.environ.get('VISION_ONNX_MODEL') \
                or getattr(config, 'ONNX_MODEL_PATH', None) \
                or '/home/radxa/models/best_320_int8.onnx'
        if not Path(model_path).exists():
            raise FileNotFoundError(f'ONNX model not found: {model_path}')

        self.model_path = model_path
        # 优先级链: 参数 > 环境变量 > config 默认值 (B4 收编)
        self.conf_thresh = float(
            conf_thresh if conf_thresh is not None
            else os.environ.get('VISION_ONNX_CONF',
                                getattr(config, 'ONNX_CONF_THRESH', 0.4)))
        self.iou_thresh = iou_thresh

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = num_threads
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.enable_cpu_mem_arena = True
        opts.enable_mem_pattern = True
        self.sess = ort.InferenceSession(
            model_path, opts, providers=['CPUExecutionProvider'])

        inp = self.sess.get_inputs()[0]
        self.input_name = inp.name
        self.input_size = inp.shape[2] if input_size is None else input_size
        out_shape = self.sess.get_outputs()[0].shape
        self.num_anchors = out_shape[2]
        self.output_channels = out_shape[1]  # 4 + nc = 4 + 5 = 9

        # ── 性能统计 (v2: ring buffer + max 峰值) ──
        # 累积平均会被早期/晚期帧拖累, 无法反映真实瞬时性能;
        # ring buffer 维护最近 N 帧, 同时保留 _max_infer_ms 监控热降频
        self._infer_window = int(
            os.environ.get('VISION_ONNX_STATS_WIN',
                           str(getattr(config, 'ONNX_STATS_WINDOW', 100))))
        self._infer_times = []          # ring buffer (deque-like list)
        self._infer_count = 0           # 总计数 (用于日志触发周期)
        self._max_infer_ms = 0.0        # 历史单帧峰值 (检测热降频/卡顿)
        self._max_infer_ms_recent = 0.0 # 近窗口单帧峰值 (滑动)

        # ── 异步推理模式 ──
        # 主循环 detect() 不阻塞, 直接返回最近一次推理结果
        # 后台 worker 不断从最新帧推理, 表观 FPS = 相机帧率, 实际推理延迟 ~150ms
        # 适合机器人对战: STM32 收到 UART 信号的频率高, 检测有 150ms 延迟可接受
        # 优先级: 参数 > env > config 默认 (B4 收编)
        env_async = os.environ.get('VISION_ONNX_ASYNC')
        if env_async is not None:
            self.async_mode = env_async != '0'
        else:
            self.async_mode = bool(async_mode) and bool(
                getattr(config, 'ONNX_ASYNC', True))
        # ASYNC_TARGET_MAX_AGE_S: _async_targets 超过此年龄视为失效, 返回 []
        # 防止 worker 卡死/退出后主循环持续追幽灵目标; 默认 0.5s
        # 单帧推理约 150ms, 0.5s 容忍 3 帧 worker 抖动, 4 帧后视为异常
        # 优先级: env > config 默认 (B4 收编)
        self._async_target_max_age = float(
            os.environ.get(
                'VISION_ONNX_TARGET_MAX_AGE_S',
                str(getattr(config, 'ONNX_TARGET_MAX_AGE_S', 0.5))))
        self._async_frame = None
        self._async_frame_lock = threading.Lock()
        self._async_targets = []
        self._async_targets_ts = 0.0    # 最近一次 worker 完成更新的时间
        self._async_targets_lock = threading.Lock()
        self._async_filter = None  # None | {'blue'} | {'yellow'}
        self._async_my_color = None
        self._async_stop = False
        self._async_worker = None
        # 上次过期警告时间 (避免刷屏)
        self._last_stale_warn = 0.0
        if self.async_mode:
            self._start_async_worker()

        print(f'[onnx_detector] loaded {model_path}, input={self.input_size}, '
              f'threads={num_threads}, conf>={self.conf_thresh}, '
              f'async={self.async_mode}, stats_win={self._infer_window}, '
              f'max_age={self._async_target_max_age:.2f}s',
              flush=True)

    # ------------------------------------------------------------------
    # 异步 worker (默认开启)
    # ------------------------------------------------------------------
    def _start_async_worker(self):
        self._async_worker = threading.Thread(
            target=self._async_loop, daemon=True, name='onnx-infer')
        self._async_worker.start()

    def _pin_to_a76(self):
        """worker 线程内部调用: 绑到 A76 大核 (cpu6,7) 避免与主循环抢 A55."""
        try:
            os.sched_setaffinity(0, {6, 7})
            print('[onnx_detector] worker pinned to cpu{6,7} (A76)', flush=True)
        except Exception as e:
            print(f'[onnx_detector] cpu pin skipped: {e}', flush=True)

    def _async_loop(self):
        self._pin_to_a76()
        while not self._async_stop:
            with self._async_frame_lock:
                frame = self._async_frame
                color_filter = self._async_filter
                self._async_frame = None  # consume
            if frame is None:
                time.sleep(0.003)
                continue
            try:
                targets = self._infer_blocking(frame, color_filter=color_filter)
            except Exception as e:
                print(f'[onnx_detector] async infer error: {e}', flush=True)
                targets = []
            # v2: 同时更新 targets + 完成时间戳, 主循环用时间戳判过期
            now = time.time()
            with self._async_targets_lock:
                self._async_targets = targets
                self._async_targets_ts = now

    def close(self):
        self._async_stop = True
        if self._async_worker is not None:
            self._async_worker.join(timeout=2.0)

    # ------------------------------------------------------------------
    # 主检测接口 (override ColorDetector)
    # ------------------------------------------------------------------
    def detect(self, frame):
        """检测一帧所有目标 (双色 + 中立), 返回 List[Target].
        async_mode=True 时不阻塞, 返回最近一次推理结果."""
        if self.async_mode:
            return self._submit_async(frame, color_filter=None)
        return self._infer_blocking(frame, color_filter=None)

    def detect_own(self, frame, my_color):
        """只检测我方颜色目标.
        my_color: 'b'=蓝, 'y'=黄"""
        target_color = 'blue' if my_color == 'b' else 'yellow'
        if self.async_mode:
            return self._submit_async(frame, color_filter={target_color})
        return self._infer_blocking(frame, color_filter={target_color})

    def _submit_async(self, frame, color_filter):
        """async 路径: 把帧丢给 worker, 立刻返回最近一次结果.

        v2 (Codex 评审 A3+A4 合并修复):
          - A3: frame.copy() 防 V4L2 MMAP BUFFERSIZE=1 数据竞争
                (相机底层会在 worker 推理 150ms 期间覆盖同一块内存)
          - A4: 检查 _async_targets_ts 时间戳, 超过 max_age 视为失效返回 []
                (worker 死亡 / 长时间无新帧 / 推理卡死时, 防止主循环追幽灵目标)
        """
        with self._async_frame_lock:
            # 仅在 worker 消费完上一帧时才更新 (None 表示空闲)
            if self._async_frame is None:
                # A3: 拷贝 frame, 与相机 capture buffer 完全隔离
                self._async_frame = frame.copy()
                self._async_filter = color_filter

        # A4: 检查 targets 新鲜度
        with self._async_targets_lock:
            targets_ts = self._async_targets_ts
            targets_copy = list(self._async_targets)

        if targets_ts <= 0:
            # worker 还没产出过任何结果 (启动初期), 返回空列表
            return []

        age = time.time() - targets_ts
        if age > self._async_target_max_age:
            # 老目标过期 — 偶发警告 (节流, 避免刷屏)
            now = time.time()
            if now - self._last_stale_warn > 5.0:
                print(f'[onnx_detector] WARN async targets stale ({age:.2f}s '
                      f'> {self._async_target_max_age:.2f}s), worker '
                      f'alive={self._async_worker.is_alive() if self._async_worker else "N/A"}',
                      flush=True)
                self._last_stale_warn = now
            return []
        return targets_copy

    # ------------------------------------------------------------------
    # 核心推理 + 后处理 (阻塞)
    # ------------------------------------------------------------------
    def _infer_blocking(self, frame, color_filter=None):
        t0 = time.monotonic()

        # 1) 预处理 (letterbox + BGR→RGB + /255 + NCHW)
        x, ratio, dw, dh = self._preprocess_letterbox(frame)

        # 2) ONNX 推理
        out = self.sess.run(None, {self.input_name: x})[0]  # [1, 4+nc, A]

        # 3) NMS decode → Target 列表
        targets = self._decode_and_nms(
            out[0], ratio, dw, dh, frame.shape[1], frame.shape[0],
            color_filter=color_filter,
        )

        # 4) Tracker 平滑 (帧间稳定 + 速度预测)
        if self._tracker:
            targets = self._tracker.update(targets)

        # 5) 性能记录 (v2: ring buffer + max 峰值, 不再累积平均)
        elapsed_ms = (time.monotonic() - t0) * 1000
        self._infer_count += 1
        self._infer_times.append(elapsed_ms)
        if len(self._infer_times) > self._infer_window:
            self._infer_times.pop(0)
        # 历史峰值 (用于检测罕见卡顿)
        if elapsed_ms > self._max_infer_ms:
            self._max_infer_ms = elapsed_ms

        if self._infer_count % 50 == 0 and self._infer_times:
            n = len(self._infer_times)
            avg = sum(self._infer_times) / n
            recent_max = max(self._infer_times)
            self._max_infer_ms_recent = recent_max
            sorted_t = sorted(self._infer_times)
            p95_idx = max(0, int(n * 0.95) - 1)
            p95 = sorted_t[p95_idx]
            print(f'[onnx_detector] win={n} avg={avg:.0f}ms p95={p95:.0f}ms '
                  f'max={recent_max:.0f}ms ({1000/avg:.1f} FPS), '
                  f'hist_max={self._max_infer_ms:.0f}ms n={self._infer_count}',
                  flush=True)

        return targets

    def _preprocess_letterbox(self, bgr_frame):
        sz = self.input_size
        h0, w0 = bgr_frame.shape[:2]
        ratio = min(sz / w0, sz / h0)
        nw, nh = int(round(w0 * ratio)), int(round(h0 * ratio))
        dw, dh = (sz - nw) // 2, (sz - nh) // 2

        if (nw, nh) != (w0, h0):
            resized = cv2.resize(bgr_frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
        else:
            resized = bgr_frame

        canvas = np.full((sz, sz, 3), 114, dtype=np.uint8)
        canvas[dh:dh + nh, dw:dw + nw] = resized
        rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        x = rgb.transpose(2, 0, 1)[None]  # NCHW
        return x, ratio, dw, dh

    def _decode_and_nms(self, raw, ratio, dw, dh, orig_w, orig_h,
                        color_filter=None):
        """raw shape: [4+nc, anchors]. bbox cxcywh in input pixel coords."""
        bbox = raw[:4]
        cls = raw[4:]

        cls_id = cls.argmax(axis=0)              # [A]
        cls_score = cls.max(axis=0)               # [A]

        keep = cls_score >= self.conf_thresh
        if not keep.any():
            return []

        bbox = bbox[:, keep]
        cls_id = cls_id[keep]
        cls_score = cls_score[keep]

        # cxcywh → xyxy (in input coords)
        x1 = bbox[0] - bbox[2] / 2
        y1 = bbox[1] - bbox[3] / 2
        x2 = bbox[0] + bbox[2] / 2
        y2 = bbox[1] + bbox[3] / 2

        # NMS
        boxes = np.stack([x1, y1, x2 - x1, y2 - y1], axis=1).astype(np.float32).tolist()
        indices = cv2.dnn.NMSBoxes(
            boxes, cls_score.tolist(), self.conf_thresh, self.iou_thresh,
            top_k=100)
        if len(indices) == 0:
            return []
        indices = np.array(indices).flatten()

        # letterbox 反映射 + 转 Target
        out = []
        for i in indices:
            cls = int(cls_id[i])
            color = _CLS_TO_COLOR.get(cls)
            if color is None:
                continue
            if color_filter is not None and color not in color_filter:
                continue

            xx1 = max(0, min(orig_w - 1, (x1[i] - dw) / ratio))
            yy1 = max(0, min(orig_h - 1, (y1[i] - dh) / ratio))
            xx2 = max(0, min(orig_w - 1, (x2[i] - dw) / ratio))
            yy2 = max(0, min(orig_h - 1, (y2[i] - dh) / ratio))

            box_x = int(xx1)
            box_y = int(yy1)
            box_w = int(xx2 - xx1)
            box_h = int(yy2 - yy1)
            cx = int((xx1 + xx2) / 2)
            cy = int((yy1 + yy2) / 2)
            area = float(box_w * box_h)
            t = Target(color=color, cx=cx, cy=cy,
                       x=box_x, y=box_y, w=box_w, h=box_h,
                       area=area, solidity=1.0)
            out.append(t)
        return out

    # ------------------------------------------------------------------
    # classify: ONNX 输出 white(neutral) 时把它放入 neutrals,
    # 与父类不一样 (父类已废弃白色) - 这里恢复 3 类区分
    # ------------------------------------------------------------------
    def classify(self, targets, my_color):
        """根据己方颜色分类为 friends / enemies / neutrals.
        相比父类: 把 ONNX 检测到的 neutral_block (color='white') 加入 neutrals."""
        if my_color == 'b':
            friend_color, enemy_color = 'blue', 'yellow'
        else:
            friend_color, enemy_color = 'yellow', 'blue'
        friends  = [t for t in targets if t.color == friend_color]
        enemies  = [t for t in targets if t.color == enemy_color]
        neutrals = [t for t in targets if t.color == 'white']
        return friends, enemies, neutrals


# 自检 entry point
def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default='/home/radxa/models/best_320_int8.onnx')
    ap.add_argument('--image', required=True)
    ap.add_argument('--conf', type=float, default=0.25)
    ap.add_argument('--color', default='b', help='b=蓝方 y=黄方')
    args = ap.parse_args()

    img = cv2.imread(args.image)
    if img is None:
        print(f'cannot read {args.image}'); sys.exit(1)
    print(f'image: {args.image} shape={img.shape}')

    det = OnnxDetector(model_path=args.model, conf_thresh=args.conf)

    # 1) detect 所有目标
    targets = det.detect(img)
    print(f'\n[detect all] {len(targets)} targets:')
    for t in targets: print(f'  {t}')

    # 2) detect_own
    own = det.detect_own(img, args.color)
    print(f'\n[detect_own {args.color}] {len(own)} targets:')
    for t in own: print(f'  {t}')

    # 3) classify
    friends, enemies, neutrals = det.classify(targets, args.color)
    print(f'\n[classify {args.color}] friends={len(friends)} '
          f'enemies={len(enemies)} neutrals={len(neutrals)}')

    # 4) priority
    ptype, ptgt = det.get_priority_target(friends, enemies, neutrals)
    print(f'\n[priority] type={ptype} target={ptgt}')


if __name__ == '__main__':
    main()
