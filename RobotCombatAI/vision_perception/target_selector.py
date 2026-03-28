#!/usr/bin/env python3
"""
目标选择策略模块 - 多目标优先级排序与类别映射
"""

import logging

logger = logging.getLogger(__name__)

# 类别映射: cylinder_0=红 cylinder_1=蓝 cylinder_2=白(中立)
CLASS_MAP = {
    'b': {0: 'E', 1: 'F', 2: 'N'},  # 蓝方: 红=敌, 蓝=友, 白=中立
    'y': {0: 'F', 1: 'E', 2: 'N'},  # 黄方: 红=友, 蓝=敌, 白=中立
}

# 目标类型优先级权重 (越高越优先)
TYPE_PRIORITY = {
    'N': 100,  # 中立(白色) - 最优先收集
    'E': 80,   # 敌方 - 次优先
    'F': -50,  # 友方 - 回避
    'X': 0,    # 无目标
}


class TargetInfo:
    __slots__ = ('type', 'cx', 'cy', 'area', 'dir', 'confidence', 'class_id')

    def __init__(self, type_ch, cx, cy, area, dir_val, confidence=0.0, class_id=-1):
        self.type = type_ch
        self.cx = int(cx)
        self.cy = int(cy)
        self.area = int(area)
        self.dir = int(max(-100, min(100, dir_val)))
        self.confidence = confidence
        self.class_id = class_id


class TargetSelector:
    """多目标选择策略 (带EMA平滑+目标粘滞)"""

    def __init__(self, team_color='b', frame_width=640, frame_height=480,
                 ema_alpha=0.3, sticky_threshold=3):
        self.frame_width = frame_width
        self.frame_height = frame_height
        self._class_map = CLASS_MAP.get(team_color, CLASS_MAP['b'])
        self._ema_alpha = ema_alpha
        self._sticky_threshold = sticky_threshold
        self._prev_target = None
        self._sticky_frames = 0

    def update_team_color(self, color: str):
        self._class_map = CLASS_MAP.get(color, CLASS_MAP['b'])
        logger.info("TargetSelector: team color -> %s", color)

    def calc_direction(self, cx: float) -> int:
        center = self.frame_width / 2.0
        if center == 0:
            return 0
        dir_val = int((cx - center) / center * 100)
        return max(-100, min(100, dir_val))

    def map_class(self, class_id: int) -> str:
        return self._class_map.get(class_id, 'X')

    def _select_raw(self, detections) -> TargetInfo:
        """
        从检测结果中选择最优目标(原始逻辑, 无平滑)

        Args:
            detections: numpy array shape (N, 6) -> [x1, y1, x2, y2, conf, cls]
                        或 list of dict (from YOLOv5Detector.detect)

        Returns:
            TargetInfo: 最优目标, 无目标时返回 type='X'
        """
        if detections is None or len(detections) == 0:
            return TargetInfo('X', 0, 0, 0, 0)

        candidates = []

        for det in detections:
            # 支持 numpy array 格式 [x1, y1, x2, y2, conf, cls]
            if hasattr(det, '__len__') and not isinstance(det, dict):
                x1, y1, x2, y2, conf, cls = float(det[0]), float(det[1]), \
                    float(det[2]), float(det[3]), float(det[4]), int(det[5])
            else:
                # dict 格式
                bbox = det.get('bbox', [0, 0, 0, 0])
                x1, y1, x2, y2 = bbox
                conf = det.get('confidence', 0)
                cls = det.get('class_id', 0)

            type_ch = self.map_class(cls)

            # 过滤未知类别
            if type_ch == 'X':
                continue

            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0
            area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
            dir_val = self.calc_direction(cx)

            candidates.append(TargetInfo(
                type_ch, cx, cy, area, dir_val, conf, cls
            ))

        if not candidates:
            return TargetInfo('X', 0, 0, 0, 0)

        # 排序: 类型优先级 > 面积(近的大) > 置信度
        candidates.sort(
            key=lambda t: (
                TYPE_PRIORITY.get(t.type, 0),
                t.area,
                t.confidence
            ),
            reverse=True
        )

        return candidates[0]

    def select(self, detections) -> TargetInfo:
        """带EMA平滑和目标粘滞的选择"""
        raw = self._select_raw(detections)

        # 无历史, 直接返回
        if self._prev_target is None or self._prev_target.type == 'X':
            self._prev_target = raw
            self._sticky_frames = 0
            return raw

        # 无目标时清除历史
        if raw.type == 'X':
            self._sticky_frames += 1
            if self._sticky_frames >= self._sticky_threshold:
                self._prev_target = raw
                self._sticky_frames = 0
            return self._prev_target

        # 目标类型变化: 粘滞机制(连续N帧新类型才切换)
        if raw.type != self._prev_target.type:
            self._sticky_frames += 1
            if self._sticky_frames < self._sticky_threshold:
                return self._prev_target
            # 达到阈值, 切换到新目标
            self._sticky_frames = 0
            self._prev_target = raw
            return raw

        self._sticky_frames = 0

        # 同类型目标: EMA平滑方向和位置
        a = self._ema_alpha
        smoothed = TargetInfo(
            raw.type,
            int(a * raw.cx + (1 - a) * self._prev_target.cx),
            int(a * raw.cy + (1 - a) * self._prev_target.cy),
            int(a * raw.area + (1 - a) * self._prev_target.area),
            int(a * raw.dir + (1 - a) * self._prev_target.dir),
            raw.confidence, raw.class_id
        )

        self._prev_target = smoothed
        return smoothed
