"""fused_detector.py — HSV + ONNX + AprilTag 加权融合的高精度检测器.

数据流:
  detect(frame):
    1) HSV 同步检测  (主线程, ~15ms 每帧, FPS=主循环驱动)
    2) ONNX 异步检测 (后台 worker, ~150ms 推理, detect() 不阻塞)
    3) 融合 HSV 和 ONNX 候选 (按 IoU/中心距离匹配 + 加权投票)
    4) AprilTag 融合在 main.py 主循环里 (现有逻辑, Tag 覆盖颜色 = 最高权威)
    5) 单一 Tracker 平滑 fused 结果

权重设计:
  AprilTag : 1.0  (有 Tag 直接 override, 最高优先, 由 main.py 处理)
  ONNX     : 0.7  (97% 精度, 但 ~150ms 延迟)
  HSV      : 0.3  (~70% 精度, 受光照影响, 但 30 FPS 实时)

匹配规则:
  HSV 目标 vs ONNX 目标: 中心点距离 < FUSE_MATCH_DIST_PX (默认 80px) 视为同一目标
  同意 → 直接采纳同一 color
  分歧 → 权重大的胜 (默认 ONNX 权重 > HSV)

输出 bbox 来源:
  ONNX 匹配到 → ONNX bbox (更准, 矩形紧贴目标)
  仅 HSV → HSV bbox (轮廓外接矩形)

性能:
  ONNX async 不阻塞主循环 → 主循环 FPS = HSV FPS (~15-30 FPS)
  ONNX 推理 ~150ms 在后台并行, 不影响主响应
"""
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np

import config
from detector import ColorDetector, Target, Tracker

# 延迟导入 OnnxDetector (避免 onnxruntime 缺失时整个模块加载失败)
_OnnxDetector = None
def _get_onnx_detector():
    global _OnnxDetector
    if _OnnxDetector is None:
        from onnx_detector import OnnxDetector  # noqa: E402
        _OnnxDetector = OnnxDetector
    return _OnnxDetector


class FusedDetector:
    """HSV + ONNX 加权融合检测器, 接口完全兼容 ColorDetector.

    主循环 detect() 不阻塞 (ONNX 异步), FPS ≈ HSV 帧率.
    精度: AprilTag(1.0) > ONNX(0.7) > HSV(0.3), 自动取权威源."""

    def __init__(self,
                 onnx_model_path=None,
                 onnx_conf_thresh=None,
                 onnx_iou_thresh=0.45,
                 onnx_threads=None,
                 weight_hsv=None,
                 weight_onnx=None,
                 match_dist_px=None,
                 friend_min_area=None,
                 enemy_near_area=None,
                 hsv_only_min_area=None,
                 hsv_only_white_min_area=None,
                 friend_trust_area=None,
                 color_conflict_policy=None,
                 enable_tracking=True):
        # 优先级链 (B4 收编): 参数 > 环境变量 > config 默认值
        # 注: env vars 已在 config.py 集中声明默认值, 这里读 env 是为了支持 systemd
        # Environment= 临时覆盖, 不破坏 config 单一来源原则
        self.weight_hsv = float(
            weight_hsv if weight_hsv is not None
            else os.environ.get('VISION_FUSE_W_HSV',
                                getattr(config, 'FUSE_W_HSV', 0.3)))
        self.weight_onnx = float(
            weight_onnx if weight_onnx is not None
            else os.environ.get('VISION_FUSE_W_ONNX',
                                getattr(config, 'FUSE_W_ONNX', 0.7)))
        self.match_dist_px = int(
            match_dist_px if match_dist_px is not None
            else os.environ.get('VISION_FUSE_MATCH_DIST',
                                getattr(config, 'FUSE_MATCH_DIST_PX', 80)))
        # 距离过滤: 远处的"己方颜色"目标常常是误判 (HSV 把远处敌方车辆颜色看成己方块)
        # friend.area < friend_min_area 时, 触发距离不可信过滤
        # 仅当近距离 (>= enemy_near_area) 存在 enemy 时启用过滤, 避免误伤合法远 friend
        self.friend_min_area = int(
            friend_min_area if friend_min_area is not None
            else os.environ.get('VISION_FUSE_FRIEND_MIN_AREA',
                                getattr(config, 'FUSE_FRIEND_MIN_AREA', 5000)))
        self.enemy_near_area = int(
            enemy_near_area if enemy_near_area is not None
            else os.environ.get('VISION_FUSE_ENEMY_NEAR_AREA',
                                getattr(config, 'FUSE_ENEMY_NEAR_AREA', 6000)))

        # v3 (HSV 远端误识别修复): HSV-only 目标 + friend 全局信任 + 分歧策略
        self.hsv_only_min_area = int(
            hsv_only_min_area if hsv_only_min_area is not None
            else os.environ.get('VISION_FUSE_HSV_ONLY_MIN_AREA',
                                getattr(config, 'FUSE_HSV_ONLY_MIN_AREA', 6000)))
        # v4 (白色块误识别修复): white 走特殊更宽松阈值, 因 ONNX 几乎不识别白色
        self.hsv_only_white_min_area = int(
            hsv_only_white_min_area if hsv_only_white_min_area is not None
            else os.environ.get('VISION_FUSE_HSV_ONLY_WHITE_MIN_AREA',
                                getattr(config, 'FUSE_HSV_ONLY_WHITE_MIN_AREA',
                                        3000)))
        self.friend_trust_area = int(
            friend_trust_area if friend_trust_area is not None
            else os.environ.get('VISION_FUSE_FRIEND_TRUST_AREA',
                                getattr(config, 'FUSE_FRIEND_TRUST_AREA', 8000)))
        # 颜色分歧策略: 'onnx' / 'hsv' / 'neutral' / 'drop'
        self.color_conflict_policy = (
            color_conflict_policy if color_conflict_policy is not None
            else os.environ.get('VISION_FUSE_COLOR_CONFLICT',
                                getattr(config, 'FUSE_COLOR_CONFLICT_POLICY',
                                        'neutral')))
        # 过滤计数 (节流日志用)
        self._hsv_only_drop_count = 0
        self._friend_trust_drop_count = 0
        self._color_conflict_count = 0

        # P1 (2026-05-22): 温度降级开关. True 时 detect() 跳过 ONNX 推理
        # 走纯 HSV 通路, 由 ThermalGuard 周期调 set_throttled() 切换
        # 此时 _fuse() 仍调用 (但 onnx_targets=[]), 保留 hsv_only_min_area 远端防护
        self._thermal_throttled = False
        self._hsv_only_min_area_default = self.hsv_only_min_area
        self._throttle_warmup = 0  # ONNX 恢复后预热帧计数
        # 2026-05-23 致命修复: ONNX stale 自动降级
        # ONNX 推理变慢 → 结果超时被丢弃 → onnx_targets=[]
        # 但 ThermalGuard 还没触发 → hsv_only_min_area 仍是 6000
        # → 中远距离真实目标全被过滤 → 视觉发 X → STM32 纯 IR 乱撞
        self._onnx_empty_streak = 0  # 连续 ONNX 空结果帧数
        self._onnx_good_streak = 0   # 连续 ONNX 有结果帧数 (恢复滞回用)
        self._ONNX_EMPTY_THRESH = 5  # 连续 N 帧空结果就自动放松阈值
        self._ONNX_RECOVER_THRESH = 20  # 连续 N 帧有结果才恢复严格阈值 (防震荡)

        # 内部 HSV (关 tracker, 用我们自己的 fused tracker)
        self._hsv = ColorDetector(enable_tracking=False)

        # 内部 ONNX (关 tracker, 异步推理)
        OnnxDetector = _get_onnx_detector()
        self._onnx = OnnxDetector(
            model_path=onnx_model_path,
            conf_thresh=onnx_conf_thresh,
            iou_thresh=onnx_iou_thresh,
            num_threads=onnx_threads,
            enable_tracking=False,
            async_mode=True,
        )

        # 自己的 Tracker (平滑 fused 结果)
        if enable_tracking:
            self._tracker = Tracker(
                smoothing=getattr(config, 'TRACKER_SMOOTH', 0.3),
                max_dist=getattr(config, 'TRACKER_MAX_DIST', 100),
                max_lost=getattr(config, 'TRACKER_MAX_LOST', 5),
            )
        else:
            self._tracker = None

        # main.py 内部状态字段 (委托给 _hsv)
        # _last_max_area 是 ColorDetector 内部状态, 通过 property 暴露

        print(f'[fused_detector] HSV+ONNX 融合启用 v3 (远端 HSV 误识别修复)',
              flush=True)
        print(f'  weights      : w_hsv={self.weight_hsv} w_onnx={self.weight_onnx}',
              flush=True)
        print(f'  match        : match_dist={self.match_dist_px}px',
              flush=True)
        print(f'  filter (旧)  : friend_min_area={self.friend_min_area} '
              f'enemy_near_area={self.enemy_near_area}',
              flush=True)
        print(f'  filter (新)  : hsv_only_min_area={self.hsv_only_min_area} '
              f'hsv_only_white={self.hsv_only_white_min_area} '
              f'friend_trust_area={self.friend_trust_area}',
              flush=True)
        print(f'  conflict     : color_conflict_policy={self.color_conflict_policy}',
              flush=True)

    # ------------------------------------------------------------------
    # 主检测接口 (兼容 ColorDetector)
    # ------------------------------------------------------------------
    def detect(self, frame):
        """主检测: HSV(同步) + ONNX(异步缓存) → 融合 → Tracker.

        P1: thermal_throttled=True 时跳过 ONNX (高温下 ONNX 量化误差大且拖慢主循环).
            HSV-only 远端误识别防护由 _fuse() 的 hsv_only_min_area 阈值兜底.
        """
        hsv_targets = self._hsv.detect(frame)         # ~15ms
        if self._thermal_throttled:
            onnx_targets = []                          # 高温降级: 跳过 ONNX
        elif self._throttle_warmup > 0:
            self._onnx.detect(frame)                   # 提交帧但丢弃结果 (预热)
            onnx_targets = []
            self._throttle_warmup -= 1
        else:
            onnx_targets = self._onnx.detect(frame)   # 0ms (async cache)

        # 2026-05-23 致命修复: ONNX 结果持续为空时自动放松 HSV 过滤
        # 防止 "ONNX stale + hsv_only_min_area=6000" 杀死所有中远距离目标
        # v2: 加恢复滞回 — 偶尔 1 帧有结果不足以证明 ONNX 稳定，需连续 20 帧才恢复
        if not onnx_targets and not self._thermal_throttled:
            self._onnx_empty_streak += 1
            self._onnx_good_streak = 0  # 打断恢复计数
            if self._onnx_empty_streak == self._ONNX_EMPTY_THRESH:
                relaxed = max(2000, self._hsv_only_min_area_default // 3)
                print(f'[fused_detector] ONNX empty x{self._onnx_empty_streak} '
                      f'→ auto-relax hsv_only_min={self.hsv_only_min_area}'
                      f'→{relaxed}', flush=True)
                self.hsv_only_min_area = relaxed
        elif onnx_targets and not self._thermal_throttled:
            self._onnx_empty_streak = 0
            self._onnx_good_streak += 1
            # 恢复滞回: 需连续 N 帧 ONNX 有结果才恢复严格阈值
            if (self._onnx_good_streak >= self._ONNX_RECOVER_THRESH and
                    self.hsv_only_min_area != self._hsv_only_min_area_default):
                print(f'[fused_detector] ONNX stable x{self._onnx_good_streak} '
                      f'→ restore hsv_only_min={self._hsv_only_min_area_default}',
                      flush=True)
                self.hsv_only_min_area = self._hsv_only_min_area_default

        fused = self._fuse(hsv_targets, onnx_targets)
        if self._tracker:
            fused = self._tracker.update(fused)
        return fused

    def set_throttled(self, throttled, reason=''):
        """P1: 由 ThermalGuard 调用. True=温度过高, 跳过 ONNX 走 HSV-only.

        B3: 热降级时降低 hsv_only_min_area (此时 ONNX 不在场, HSV 是唯一源,
            远端真目标不应被过于严格的 area 门槛过滤).
        B1: ONNX 恢复后设置 3 帧预热期, 期间 ONNX 结果不稳定仍走 HSV-only.
        """
        old = self._thermal_throttled
        self._thermal_throttled = bool(throttled)
        if old != self._thermal_throttled:
            if self._thermal_throttled:
                self.hsv_only_min_area = max(2000, self._hsv_only_min_area_default // 3)
                self._onnx_empty_streak = 0
                self._onnx_good_streak = 0
                mode = 'HSV-ONLY (ONNX skipped)'
            else:
                # 恢复时不立即拉回6000! 保持宽松阈值直到 ONNX 稳定产出
                # (warmup + 初始几帧 ONNX 结果不稳定, 防 6000 杀目标)
                self.hsv_only_min_area = max(2000, self._hsv_only_min_area_default // 3)
                self._throttle_warmup = 3
                self._onnx_empty_streak = 0
                self._onnx_good_streak = 0
                mode = 'HSV+ONNX FUSED (hsv_min stays relaxed until ONNX stable)'
            print(f'[fused_detector] mode → {mode} '
                  f'hsv_only_min={self.hsv_only_min_area} '
                  + (f'({reason})' if reason else ''), flush=True)

    def detect_own(self, frame, my_color):
        """只返回我方颜色目标 (远处可疑目标会被距离过滤抑制).
        过滤逻辑: 若同时存在近距离敌方车辆 (enemy.area >= enemy_near_area),
        则远处的"己方颜色"目标 (area < friend_min_area) 视为误判 (敌方车辆被识别成
        己方块), 不输出. 防止远处假目标让机器人盲目后退."""
        target_color = 'blue' if my_color == 'b' else 'yellow'
        enemy_color = 'yellow' if my_color == 'b' else 'blue'

        all_targets = self.detect(frame)
        own = [t for t in all_targets if t.color == target_color]
        enemies = [t for t in all_targets if t.color == enemy_color]

        # 检查近距离 enemy 是否存在
        has_near_enemy = any(e.area >= self.enemy_near_area for e in enemies)
        if has_near_enemy:
            # 过滤远处可疑 own (避免敌方车辆误判为己方块)
            own = [t for t in own if t.area >= self.friend_min_area]
        return own

    # ------------------------------------------------------------------
    # 融合核心算法 (HSV + ONNX 加权)
    # ------------------------------------------------------------------
    def _fuse(self, hsv_targets, onnx_targets):
        """按中心距离匹配 HSV 和 ONNX 候选.

        v3 投票规则 (修远端 HSV 误识别 bug):
          - 同意 → 信该颜色
          - 分歧 → 按 color_conflict_policy ('neutral'/'hsv'/'onnx'/'drop')
          - 仅 HSV 检到 → 要求 area >= hsv_only_min_area 才信任 (过滤远端假目标)
          - 仅 ONNX 检到 → 直接信 (ONNX 不会假阳性远端环境, 训练集决定)

        2026-05-23 修复: 当 ONNX 完全没有贡献时 (onnx_targets=[]),
        所有目标走 HSV-only 路径, 此时用宽松阈值避免杀死真实目标。
        """
        used_onnx = set()
        result = []

        # 当 ONNX 完全没贡献时, 用宽松阈值 (和 thermal_throttled 等效)
        onnx_absent = len(onnx_targets) == 0
        effective_hsv_min = self.hsv_only_min_area
        effective_white_min = self.hsv_only_white_min_area
        if onnx_absent:
            effective_hsv_min = max(2000, self.hsv_only_min_area // 3)
            effective_white_min = max(1500, self.hsv_only_white_min_area // 2)

        for h in hsv_targets:
            best_i, best_d = -1, self.match_dist_px
            for i, o in enumerate(onnx_targets):
                if i in used_onnx:
                    continue
                d = ((h.cx - o.cx) ** 2 + (h.cy - o.cy) ** 2) ** 0.5
                if d < best_d:
                    best_d, best_i = d, i

            if best_i < 0:
                if h.color == 'white':
                    min_area = effective_white_min
                else:
                    min_area = effective_hsv_min
                if h.area >= min_area:
                    result.append(h)
                else:
                    self._hsv_only_drop_count += 1
                    if self._hsv_only_drop_count % 60 == 1:
                        print(f'[fused] HSV-only drop {h.color} '
                              f'area={int(h.area)}<{min_area} '
                              f'(总计 {self._hsv_only_drop_count} 次)', flush=True)
                continue

            o = onnx_targets[best_i]
            used_onnx.add(best_i)

            # v3 颜色投票:
            if h.color == o.color:
                color = h.color  # 两者同意
            else:
                self._color_conflict_count += 1
                policy = self.color_conflict_policy
                if policy == 'drop':
                    # 完全丢弃此目标 (最保守, 避免误判)
                    if self._color_conflict_count % 30 == 1:
                        print(f'[fused] color conflict drop: HSV={h.color} '
                              f'ONNX={o.color} area={int(o.area)} '
                              f'(总计 {self._color_conflict_count})', flush=True)
                    continue
                elif policy == 'neutral':
                    color = 'white'  # 降级为中立, attack 模式下优先级低于 enemy
                elif policy == 'hsv':
                    color = h.color
                else:  # 'onnx' (旧行为)
                    color = o.color
                if self._color_conflict_count % 30 == 1:
                    print(f'[fused] color conflict: HSV={h.color} ONNX={o.color} '
                          f'→ {color} (policy={policy}, '
                          f'总计 {self._color_conflict_count})', flush=True)

            # bbox 优先用 ONNX (训练数据驱动, 更紧贴目标)
            result.append(Target(
                color=color,
                cx=o.cx, cy=o.cy,
                x=o.x, y=o.y, w=o.w, h=o.h,
                area=o.area, solidity=1.0,
            ))

        # 仅 ONNX 检到 (HSV 漏检) → 直接信 (ONNX 不会在环境上假阳性)
        for i, o in enumerate(onnx_targets):
            if i not in used_onnx:
                result.append(o)

        return result

    # ------------------------------------------------------------------
    # ColorDetector 兼容接口 (委托给 HSV)
    # ------------------------------------------------------------------
    def classify(self, targets, my_color):
        """分类为 friends / enemies / neutrals (ONNX 5 类→ 3 类).

        v3 多层 friend 过滤 (修远端 HSV 误识别):
          1. 全局信任阈值: friend.area >= friend_trust_area 才进入 friends 列表
             (远端小面积 friend 大概率是 HSV/ONNX 环境噪声, 不信任)
          2. 距离过滤: has_near_enemy 时 friend 阈值收紧到 friend_min_area
             (兼容旧行为, 防"远友+近敌"组合)
        理由: friend 信号会让 STM32 回避, 假阳性代价大于漏报;
              远端真 friend (己方块) 不在威胁范围, 可以不信任.
        """
        if my_color == 'b':
            friend_color, enemy_color = 'blue', 'yellow'
        else:
            friend_color, enemy_color = 'yellow', 'blue'

        raw_friends = [t for t in targets if t.color == friend_color]
        enemies     = [t for t in targets if t.color == enemy_color]
        neutrals    = [t for t in targets if t.color == 'white']

        # v3 第 1 层: friend 全局信任阈值 (远端小面积 friend 永远不信)
        trusted_friends = [f for f in raw_friends if f.area >= self.friend_trust_area]
        global_dropped = len(raw_friends) - len(trusted_friends)
        if global_dropped > 0:
            self._friend_trust_drop_count += global_dropped
            if self._friend_trust_drop_count % 60 <= global_dropped:
                print(f'[fused] friend_trust drop {global_dropped} '
                      f'(area<{self.friend_trust_area}, 总计 '
                      f'{self._friend_trust_drop_count})', flush=True)

        # v3 第 2 层: has_near_enemy 时进一步收紧 (兼容旧行为)
        has_near_enemy = any(e.area >= self.enemy_near_area for e in enemies)
        if has_near_enemy:
            filtered = [f for f in trusted_friends if f.area >= self.friend_min_area]
            dropped = len(trusted_friends) - len(filtered)
            if dropped > 0:
                if not hasattr(self, '_dist_filter_log_count'):
                    self._dist_filter_log_count = 0
                self._dist_filter_log_count += 1
                if self._dist_filter_log_count % 30 == 1:
                    print(f'[fused] distance filter dropped {dropped} far friend(s) '
                          f'(near enemy present, area_thresh={self.friend_min_area})',
                          flush=True)
            friends = filtered
        else:
            friends = trusted_friends

        return friends, enemies, neutrals

    def get_priority_target(self, friends, enemies, neutrals):
        return self._hsv.get_priority_target(friends, enemies, neutrals)

    def detect_black_ratio(self, frame):
        return self._hsv.detect_black_ratio(frame)

    def reset_drop_ema(self):
        return self._hsv.reset_drop_ema()

    def draw_targets(self, frame, targets, my_color='b'):
        return self._hsv.draw_targets(frame, targets, my_color)

    # main.py 访问的内部状态字段 → 委托给 HSV
    @property
    def _last_max_area(self):
        return self._hsv._last_max_area

    @_last_max_area.setter
    def _last_max_area(self, v):
        self._hsv._last_max_area = v


def main():
    import argparse, glob
    ap = argparse.ArgumentParser()
    ap.add_argument('--image', required=True)
    ap.add_argument('--color', default='b')
    args = ap.parse_args()

    img = cv2.imread(args.image)
    if img is None:
        print(f'cannot read {args.image}'); sys.exit(1)
    print(f'image: {args.image} shape={img.shape}')

    det = FusedDetector()
    # 让 ONNX worker warm up
    print('warmup...')
    for _ in range(3):
        det.detect(img); time.sleep(0.2)

    t0 = time.time()
    targets = det.detect(img)
    print(f'\n[detect] {len(targets)} targets in {(time.time()-t0)*1000:.1f} ms')
    for t in targets: print(f'  {t}')
    friends, enemies, neutrals = det.classify(targets, args.color)
    print(f'\n[classify {args.color}] friends={len(friends)} enemies={len(enemies)} '
          f'neutrals={len(neutrals)}')

    # bench
    times=[]
    for _ in range(50):
        t0=time.time(); det.detect(img); times.append(time.time()-t0)
    avg=sum(times)/len(times)*1000
    print(f'[bench] detect avg={avg:.1f}ms ≈ {1000/avg:.1f} FPS')


if __name__ == '__main__':
    main()
