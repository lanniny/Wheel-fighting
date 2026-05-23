"""
颜色检测器 v4 - CLAHE光照均衡 + 形态学过滤 + 帧间跟踪平滑

改进点 (vs v3):
  - 白色阈值收紧 S:0-25, 减少反光/高光面误检
  - 优先级模式可配置: collect(N>E) / attack(E>N)
  - 距离分段: near/mid/far 替代线性比例, 语义更清晰
  - Tracker 增加 color_switch_cooldown 防止颜色跳变
"""
import cv2
import numpy as np
import config


class Target:
    __slots__ = ('color', 'cx', 'cy', 'x', 'y', 'w', 'h', 'area',
                 'solidity', 'distance', 'distance_level', 'direction')

    def __init__(self, color, cx, cy, x, y, w, h, area, solidity=0.0):
        self.color = color
        self.cx = cx
        self.cy = cy
        self.x = x
        self.y = y
        self.w = w
        self.h = h
        self.area = area
        self.solidity = solidity
        # 相对画面中心的水平偏移, 归一化到 [-1, +1]
        self.direction = (cx - config.CAMERA_WIDTH / 2) / (config.CAMERA_WIDTH / 2)
        if getattr(config, 'DIRECTION_FLIP', False):
            self.direction = -self.direction
        # 距离分段: 'near'/'mid'/'far'
        near_th = getattr(config, 'DISTANCE_NEAR', 12000)
        far_th = getattr(config, 'DISTANCE_FAR', 3000)
        if area >= near_th:
            self.distance_level = 'near'
        elif area <= far_th:
            self.distance_level = 'far'
        else:
            self.distance_level = 'mid'
        # 连续距离值保留兼容
        self.distance = min(1.0, area / config.MAX_CONTOUR_AREA)

    def __repr__(self):
        return (f'{self.color}({self.cx},{self.cy} {self.w}x{self.h} '
                f'a={self.area:.0f} dir={self.direction:+.2f} dist={self.distance:.2f})')


class Tracker:
    """帧间平滑跟踪器, 支持速度预测 + 面积约束 + 自适应平滑 + 新目标确认"""

    def __init__(self, smoothing=0.15, max_dist=150, max_lost=5,
                 color_switch_frames=None, enable_prediction=True,
                 confirm_frames=2):
        if color_switch_frames is None:
            color_switch_frames = getattr(config, 'TRACKER_COLOR_SWITCH_FRAMES', 5)
        self.smoothing = smoothing
        self.max_dist = max_dist
        self.max_lost = max_lost
        self.color_switch_frames = color_switch_frames
        self.enable_prediction = enable_prediction
        self.confirm_frames = confirm_frames
        self._tracks = {}
        self._next_id = 0

    def _predict_pos(self, tr):
        """线性外推预测下帧位置"""
        if not self.enable_prediction:
            return tr['cx'], tr['cy']
        vx = tr.get('vx', 0)
        vy = tr.get('vy', 0)
        return tr['cx'] + vx, tr['cy'] + vy

    def update(self, targets):
        """用当前帧的检测结果更新跟踪器, 返回平滑后的目标列表"""
        used_tracks = set()
        result = []

        for t in targets:
            best_id = None
            best_dist = self.max_dist
            for tid, tr in self._tracks.items():
                if tid in used_tracks:
                    continue
                px, py = self._predict_pos(tr)
                d = ((px - t.cx) ** 2 + (py - t.cy) ** 2) ** 0.5
                # 面积变化约束: 2× (收紧, 原3×)
                if tr['area'] > 0:
                    area_ratio = t.area / tr['area']
                    if area_ratio > 2.0 or area_ratio < 0.5:
                        continue
                if d < best_dist:
                    best_dist = d
                    best_id = tid

            if best_id is not None:
                tr = self._tracks[best_id]

                # 颜色切换冷却
                if t.color != tr['color']:
                    tr['color_switch_count'] = tr.get('color_switch_count', 0) + 1
                    if tr['color_switch_count'] < self.color_switch_frames:
                        t = Target(tr['color'], t.cx, t.cy, t.x, t.y,
                                   t.w, t.h, t.area, t.solidity)
                    else:
                        tr['color'] = t.color
                        tr['color_switch_count'] = 0
                else:
                    tr['color_switch_count'] = 0

                # 更新速度
                tr['vx'] = t.cx - tr['cx']
                tr['vy'] = t.cy - tr['cy']

                # 自适应平滑: 快速运动→低平滑(跟紧), 慢速→高平滑(去抖)
                speed = (tr['vx'] ** 2 + tr['vy'] ** 2) ** 0.5
                s = max(0.05, min(0.4, self.smoothing - speed * 0.008))

                tr['cx'] = int(tr['cx'] * s + t.cx * (1 - s))
                tr['cy'] = int(tr['cy'] * s + t.cy * (1 - s))
                tr['area'] = tr['area'] * s + t.area * (1 - s)
                tr['lost'] = 0
                tr['confirmed'] = True
                used_tracks.add(best_id)
                result.append(Target(t.color, tr['cx'], tr['cy'],
                                     t.x, t.y, t.w, t.h, tr['area'], t.solidity))
            else:
                tid = self._next_id
                self._next_id += 1
                self._tracks[tid] = {
                    'cx': t.cx, 'cy': t.cy, 'area': t.area,
                    'color': t.color, 'lost': 0,
                    'color_switch_count': 0,
                    'vx': 0, 'vy': 0,
                    'confirmed': self.confirm_frames <= 1,
                    'confirm_count': 1,
                }
                used_tracks.add(tid)
                # 新目标确认: 需连续 N 帧出现才输出
                if self.confirm_frames <= 1:
                    result.append(t)

        # 清除丢失轨迹, 丢失时速度快速衰减
        for tid, tr in list(self._tracks.items()):
            if tid not in used_tracks:
                tr['lost'] += 1
                tr['vx'] *= 0.3
                tr['vy'] *= 0.3
                if tr['lost'] > self.max_lost:
                    del self._tracks[tid]
            else:
                # 新目标确认计数
                if not tr.get('confirmed', False):
                    tr['confirm_count'] = tr.get('confirm_count', 0) + 1
                    if tr['confirm_count'] >= self.confirm_frames:
                        tr['confirmed'] = True

        # 补充已确认但本轮未加入 result 的新确认目标
        for tid, tr in self._tracks.items():
            if tid in used_tracks and tr.get('confirmed') and tr.get('confirm_count', 0) == self.confirm_frames:
                # 刚确认的目标, 补入 result
                for t in targets:
                    if abs(t.cx - tr['cx']) < 30 and abs(t.cy - tr['cy']) < 30:
                        result.append(Target(t.color, tr['cx'], tr['cy'],
                                             t.x, t.y, t.w, t.h, tr['area'], t.solidity))
                        break

        return result


class ColorDetector:
    def __init__(self, enable_tracking=True):
        # 形态学核: open去噪点, close填空洞
        self._kernel_open  = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        self._kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        # 小目标用小核, 大目标用大核
        self._kernel_open_sm  = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2))
        self._kernel_close_sm = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        self._kernel_open_lg  = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self._kernel_close_lg = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        self._tracker = Tracker(
            smoothing=getattr(config, 'TRACKER_SMOOTHING', 0.15),
            max_dist=getattr(config, 'TRACKER_MAX_DIST', 150),
            max_lost=getattr(config, 'TRACKER_MAX_LOST', 5),
            enable_prediction=getattr(config, 'TRACKER_PREDICT', True),
            confirm_frames=getattr(config, 'TRACKER_CONFIRM_FRAMES', 2),
        ) if enable_tracking else None
        # CLAHE
        clip = getattr(config, 'CLAHE_CLIP_LIMIT', 1.5)
        tile = getattr(config, 'CLAHE_TILE_SIZE', 4)
        self._clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=(tile, tile))
        self._clahe_half = cv2.createCLAHE(clipLimit=clip, tileGridSize=(2, 2))
        self._clahe_s = cv2.createCLAHE(clipLimit=1.0, tileGridSize=(tile, tile))
        # 光照 EMA
        self._v_ema = 128.0
        # 上帧最大目标面积 (用于自适应形态学核)
        self._last_max_area = 0
        # 掉台检测 EMA: ratio 和 direction 时间平滑
        self._drop_ratio_ema = 0.0
        self._drop_dir_ema = 0.0
        self._drop_ema_initialized = False
        # 缓存高频访问的配置值 (避免热路径 getattr 开销)
        self._min_area = config.MIN_CONTOUR_AREA
        self._min_area_yellow = getattr(config, 'MIN_CONTOUR_AREA_YELLOW', config.MIN_CONTOUR_AREA)
        self._max_area = config.MAX_CONTOUR_AREA
        self._max_area_yellow = getattr(config, 'MAX_CONTOUR_AREA_YELLOW', config.MAX_CONTOUR_AREA)
        self._min_aspect = config.MIN_ASPECT_RATIO
        self._max_aspect = config.MAX_ASPECT_RATIO
        self._yellow_circ_min = getattr(config, 'YELLOW_CIRCULARITY_MIN', 0.15)
        self._yellow_h_std_max = getattr(config, 'YELLOW_H_STD_MAX', 25)
        self._yellow_s_mean_min = getattr(config, 'YELLOW_S_MEAN_MIN', 0)
        self._bottom_exclude = getattr(config, 'FRAME_BOTTOM_EXCLUDE', 0.12)
        self._close_range_ratio = getattr(config, 'CLOSE_RANGE_RATIO', 0.40)
        self._adaptive_v = getattr(config, 'ADAPTIVE_V_THRESHOLD', True)
        self._use_clahe = getattr(config, 'USE_CLAHE', True)
        self._detect_half = getattr(config, 'DETECT_HALF_RES', True)
        self._clahe_s_channel = getattr(config, 'CLAHE_S_CHANNEL', False)

    # ------------------------------------------------------------------
    # 预处理: CLAHE + 高斯模糊 → HSV
    # ------------------------------------------------------------------
    def _preprocess(self, frame):
        """BGR → HSV, 可选半分辨率 + CLAHE均衡V通道 + 光照EMA"""
        use_half = self._detect_half
        if use_half:
            frame = cv2.resize(frame, (frame.shape[1] // 2, frame.shape[0] // 2),
                               interpolation=cv2.INTER_NEAREST)
        blurred = cv2.GaussianBlur(frame, (3, 3), 0)
        hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)

        if self._use_clahe:
            h, s, v = cv2.split(hsv)
            v = (self._clahe_half if use_half else self._clahe).apply(v)
            if self._clahe_s_channel:
                s = self._clahe_s.apply(s)
            hsv = cv2.merge([h, s, v])

        # 光照 EMA: 平滑帧间亮度变化
        current_v = hsv[:, :, 2].mean()
        self._v_ema = 0.9 * self._v_ema + 0.1 * current_v
        self._is_half_res = use_half

        return hsv

    # ------------------------------------------------------------------
    # 单色检测
    # ------------------------------------------------------------------
    def _detect_color(self, hsv, color_name, hsv_range, exclusion_mask=None):
        """在HSV图中检测一种颜色，返回按面积降序排列的 Target 列表"""
        lower = hsv_range['lower'].copy()
        upper = hsv_range['upper'].copy()

        # 连续自适应V阈值: 基于光照EMA平滑调整, 替代硬切换
        if self._adaptive_v and color_name != 'white':
            v_offset = int((128 - self._v_ema) * 0.3)
            lower[2] = max(20, int(lower[2]) + v_offset)

        mask = cv2.inRange(hsv, lower, upper)

        # 自适应形态学核: 根据上帧最大目标面积选择核大小
        last_area = self._last_max_area
        is_half = self._is_half_res
        # 半分辨率下面积缩小4倍
        area_th = last_area * 4 if is_half else last_area
        if area_th > 10000:
            k_open, k_close = self._kernel_open_lg, self._kernel_close_lg
        elif area_th < 2000:
            k_open, k_close = self._kernel_open_sm, self._kernel_close_sm
        else:
            k_open, k_close = self._kernel_open, self._kernel_close

        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k_open)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k_close)

        if exclusion_mask is not None:
            mask = cv2.bitwise_and(mask, cv2.bitwise_not(exclusion_mask))

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)

        # 面积阈值 (已缓存到 __init__)
        is_yellow = color_name == 'yellow'
        if color_name == 'white':
            min_area = 1500
            max_area = self._max_area
        elif is_yellow:
            min_area = self._min_area_yellow
            max_area = self._max_area_yellow
        else:
            min_area = self._min_area
            max_area = self._max_area

        # 半分辨率下面积阈值缩小4倍
        if is_half:
            min_area //= 4
            max_area //= 4

        base_solidity = 0.65 if color_name == 'white' else 0.3
        min_aspect = 0.5 if color_name == 'white' else self._min_aspect
        max_aspect = 2.0 if color_name == 'white' else self._max_aspect

        targets = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < min_area or area > max_area:
                continue

            x, y, w, h = cv2.boundingRect(cnt)

            # 大轮廓跳过宽高比检查
            area_full = area * 4 if is_half else area
            if area_full < 50000:
                aspect = w / h if h > 0 else 0
                if aspect < min_aspect or aspect > max_aspect:
                    continue

            hull_area = cv2.contourArea(cv2.convexHull(cnt))
            solidity = area / hull_area if hull_area > 0 else 0
            min_solidity = base_solidity if area_full < 5000 else 0.15
            if solidity < min_solidity:
                continue

            # 底部画幅排除 (黄色专用): 台面近距离区域暖色反光集中
            if is_yellow and self._bottom_exclude > 0:
                frame_h = hsv.shape[0]
                if (y + h) > frame_h * (1 - self._bottom_exclude):
                    continue

            # 圆度过滤 (黄色专用): 能量块圆柱体~0.5-0.8, 噪声<0.3
            if is_yellow:
                perimeter = cv2.arcLength(cnt, True)
                if perimeter > 0:
                    circularity = 4 * 3.14159 * area / (perimeter * perimeter)
                    if circularity < self._yellow_circ_min:
                        continue

            # H/S 二次验证 (共用 contour_mask, 避免重复创建)
            need_h_check = area_full < 5000 and color_name in ('blue', 'yellow')
            need_s_check = is_yellow and self._yellow_s_mean_min > 0
            if need_h_check or need_s_check:
                contour_mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
                cv2.drawContours(contour_mask, [cnt], -1, 255, -1)
                mask_px = contour_mask > 0
                if need_h_check:
                    h_pixels = hsv[:, :, 0][mask_px]
                    std_limit = self._yellow_h_std_max if is_yellow else 25
                    if len(h_pixels) > 10 and h_pixels.std() > std_limit:
                        continue
                if need_s_check:
                    s_pixels = hsv[:, :, 1][mask_px]
                    if len(s_pixels) > 0 and s_pixels.mean() < self._yellow_s_mean_min:
                        continue

            # 半分辨率坐标映射回全分辨率
            if is_half:
                cx = (x + w // 2) * 2
                cy = (y + h // 2) * 2
                x, y, w, h = x * 2, y * 2, w * 2, h * 2
                area = area * 4
            else:
                cx = x + w // 2
                cy = y + h // 2

            targets.append(Target(color_name, cx, cy, x, y, w, h, area, solidity))

        targets.sort(key=lambda t: t.area, reverse=True)
        # 更新上帧最大面积
        if targets:
            self._last_max_area = int(targets[0].area)
        return targets[:config.MAX_TARGETS]

    # ------------------------------------------------------------------
    # 排斥掩码: 蓝/黄区域膨胀后排除白色检测
    # ------------------------------------------------------------------
    @staticmethod
    def _build_exclusion_mask(shape, targets, dilate_px=15):
        """从已检测到的目标生成排斥掩码 (255=排斥区域)"""
        mask = np.zeros(shape, dtype=np.uint8)
        for t in targets:
            cv2.rectangle(mask, (t.x, t.y), (t.x + t.w, t.y + t.h), 255, -1)
        if dilate_px > 0 and len(targets) > 0:
            kernel = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (dilate_px * 2 + 1, dilate_px * 2 + 1))
            mask = cv2.dilate(mask, kernel)
        return mask

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------
    def _detect_close_range(self, hsv, color_filter=None):
        """近距离回退: 中心ROI颜色占比检测 (色块充满画面时轮廓检测失效)。

        Args:
            color_filter: None=双色模式(蓝+黄), 或 (name, hsv_range) 元组=单色模式
        """
        fh, fw = hsv.shape[:2]
        roi = hsv[fh // 4:3 * fh // 4, fw // 4:3 * fw // 4]
        roi_pixels = roi.shape[0] * roi.shape[1]
        if roi_pixels == 0:
            return []

        is_half = self._is_half_res
        colors = ([color_filter] if color_filter
                  else [('blue', config.HSV_BLUE), ('yellow', config.HSV_YELLOW)])

        for color_name, hsv_range in colors:
            lower = hsv_range['lower'].copy()
            if color_name != 'yellow':
                lower[1] = max(0, lower[1] - 30)
            mask = cv2.inRange(roi, lower, hsv_range['upper'])
            ratio = cv2.countNonZero(mask) / roi_pixels
            if ratio > self._close_range_ratio:
                # H 通道一致性检查
                h_pixels = roi[:, :, 0][mask > 0]
                if len(h_pixels) > 50 and h_pixels.std() > 30:
                    continue
                # 黄色 S 均值验证
                if color_name == 'yellow' and self._yellow_s_mean_min > 0:
                    s_pixels = roi[:, :, 1][mask > 0]
                    if len(s_pixels) > 0 and s_pixels.mean() < self._yellow_s_mean_min:
                        continue
                # 映射回全分辨率坐标
                if is_half:
                    cx, cy = fw, fh
                    area = int(ratio * fw * fh * 4)
                    return [Target(color_name, cx, cy,
                                   fw // 2, fh // 2, fw, fh, area)]
                else:
                    cx, cy = fw // 2, fh // 2
                    area = int(ratio * fw * fh)
                    return [Target(color_name, cx, cy,
                                   fw // 4, fh // 4, fw // 2, fh // 2, area)]
        return []

    def detect(self, frame):
        """
        检测一帧中目标 (v4: 白色 + 蓝色 + 黄色, 三色模式)。

        v4 改进 (修白色能量块被误判为蓝色 bug, 2026-05-21):
          - 恢复白色检测 (中立能量块), 之前 detect() 没调用白色
          - 白色优先扫描 + 排斥膨胀 30px, 防止白色块边缘高光/反光像素被蓝色吸收
          - 蓝色 ROI 排除白色区域; 黄色 ROI 排除白色 + 蓝色区域
          - 可通过 DETECT_WHITE=0 关闭白色检测回退到旧 v3 行为

        流程: 预处理 → 白色 → 蓝色(排除白色) → 黄色(排除白+蓝) → 近距离回退 → 跟踪
        """
        hsv = self._preprocess(frame)

        # ── v4: 白色优先 (中立能量块) ──
        # 白色块在 HSV 中表现为 V 极高 + S 极低, 但边缘高光像素会"漏"到蓝色
        # 范围 (H 在低 S 下不稳定), 不优先检测 + 排斥掩码会让白色块被识别为蓝色
        detect_white = getattr(config, 'DETECT_WHITE', True)
        if detect_white:
            white_targets = self._detect_color(hsv, 'white', config.HSV_WHITE)
            white_excl_dilate = getattr(config, 'WHITE_EXCL_DILATE', 30)
            white_excl = (self._build_exclusion_mask(
                hsv.shape[:2], white_targets, dilate_px=white_excl_dilate)
                if white_targets else None)
        else:
            white_targets = []
            white_excl = None

        # 蓝色检测 (排除白色区域)
        blue_targets = self._detect_color(
            hsv, 'blue', config.HSV_BLUE, exclusion_mask=white_excl)

        # 蓝/白合并掩码, 给黄色检测用 (黄色不能跑到白色或蓝色区域)
        if white_targets or blue_targets:
            yellow_excl = self._build_exclusion_mask(
                hsv.shape[:2], white_targets + blue_targets, dilate_px=20)
        else:
            yellow_excl = None

        yellow_targets = self._detect_color(
            hsv, 'yellow', config.HSV_YELLOW, exclusion_mask=yellow_excl)

        all_targets = white_targets + blue_targets + yellow_targets

        if not all_targets:
            all_targets = self._detect_close_range(hsv)

        if self._tracker:
            all_targets = self._tracker.update(all_targets)

        return all_targets

    def detect_own(self, frame, my_color):
        """
        单色检测: 只检测己方颜色能量块。
        my_color: 'b'=蓝色, 'y'=黄色
        """
        hsv = self._preprocess(frame)

        if my_color == 'b':
            color_name, hsv_range = 'blue', config.HSV_BLUE
        else:
            color_name, hsv_range = 'yellow', config.HSV_YELLOW

        targets = self._detect_color(hsv, color_name, hsv_range)

        if not targets:
            targets = self._detect_close_range(hsv, color_filter=(color_name, hsv_range))

        if self._tracker:
            targets = self._tracker.update(targets)

        return targets

    def classify(self, targets, my_color):
        """根据己方颜色将目标分类为 friends/enemies/neutrals.

        v4 (2026-05-21): 恢复 neutrals 列表 (白色 = 中立能量块).
        """
        if my_color == 'b':
            friend_color, enemy_color = 'blue', 'yellow'
        else:
            friend_color, enemy_color = 'yellow', 'blue'
        friends  = [t for t in targets if t.color == friend_color]
        enemies  = [t for t in targets if t.color == enemy_color]
        neutrals = [t for t in targets if t.color == 'white']
        return friends, enemies, neutrals

    # ------------------------------------------------------------------
    # 掉台回复: 重置 EMA 状态 (进入/退出掉台模式时调用)
    # ------------------------------------------------------------------
    def reset_drop_ema(self):
        """重置掉台检测的 EMA 平滑状态"""
        self._drop_ratio_ema = 0.0
        self._drop_dir_ema = 0.0
        self._drop_ema_initialized = False

    # ------------------------------------------------------------------
    # 掉台回复: 黑色(台面)检测
    # ------------------------------------------------------------------
    def detect_black_ratio(self, frame):
        """检测画面中黑色(台面)区域占比和中心位置。

        v2 改进 (修复 V_factor=1.0 无区分力问题):
          1) V 阈值用 percentile-25 代替均值, 选出真正的暗区而非 ~50% 噪声
          2) 低饱和度 (S < S_MAX): 黑色/灰色无彩色
          3) 最大连通域占比: 台面是大块连续暗区, 散乱噪点被排除
          4) 均匀性加分需同时满足 V_std 和 S_std 低 (真正均匀暗色)

        Returns:
            (ratio, cx, cy, direction, dbg_info)
            ratio: 有效黑色面积占比 [0.0, 1.0]
            cx, cy: 黑色区域质心 (全分辨率坐标)
            direction: 质心相对画面中心偏移 [-1.0, +1.0]
            dbg_info: 诊断字典 (v_mean, v_std, s_std, raw_ratio, blob_ratio, bonus)
        """
        h, w = frame.shape[:2]
        margin_x = w // 6
        margin_y = h // 6
        roi = frame[margin_y:h - margin_y, margin_x:w - margin_x]

        # 只需 V 和 S 通道, 跳过完整 HSV 转换:
        # V = max(B,G,R), S = (V-min)/V*255 — 与 OpenCV HSV 公式一致
        # 省去 H 通道计算和 cvtColor 内部临时分配, 掉台检测场景下足够精确
        b_ch = roi[:, :, 0].astype(np.uint16)
        g_ch = roi[:, :, 1].astype(np.uint16)
        r_ch = roi[:, :, 2].astype(np.uint16)
        v_ch_16 = np.maximum(np.maximum(b_ch, g_ch), r_ch)
        min_ch = np.minimum(np.minimum(b_ch, g_ch), r_ch)
        v_ch = v_ch_16.astype(np.uint8)
        # S = (V - min) * 255 / V, 避免浮点除法
        with np.errstate(divide='ignore', invalid='ignore'):
            s_ch = np.where(v_ch_16 > 0,
                            ((v_ch_16 - min_ch) * 255 // v_ch_16).astype(np.uint8),
                            np.uint8(0))

        v_mean = float(v_ch.mean())
        v_std = float(v_ch.std())
        s_std = float(s_ch.std())

        # 自适应 V 阈值: 用 V 的 P25 (25th percentile) + 偏移量
        # AE 把暗场景提亮到 V_mean≈130, 但台面像素集中在分布低端
        # P25 代表 "偏暗的那部分", 加偏移量向上扩展覆盖台面边缘
        v_p25 = float(np.percentile(v_ch, 25))
        v_offset = getattr(config, 'DROP_V_OFFSET', 15)
        adaptive_v_max = max(
            int(config.HSV_BLACK['upper'][2]),       # 绝对下限 (默认60)
            int(v_p25 + v_offset),                   # P25 + 偏移
        )
        s_max = int(config.HSV_BLACK['upper'][1])    # 饱和度上限 (默认100)

        # 条件1: 低饱和度 (无彩色)
        s_mask = (s_ch <= s_max).astype(np.uint8) * 255
        # 条件2: V 低于自适应阈值
        v_mask = (v_ch <= adaptive_v_max).astype(np.uint8) * 255
        # 交集
        black_mask = cv2.bitwise_and(s_mask, v_mask)

        # 形态学清理: 去噪点 + 填空洞
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        black_mask = cv2.morphologyEx(black_mask, cv2.MORPH_OPEN, kernel)
        black_mask = cv2.morphologyEx(black_mask, cv2.MORPH_CLOSE, kernel)

        total_pixels = roi.shape[0] * roi.shape[1]
        black_pixels = cv2.countNonZero(black_mask)
        raw_ratio = black_pixels / total_pixels if total_pixels > 0 else 0.0

        # 最大连通域占比: 台面是大块连续暗区, 散点噪声不算
        blob_ratio = 0.0
        blob_cx, blob_cy = roi.shape[1] // 2, roi.shape[0] // 2
        if black_pixels > 100:
            num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
                black_mask, connectivity=8)
            if num_labels > 1:
                # stats[0] 是背景, 跳过; 找最大前景连通域
                largest_idx = 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])
                largest_area = stats[largest_idx, cv2.CC_STAT_AREA]
                blob_ratio = largest_area / total_pixels
                blob_cx = int(centroids[largest_idx][0])
                blob_cy = int(centroids[largest_idx][1])

        # 选择更高的: 原始像素比 vs 最大连通域比
        # 台面场景: blob_ratio ≈ raw_ratio (大部分暗像素连成一片)
        # 噪声场景: blob_ratio << raw_ratio (暗像素分散)
        base_ratio = max(raw_ratio * 0.7, blob_ratio)

        # 均匀性加分: V_std 和 S_std 都低 → 真正均匀暗色, 更可能是台面
        uniformity_bonus = 0.0
        if v_std < 25 and s_std < 30:
            uniformity_bonus = 0.15
        elif v_std < 35 and s_std < 40:
            uniformity_bonus = 0.08

        effective_ratio = min(1.0, base_ratio + uniformity_bonus)

        # 计算质心 (优先用最大连通域质心)
        cx_full = w // 2
        cy_full = h // 2
        direction = 0.0

        if blob_ratio > 0.05:
            cx_full = blob_cx + margin_x
            cy_full = blob_cy + margin_y
            direction = (cx_full - w / 2) / (w / 2)
        elif black_pixels > 100:
            moments = cv2.moments(black_mask)
            if moments['m00'] > 0:
                cx_roi = int(moments['m10'] / moments['m00'])
                cy_roi = int(moments['m01'] / moments['m00'])
                cx_full = cx_roi + margin_x
                cy_full = cy_roi + margin_y
                direction = (cx_full - w / 2) / (w / 2)

        # 时间 EMA 平滑: 减少帧间抖动, 让 ratio 和 direction 更稳定
        ratio_alpha = getattr(config, 'DROP_RATIO_EMA', 0.4)
        dir_alpha = getattr(config, 'DROP_DIR_EMA', 0.3)

        if not self._drop_ema_initialized:
            # 首帧初始化
            self._drop_ratio_ema = effective_ratio
            self._drop_dir_ema = direction
            self._drop_ema_initialized = True
        else:
            self._drop_ratio_ema = (ratio_alpha * effective_ratio
                                    + (1 - ratio_alpha) * self._drop_ratio_ema)
            self._drop_dir_ema = (dir_alpha * direction
                                  + (1 - dir_alpha) * self._drop_dir_ema)

        smoothed_ratio = self._drop_ratio_ema
        smoothed_dir = self._drop_dir_ema

        dbg_info = {
            'v_mean': v_mean, 'v_std': v_std, 's_std': s_std,
            'v_p25': v_p25, 'v_th': adaptive_v_max,
            'raw_ratio': raw_ratio, 'blob_ratio': blob_ratio,
            'bonus': uniformity_bonus,
            'instant_ratio': effective_ratio,
            'instant_dir': direction,
        }

        return smoothed_ratio, cx_full, cy_full, smoothed_dir, dbg_info

    def get_priority_target(self, friends, enemies, neutrals):
        """
        获取最优先目标 (面积最大=最近优先)
        优先级由 config.PRIORITY_MODE 决定:
          - 'collect': N(中立) > E(敌方) — 收集能量块得分为主
          - 'attack':  E(敌方) > N(中立) — 推敌方能量块为主
        返回: (type_char, target) 或 ('X', None)
        """
        mode = getattr(config, 'PRIORITY_MODE', 'collect')
        if mode == 'collect':
            if neutrals:
                return 'N', neutrals[0]
            if enemies:
                return 'E', enemies[0]
        else:
            if enemies:
                return 'E', enemies[0]
            if neutrals:
                return 'N', neutrals[0]
        return 'X', None

    def draw_targets(self, frame, targets, my_color='b'):
        """在帧上绘制所有目标的边框、标签和方向箭头 (调试用)"""
        if my_color == 'b':
            friend_name, enemy_name = 'blue', 'yellow'
        else:
            friend_name, enemy_name = 'yellow', 'blue'

        color_bgr = {
            'blue':   (255, 100,   0),
            'yellow': (  0, 230, 255),
            'white':  (180, 180, 180),
        }

        for t in targets:
            bgr = color_bgr.get(t.color, (0, 255, 0))
            cv2.rectangle(frame, (t.x, t.y), (t.x + t.w, t.y + t.h), bgr, 2)

            tag = ('ENEMY'   if t.color == enemy_name else
                   'FRIEND'  if t.color == friend_name else 'NEUTRAL')
            label = f'{tag} a={t.area:.0f} dir={t.direction:+.2f}'
            cv2.putText(frame, label, (t.x, t.y - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, bgr, 1)
            cv2.drawMarker(frame, (t.cx, t.cy), bgr, cv2.MARKER_CROSS, 12, 2)

            # 敌方目标: 从画面中心画引导箭头
            if t.color == enemy_name:
                fcx = config.CAMERA_WIDTH  // 2
                fcy = config.CAMERA_HEIGHT // 2
                cv2.arrowedLine(frame, (fcx, fcy), (t.cx, t.cy),
                                (0, 0, 255), 2, tipLength=0.05)

        # 画面中心十字
        cx = config.CAMERA_WIDTH  // 2
        cy = config.CAMERA_HEIGHT // 2
        cv2.line(frame, (cx - 20, cy), (cx + 20, cy), (0, 255, 0), 1)
        cv2.line(frame, (cx, cy - 20), (cx, cy + 20), (0, 255, 0), 1)

        return frame


class TagDetector:
    """AprilTag / QR 码检测器 — 收集模式下识别能量块标签。

    默认使用 OpenCV 内置 ArUco (DICT_APRILTAG_36h11, 零依赖)。
    如需 AprilTag: pip install pupil-apriltags, 修改 backend='apriltag'。
    """

    # Tag ID → 类型映射
    @staticmethod
    def classify_tag(tag_id, my_color):
        """根据 Tag ID 和己方颜色返回目标类型字符。
        tag_id: 0=中立, 1=蓝方, 2=黄方
        返回: 'E'=敌方, 'F'=友方, 'N'=中立
        """
        tag_id = int(tag_id)
        neutral_id = getattr(config, 'TAG_ID_NEUTRAL', 0)
        blue_id = getattr(config, 'TAG_ID_BLUE', 1)
        yellow_id = getattr(config, 'TAG_ID_YELLOW', 2)
        if tag_id == neutral_id:
            return 'N'
        if my_color == 'b':
            return 'F' if tag_id == blue_id else 'E'
        else:
            return 'F' if tag_id == yellow_id else 'E'

    def __init__(self, backend=None):
        self.backend = backend or getattr(config, 'TAG_BACKEND', 'apriltag')
        if self.backend == 'qr':
            self._qr = cv2.QRCodeDetector()
        elif self.backend == 'apriltag':
            # 优先 dt-apriltags (预编译 aarch64 wheel), 回退 pupil-apriltags
            try:
                from dt_apriltags import Detector
                self._at = Detector(families='tag36h11')
                print('[TAG] dt-apriltags backend OK')
            except ImportError:
                try:
                    from pupil_apriltags import Detector
                    self._at = Detector(families='tag36h11')
                    print('[TAG] pupil-apriltags backend OK')
                except ImportError:
                    print('[WARN] apriltag libs not installed, falling back to aruco')
                    self.backend = 'aruco'
                    self._init_aruco()
        elif self.backend == 'aruco':
            self._init_aruco()

    def _init_aruco(self):
        """初始化 OpenCV ArUco 后端 (兼容旧版, 不建议用于实际 AprilTag)"""
        try:
            aruco = cv2.aruco
            if hasattr(aruco, 'getPredefinedDictionary'):
                self._aruco_dict = aruco.getPredefinedDictionary(
                    aruco.DICT_APRILTAG_36h11)
            else:
                self._aruco_dict = aruco.Dictionary_get(
                    aruco.DICT_APRILTAG_36h11)
            if hasattr(aruco, 'DetectorParameters'):
                self._aruco_params = aruco.DetectorParameters()
            else:
                self._aruco_params = aruco.DetectorParameters_create()
            self._aruco_detector = (
                aruco.ArucoDetector(self._aruco_dict, self._aruco_params)
                if hasattr(aruco, 'ArucoDetector') else None)
        except Exception as e:
            print(f'[WARN] aruco backend unavailable: {e}, falling back to QR')
            self.backend = 'qr'
            self._qr = cv2.QRCodeDetector()

    def detect_tags(self, frame):
        """
        检测帧中的标签, 返回 Tag 字典列表。
        每个 Tag: {'id': int|str, 'cx': int, 'cy': int, 'area': int}
        """
        if self.backend == 'qr':
            return self._detect_qr(frame)
        elif self.backend == 'aruco':
            return self._detect_aruco(frame)
        elif self.backend == 'apriltag':
            return self._detect_apriltag(frame)
        return []

    def detect_tags_in_rois(self, frame, targets):
        """在颜色目标的 ROI 区域内检测 Tag (比全帧快很多)。

        对每个 Target 的 bounding box 扩展 margin 后裁剪灰度图,
        仅在小区域内检测, 坐标映射回全帧。支持 apriltag 和 aruco 后端。

        Args:
            frame: BGR 全帧
            targets: ColorDetector.detect() 返回的 Target 列表
        Returns:
            list of {'id': int, 'cx': int, 'cy': int, 'area': int}
        """
        if self.backend not in ('apriltag', 'aruco') or not targets:
            return []

        margin = getattr(config, 'TAG_ROI_MARGIN', 40)
        fh, fw = frame.shape[:2]
        all_tags = []

        for t in targets:
            x1 = max(0, t.x - margin)
            y1 = max(0, t.y - margin)
            x2 = min(fw, t.x + t.w + margin)
            y2 = min(fh, t.y + t.h + margin)
            roi_bgr = frame[y1:y2, x1:x2]
            if roi_bgr.size < 300:
                continue
            # 局部灰度转换: 比全帧 cvtColor 快 ~15x (小ROI vs 640x480)
            roi = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY)

            if self.backend == 'apriltag':
                try:
                    results = self._at.detect(roi)
                except Exception:
                    continue
                min_margin = getattr(config, 'TAG_DECISION_MARGIN', 30)
                for r in results:
                    if hasattr(r, 'decision_margin') and r.decision_margin < min_margin:
                        continue
                    cx = int(r.center[0]) + x1
                    cy = int(r.center[1]) + y1
                    pts = r.corners
                    tw = int(pts[:, 0].max() - pts[:, 0].min())
                    th = int(pts[:, 1].max() - pts[:, 1].min())
                    all_tags.append({'id': r.tag_id, 'cx': cx, 'cy': cy,
                                     'area': tw * th})
            else:  # aruco
                try:
                    if getattr(self, '_aruco_detector', None) is not None:
                        corners, ids, _ = self._aruco_detector.detectMarkers(roi)
                    else:
                        corners, ids, _ = cv2.aruco.detectMarkers(
                            roi, self._aruco_dict, parameters=self._aruco_params)
                except Exception:
                    continue
                if ids is None:
                    continue
                for i, marker_id in enumerate(ids.flatten()):
                    pts = corners[i][0]
                    cx = int(pts[:, 0].mean()) + x1
                    cy = int(pts[:, 1].mean()) + y1
                    tw = int(pts[:, 0].max() - pts[:, 0].min())
                    th = int(pts[:, 1].max() - pts[:, 1].min())
                    all_tags.append({'id': int(marker_id), 'cx': cx, 'cy': cy,
                                     'area': tw * th})
        return all_tags

    def _detect_qr(self, frame):
        data, points, _ = self._qr.detectAndDecode(frame)
        if not data or points is None:
            return []
        pts = points[0]
        cx = int(pts[:, 0].mean())
        cy = int(pts[:, 1].mean())
        w = int(pts[:, 0].max() - pts[:, 0].min())
        h = int(pts[:, 1].max() - pts[:, 1].min())
        return [{'id': data, 'cx': cx, 'cy': cy, 'area': w * h}]

    def _detect_aruco(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        try:
            if getattr(self, '_aruco_detector', None) is not None:
                corners, ids, _ = self._aruco_detector.detectMarkers(gray)
            else:
                corners, ids, _ = cv2.aruco.detectMarkers(
                    gray, self._aruco_dict, parameters=self._aruco_params)
        except Exception:
            return []
        tags = []
        if ids is not None:
            for i, marker_id in enumerate(ids.flatten()):
                pts = corners[i][0]
                cx = int(pts[:, 0].mean())
                cy = int(pts[:, 1].mean())
                w = int(pts[:, 0].max() - pts[:, 0].min())
                h = int(pts[:, 1].max() - pts[:, 1].min())
                tags.append({'id': int(marker_id), 'cx': cx, 'cy': cy,
                             'area': w * h})
        return tags

    def _detect_apriltag(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        try:
            results = self._at.detect(gray)
        except Exception:
            return []
        tags = []
        min_margin = getattr(config, 'TAG_DECISION_MARGIN', 30)
        for r in results:
            if hasattr(r, 'decision_margin') and r.decision_margin < min_margin:
                continue
            cx, cy = int(r.center[0]), int(r.center[1])
            pts = r.corners
            w = int(pts[:, 0].max() - pts[:, 0].min())
            h = int(pts[:, 1].max() - pts[:, 1].min())
            tags.append({'id': r.tag_id, 'cx': cx, 'cy': cy,
                         'area': w * h})
        return tags
