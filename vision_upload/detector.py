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
    """帧间平滑跟踪器, 支持速度预测 + 面积约束 + 指数平滑"""

    def __init__(self, smoothing=0.15, max_dist=150, max_lost=8,
                 color_switch_frames=3, enable_prediction=True):
        self.smoothing = smoothing
        self.max_dist = max_dist
        self.max_lost = max_lost
        self.color_switch_frames = color_switch_frames
        self.enable_prediction = enable_prediction
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
                # 用预测位置匹配, 而非上帧位置
                px, py = self._predict_pos(tr)
                d = ((px - t.cx) ** 2 + (py - t.cy) ** 2) ** 0.5
                # 面积变化约束: 面积突变>3倍则不匹配
                if tr['area'] > 0:
                    area_ratio = t.area / tr['area']
                    if area_ratio > 3.0 or area_ratio < 0.33:
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

                # 更新速度 (当前位置 - 上帧位置)
                tr['vx'] = t.cx - tr['cx']
                tr['vy'] = t.cy - tr['cy']

                s = self.smoothing
                tr['cx'] = int(tr['cx'] * s + t.cx * (1 - s))
                tr['cy'] = int(tr['cy'] * s + t.cy * (1 - s))
                tr['area'] = tr['area'] * s + t.area * (1 - s)
                tr['lost'] = 0
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
                }
                used_tracks.add(tid)
                result.append(t)

        # 清除丢失轨迹, 丢失时速度衰减
        for tid, tr in list(self._tracks.items()):
            if tid not in used_tracks:
                tr['lost'] += 1
                tr['vx'] = int(tr['vx'] * 0.5)
                tr['vy'] = int(tr['vy'] * 0.5)
                if tr['lost'] > self.max_lost:
                    del self._tracks[tid]

        return result


class ColorDetector:
    def __init__(self, enable_tracking=True):
        # 形态学核: open去噪点, close填空洞 (缩小以提升帧率)
        self._kernel_open  = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        self._kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self._tracker = Tracker(
            smoothing=getattr(config, 'TRACKER_SMOOTHING', 0.15),
            max_dist=getattr(config, 'TRACKER_MAX_DIST', 150),
            max_lost=getattr(config, 'TRACKER_MAX_LOST', 8),
            enable_prediction=getattr(config, 'TRACKER_PREDICT', True),
        ) if enable_tracking else None
        # CLAHE: 自适应直方图均衡
        clip = getattr(config, 'CLAHE_CLIP_LIMIT', 1.5)
        tile = getattr(config, 'CLAHE_TILE_SIZE', 4)
        self._clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=(tile, tile))
        self._clahe_s = cv2.createCLAHE(clipLimit=1.0, tileGridSize=(tile, tile))

    # ------------------------------------------------------------------
    # 相机属性锁定 (已废弃 — 统一由 config.setup_camera() 处理)
    # ------------------------------------------------------------------
    @staticmethod
    def set_camera_props(cap):
        """[已废弃] 相机参数现由 config.setup_camera() 统一设置。"""
        pass

    # ------------------------------------------------------------------
    # 预处理: CLAHE + 高斯模糊 → HSV
    # ------------------------------------------------------------------
    def _preprocess(self, frame):
        """BGR → HSV, CLAHE均衡V+S通道以应对光照不均和低饱和度"""
        blurred = cv2.GaussianBlur(frame, (3, 3), 0)
        hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)

        if getattr(config, 'USE_CLAHE', True):
            h, s, v = cv2.split(hsv)
            v = self._clahe.apply(v)
            # S通道CLAHE: 增强低饱和度目标的可检测性
            if getattr(config, 'CLAHE_S_CHANNEL', True):
                s = self._clahe_s.apply(s)
            hsv = cv2.merge([h, s, v])

        # 缓存帧平均亮度, 供自适应阈值使用
        self._frame_v_mean = hsv[:, :, 2].mean()

        return hsv

    # ------------------------------------------------------------------
    # 单色检测
    # ------------------------------------------------------------------
    def _detect_color(self, hsv, color_name, hsv_range, exclusion_mask=None):
        """在HSV图中检测一种颜色，返回按面积降序排列的 Target 列表"""
        lower = hsv_range['lower'].copy()
        upper = hsv_range['upper'].copy()

        # 自适应V阈值: 暗环境自动放宽V下限, 亮环境收紧减少噪声
        if (getattr(config, 'ADAPTIVE_V_THRESHOLD', True)
                and color_name != 'white'):
            v_mean = getattr(self, '_frame_v_mean', 128)
            if v_mean < 80:
                lower[2] = max(20, lower[2] - 25)   # 暗环境大幅放宽
            elif v_mean < 120:
                lower[2] = max(30, lower[2] - 10)   # 中等环境微调
            elif v_mean > 180:
                lower[2] = min(lower[2] + 15, 120)  # 过亮环境收紧

        mask = cv2.inRange(hsv, lower, upper)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  self._kernel_open)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self._kernel_close)

        # 空间排斥: 减去已被其他颜色占据的区域
        if exclusion_mask is not None:
            mask = cv2.bitwise_and(mask, cv2.bitwise_not(exclusion_mask))

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)

        # 白色/黄色目标使用更严格的过滤阈值
        if color_name == 'white':
            min_area = 1500
        elif color_name == 'yellow':
            min_area = getattr(config, 'MIN_CONTOUR_AREA_YELLOW', config.MIN_CONTOUR_AREA)
        else:
            min_area = config.MIN_CONTOUR_AREA
        base_solidity = 0.65 if color_name == 'white' else 0.3
        min_aspect = 0.5 if color_name == 'white' else config.MIN_ASPECT_RATIO
        max_aspect = 2.0 if color_name == 'white' else config.MAX_ASPECT_RATIO

        targets = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            # 黄色使用专用面积上限，过滤整片暖色背景大轮廓
            max_area = (getattr(config, 'MAX_CONTOUR_AREA_YELLOW', config.MAX_CONTOUR_AREA)
                        if color_name == 'yellow' else config.MAX_CONTOUR_AREA)
            if area < min_area or area > max_area:
                continue

            x, y, w, h = cv2.boundingRect(cnt)

            # 大轮廓(>50000)跳过宽高比检查: 近距离色块可能部分超出画面
            if area < 50000:
                aspect = w / h if h > 0 else 0
                if aspect < min_aspect or aspect > max_aspect:
                    continue

            hull_area = cv2.contourArea(cv2.convexHull(cnt))
            solidity = area / hull_area if hull_area > 0 else 0
            # 大面积轮廓放宽solidity: 多目标合并/边缘截断时轮廓不规则
            min_solidity = base_solidity if area < 5000 else 0.15
            if solidity < min_solidity:
                continue

            # 二阶段 S 均值验证 (黄色专用):
            # 宽 H mask 捕获所有候选, 用轮廓内 S 均值区分真黄色 vs 蓝色冒充
            s_mean_min = getattr(config, 'YELLOW_S_MEAN_MIN', 0)
            if color_name == 'yellow' and s_mean_min > 0:
                contour_mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
                cv2.drawContours(contour_mask, [cnt], -1, 255, -1)
                s_pixels = hsv[:, :, 1][contour_mask > 0]
                if len(s_pixels) > 0 and s_pixels.mean() < s_mean_min:
                    continue  # S 均值过低, 判为蓝色冒充

            cx = x + w // 2
            cy = y + h // 2
            targets.append(Target(color_name, cx, cy, x, y, w, h, area, solidity))

        targets.sort(key=lambda t: t.area, reverse=True)
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
    def _detect_close_range(self, hsv):
        """近距离回退: 中心ROI颜色占比检测 (色块充满画面时轮廓检测失效)"""
        fh, fw = hsv.shape[:2]
        roi = hsv[fh // 4:3 * fh // 4, fw // 4:3 * fw // 4]
        roi_pixels = roi.shape[0] * roi.shape[1]
        if roi_pixels == 0:
            return []

        for color_name, hsv_range in [('blue', config.HSV_BLUE),
                                       ('yellow', config.HSV_YELLOW)]:
            lower = hsv_range['lower'].copy()
            if color_name != 'yellow':
                lower[1] = max(0, lower[1] - 30)
            mask = cv2.inRange(roi, lower, hsv_range['upper'])
            ratio = cv2.countNonZero(mask) / roi_pixels
            if ratio > 0.30:
                # 二阶段 S 均值验证 (黄色专用)
                s_mean_min = getattr(config, 'YELLOW_S_MEAN_MIN', 0)
                if color_name == 'yellow' and s_mean_min > 0:
                    s_pixels = roi[:, :, 1][mask > 0]
                    if len(s_pixels) > 0 and s_pixels.mean() < s_mean_min:
                        continue  # S 均值过低, 蓝色冒充
                cx, cy = fw // 2, fh // 2
                area = int(ratio * fw * fh)
                return [Target(color_name, cx, cy,
                               fw // 4, fh // 4, fw // 2, fh // 2, area)]
        return []

    def detect(self, frame):
        """
        检测一帧中蓝色和黄色目标 (双色模式)。
        流程: 预处理 → 蓝/黄轮廓检测 → (无结果时) ROI近距离回退 → 跟踪平滑
        """
        hsv = self._preprocess(frame)

        blue_targets = self._detect_color(hsv, 'blue', config.HSV_BLUE)
        yellow_targets = self._detect_color(hsv, 'yellow', config.HSV_YELLOW)

        all_targets = blue_targets + yellow_targets

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
            targets = self._detect_close_range_single(hsv, color_name, hsv_range)

        if self._tracker:
            targets = self._tracker.update(targets)

        return targets

    def _detect_close_range_single(self, hsv, color_name, hsv_range):
        """单色近距离回退: 中心ROI颜色占比检测"""
        fh, fw = hsv.shape[:2]
        roi = hsv[fh // 4:3 * fh // 4, fw // 4:3 * fw // 4]
        roi_pixels = roi.shape[0] * roi.shape[1]
        if roi_pixels == 0:
            return []

        lower = hsv_range['lower'].copy()
        # 蓝色放宽S以检测近距离大面积; 黄色不放宽避免背景误触发
        if color_name != 'yellow':
            lower[1] = max(0, lower[1] - 30)
        mask = cv2.inRange(roi, lower, hsv_range['upper'])
        ratio = cv2.countNonZero(mask) / roi_pixels
        min_ratio = 0.30
        if ratio > min_ratio:
            # 二阶段 S 均值验证 (黄色专用)
            s_mean_min = getattr(config, 'YELLOW_S_MEAN_MIN', 0)
            if color_name == 'yellow' and s_mean_min > 0:
                s_pixels = roi[:, :, 1][mask > 0]
                if len(s_pixels) > 0 and s_pixels.mean() < s_mean_min:
                    return []  # S 均值过低, 蓝色冒充

            cx, cy = fw // 2, fh // 2
            area = int(ratio * fw * fh)
            return [Target(color_name, cx, cy,
                           fw // 4, fh // 4, fw // 2, fh // 2, area)]
        return []

    def classify(self, targets, my_color):
        """根据己方颜色将目标分类为 friends/enemies (白色已移除)"""
        if my_color == 'b':
            friend_color, enemy_color = 'blue', 'yellow'
        else:
            friend_color, enemy_color = 'yellow', 'blue'
        friends  = [t for t in targets if t.color == friend_color]
        enemies  = [t for t in targets if t.color == enemy_color]
        neutrals = []  # 白色检测已移除
        return friends, enemies, neutrals

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

    默认使用 OpenCV 内置 QRCodeDetector (零依赖)。
    如需 AprilTag: pip install pupil-apriltags, 修改 backend='apriltag'。
    """

    def __init__(self, backend='qr'):
        self.backend = backend
        if backend == 'qr':
            self._qr = cv2.QRCodeDetector()
        elif backend == 'aruco':
            try:
                aruco = cv2.aruco
                # OpenCV 4.7+ 新 API
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
                # OpenCV 4.8+ ArucoDetector 对象
                self._aruco_detector = (
                    aruco.ArucoDetector(self._aruco_dict, self._aruco_params)
                    if hasattr(aruco, 'ArucoDetector') else None)
            except Exception as e:
                print(f'[WARN] aruco backend unavailable: {e}, falling back to QR')
                self.backend = 'qr'
                self._qr = cv2.QRCodeDetector()
        elif backend == 'apriltag':
            try:
                from pupil_apriltags import Detector
                self._at = Detector(families='tag36h11')
            except ImportError:
                print('[WARN] pupil-apriltags not installed, falling back to QR')
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
        results = self._at.detect(gray)
        tags = []
        for r in results:
            cx, cy = int(r.center[0]), int(r.center[1])
            pts = r.corners
            w = int(pts[:, 0].max() - pts[:, 0].min())
            h = int(pts[:, 1].max() - pts[:, 1].min())
            tags.append({'id': r.tag_id, 'cx': cx, 'cy': cy,
                         'area': w * h})
        return tags
