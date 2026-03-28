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
    """简易帧间平滑跟踪器, 用指数平滑消除检测抖动"""

    def __init__(self, smoothing=0.4, max_dist=80, max_lost=5,
                 color_switch_frames=3):
        self.smoothing = smoothing  # 历史权重, 越大惯性越强
        self.max_dist = max_dist    # 同一目标最大帧间位移 (像素)
        self.max_lost = max_lost    # 允许丢失帧数
        self.color_switch_frames = color_switch_frames  # 颜色切换需连续确认帧数
        self._tracks = {}
        self._next_id = 0

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
                # 允许跨颜色匹配 (同一物体颜色可能跳变)
                d = ((tr['cx'] - t.cx) ** 2 + (tr['cy'] - t.cy) ** 2) ** 0.5
                if d < best_dist:
                    best_dist = d
                    best_id = tid

            if best_id is not None:
                tr = self._tracks[best_id]

                # 颜色切换冷却: 连续 N 帧新颜色才允许切换
                if t.color != tr['color']:
                    tr['color_switch_count'] = tr.get('color_switch_count', 0) + 1
                    if tr['color_switch_count'] < self.color_switch_frames:
                        # 保持旧颜色, 但更新位置
                        t = Target(tr['color'], t.cx, t.cy, t.x, t.y,
                                   t.w, t.h, t.area, t.solidity)
                    else:
                        # 确认切换
                        tr['color'] = t.color
                        tr['color_switch_count'] = 0
                else:
                    tr['color_switch_count'] = 0

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
                }
                used_tracks.add(tid)
                result.append(t)

        # 清除长时间丢失的轨迹, 并递增未匹配track的lost计数
        for tid, tr in list(self._tracks.items()):
            if tid not in used_tracks:
                tr['lost'] += 1
                if tr['lost'] > self.max_lost:
                    del self._tracks[tid]

        return result


class ColorDetector:
    def __init__(self, enable_tracking=True):
        # 形态学核: open去噪点, close填空洞
        self._kernel_open  = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self._kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
        self._tracker = Tracker() if enable_tracking else None
        # CLAHE: 对V通道做自适应直方图均衡, clipLimit越小越保守
        self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

    # ------------------------------------------------------------------
    # 相机属性锁定 (静态方法, 在 Camera.open() 中调用)
    # ------------------------------------------------------------------
    @staticmethod
    def set_camera_props(cap):
        """
        关闭摄像头自动曝光和自动白平衡，减少赛场灯光变化导致的色偏。
        V4L2 约定: CAP_PROP_AUTO_EXPOSURE=1 表示手动, =3 表示自动。
        注意: 并非所有摄像头都支持这些属性, 失败时打印警告。
        """
        ok_wb = cap.set(cv2.CAP_PROP_AUTO_WB, 0)
        ok_exp = cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)
        if not ok_wb:
            print('[WARN] Camera does not support AUTO_WB lock')
        if not ok_exp:
            print('[WARN] Camera does not support AUTO_EXPOSURE lock')

    # ------------------------------------------------------------------
    # 预处理: CLAHE + 高斯模糊 → HSV
    # ------------------------------------------------------------------
    def _preprocess(self, frame):
        """BGR → HSV, 并对V通道做CLAHE均衡以应对光照不均"""
        blurred = cv2.GaussianBlur(frame, (5, 5), 0)
        hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)

        if getattr(config, 'USE_CLAHE', True):
            # 只均衡V(亮度)通道, H/S保持原样
            h, s, v = cv2.split(hsv)
            v = self._clahe.apply(v)
            hsv = cv2.merge([h, s, v])

        return hsv

    # ------------------------------------------------------------------
    # 单色检测
    # ------------------------------------------------------------------
    def _detect_color(self, hsv, color_name, hsv_range, exclusion_mask=None):
        """在HSV图中检测一种颜色，返回按面积降序排列的 Target 列表"""
        mask = cv2.inRange(hsv, hsv_range['lower'], hsv_range['upper'])
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  self._kernel_open)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self._kernel_close)

        # 空间排斥: 减去已被其他颜色占据的区域
        if exclusion_mask is not None:
            mask = cv2.bitwise_and(mask, cv2.bitwise_not(exclusion_mask))

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)

        # 白色目标使用更严格的过滤阈值
        min_area = 1500 if color_name == 'white' else config.MIN_CONTOUR_AREA
        min_solidity = 0.65 if color_name == 'white' else 0.5
        min_aspect = 0.5 if color_name == 'white' else config.MIN_ASPECT_RATIO
        max_aspect = 2.0 if color_name == 'white' else config.MAX_ASPECT_RATIO

        targets = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < min_area or area > config.MAX_CONTOUR_AREA:
                continue

            x, y, w, h = cv2.boundingRect(cnt)
            aspect = w / h if h > 0 else 0
            if aspect < min_aspect or aspect > max_aspect:
                continue

            hull_area = cv2.contourArea(cv2.convexHull(cnt))
            solidity = area / hull_area if hull_area > 0 else 0
            if solidity < min_solidity:
                continue

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
    def detect(self, frame):
        """
        检测一帧中蓝色和黄色目标 (白色已移除, 减少误检)。
        流程: 预处理 → 蓝/黄检测 → 跟踪平滑
        """
        hsv = self._preprocess(frame)

        blue_targets = self._detect_color(hsv, 'blue', config.HSV_BLUE)
        yellow_targets = self._detect_color(hsv, 'yellow', config.HSV_YELLOW)

        all_targets = blue_targets + yellow_targets

        if self._tracker:
            all_targets = self._tracker.update(all_targets)

        return all_targets

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
