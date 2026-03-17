"""
颜色检测器 v3 - CLAHE光照均衡 + 形态学过滤 + 帧间跟踪平滑

改进点 (vs v2):
  - detect() 在 HSV V通道上应用 CLAHE, 大幅提升强背光/暗光鲁棒性
  - set_camera_props() 静态方法: 锁定相机曝光/白平衡, 避免自动调整色偏
  - 优先级排序: 面积最大(最近)优先, 同时输出距离估计
"""
import cv2
import numpy as np
import config


class Target:
    __slots__ = ('color', 'cx', 'cy', 'x', 'y', 'w', 'h', 'area',
                 'solidity', 'distance', 'direction')

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
        # 负值=目标在左半边, 正值=目标在右半边
        self.direction = (cx - config.CAMERA_WIDTH / 2) / (config.CAMERA_WIDTH / 2)
        # 基于面积的粗略距离估计 [0, 1], 越大越近
        self.distance = min(1.0, area / config.MAX_CONTOUR_AREA)

    def __repr__(self):
        return (f'{self.color}({self.cx},{self.cy} {self.w}x{self.h} '
                f'a={self.area:.0f} dir={self.direction:+.2f} dist={self.distance:.2f})')


class Tracker:
    """简易帧间平滑跟踪器, 用指数平滑消除检测抖动"""

    def __init__(self, smoothing=0.4, max_dist=80, max_lost=5):
        self.smoothing = smoothing  # 历史权重, 越大惯性越强
        self.max_dist = max_dist    # 同一目标最大帧间位移 (像素)
        self.max_lost = max_lost    # 允许丢失帧数
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
                if tid in used_tracks or tr['color'] != t.color:
                    continue
                d = ((tr['cx'] - t.cx) ** 2 + (tr['cy'] - t.cy) ** 2) ** 0.5
                if d < best_dist:
                    best_dist = d
                    best_id = tid

            if best_id is not None:
                tr = self._tracks[best_id]
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
                }
                used_tracks.add(tid)
                result.append(t)

        # 清除长时间丢失的轨迹
        stale = [tid for tid, tr in self._tracks.items()
                 if tid not in used_tracks and tr['lost'] >= self.max_lost]
        for tid in stale:
            del self._tracks[tid]
        for tid, tr in self._tracks.items():
            if tid not in used_tracks:
                tr['lost'] += 1

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
        注意: 并非所有摄像头都支持这些属性, 失败时静默跳过。
        """
        cap.set(cv2.CAP_PROP_AUTO_WB, 0)          # 关闭自动白平衡
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)    # 切换为手动曝光模式

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
    def _detect_color(self, hsv, color_name, hsv_range):
        """在HSV图中检测一种颜色，返回按面积降序排列的 Target 列表"""
        mask = cv2.inRange(hsv, hsv_range['lower'], hsv_range['upper'])
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  self._kernel_open)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self._kernel_close)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        targets = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < config.MIN_CONTOUR_AREA or area > config.MAX_CONTOUR_AREA:
                continue

            x, y, w, h = cv2.boundingRect(cnt)
            aspect = w / h if h > 0 else 0
            if aspect < config.MIN_ASPECT_RATIO or aspect > config.MAX_ASPECT_RATIO:
                continue

            # 凸包填充度过滤: 能量块是实心色块, solidity 应 ≥ 0.5
            hull_area = cv2.contourArea(cv2.convexHull(cnt))
            solidity = area / hull_area if hull_area > 0 else 0
            if solidity < 0.5:
                continue

            cx = x + w // 2
            cy = y + h // 2
            targets.append(Target(color_name, cx, cy, x, y, w, h, area, solidity))

        targets.sort(key=lambda t: t.area, reverse=True)
        return targets[:config.MAX_TARGETS]

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------
    def detect(self, frame):
        """
        检测一帧中全部颜色目标。
        流程: 高斯模糊 → HSV + CLAHE均衡 → 三色分割 → 轮廓过滤 → 跟踪平滑
        返回: Target 列表 (已平滑)
        """
        hsv = self._preprocess(frame)

        all_targets = []
        all_targets.extend(self._detect_color(hsv, 'blue',   config.HSV_BLUE))
        all_targets.extend(self._detect_color(hsv, 'yellow', config.HSV_YELLOW))
        all_targets.extend(self._detect_color(hsv, 'white',  config.HSV_WHITE))

        if self._tracker:
            all_targets = self._tracker.update(all_targets)

        return all_targets

    def classify(self, targets, my_color):
        """根据己方颜色将目标分类为 friends/enemies/neutrals"""
        if my_color == 'b':
            friend_color, enemy_color = 'blue', 'yellow'
        else:
            friend_color, enemy_color = 'yellow', 'blue'
        friends  = [t for t in targets if t.color == friend_color]
        enemies  = [t for t in targets if t.color == enemy_color]
        neutrals = [t for t in targets if t.color == 'white']
        return friends, enemies, neutrals

    def get_priority_target(self, friends, enemies, neutrals):
        """
        获取最优先攻击目标 (面积最大=最近优先)
        优先级: 敌方 > 中立 > None
        返回: (type_char, target) 或 ('X', None)
        """
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
