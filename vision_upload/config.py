"""
视觉系统配置 - 轮式格斗机器人 v4
所有 HSV 阈值需在赛场环境下重新标定
支持环境变量覆盖, 便于板端快速调试
"""
import os
import numpy as np
import subprocess

# ============ 摄像头自动探测 ============
def find_camera():
    """自动查找 USB 摄像头设备号, 优先环境变量"""
    env_cam = os.environ.get('VISION_CAMERA')
    if env_cam:
        return env_cam
    try:
        out = subprocess.check_output(['v4l2-ctl', '--list-devices'],
                                       stderr=subprocess.STDOUT, text=True)
        lines = out.split('\n')
        for i, line in enumerate(lines):
            if 'USB' in line or 'Camera' in line or 'camera' in line:
                for j in range(i+1, min(i+3, len(lines))):
                    dev = lines[j].strip()
                    if dev.startswith('/dev/video'):
                        return dev
    except Exception:
        pass
    for idx in [0, 1, 2]:
        path = f'/dev/video{idx}'
        if os.path.exists(path):
            return path
    return '/dev/video0'

CAMERA_DEVICE = find_camera()
CAMERA_WIDTH = 640
CAMERA_HEIGHT = 480
CAMERA_FPS = 30
CAMERA_FOURCC = os.environ.get('VISION_FOURCC', 'MJPG')  # MJPG=硬解, YUYV=软解

# ============ 白平衡 ============
AUTO_WB = int(os.environ.get('VISION_AUTO_WB', '0'))       # 0=固定WB, 1=自动WB
# WB 按队伍颜色动态设置: 蓝队4200K, 黄队6000K (Realtek相机色彩特性)
# 可通过环境变量覆盖
WB_TEMPERATURE = int(os.environ.get('VISION_WB_TEMP', '0'))  # 0=自动按颜色选择
WB_BLUE = 4200   # 蓝队最佳色温
WB_YELLOW = 6000  # 黄队最佳色温


def setup_camera(cap):
    """
    统一相机参数设置 — main.py / calibrate.py / stream.py 共用。
    返回 (actual_width, actual_height)。
    """
    import cv2
    global CAMERA_WIDTH, CAMERA_HEIGHT

    # 编码格式
    fourcc_str = CAMERA_FOURCC
    if fourcc_str and fourcc_str != 'YUYV':
        cap.set(cv2.CAP_PROP_FOURCC,
                cv2.VideoWriter_fourcc(*fourcc_str))

    # 分辨率 + 帧率 + 缓冲
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, CAMERA_FPS)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    # 曝光: 必须自动(3), 手动(1)会导致画面过暗
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3)

    # 白平衡: 固定色温消除暖色调偏移
    cap.set(cv2.CAP_PROP_AUTO_WB, AUTO_WB)
    if AUTO_WB == 0 and WB_TEMPERATURE > 0:
        cap.set(cv2.CAP_PROP_WB_TEMPERATURE, WB_TEMPERATURE)

    # 丢弃初始脏帧
    for _ in range(5):
        cap.read()

    # 回写实际分辨率 (新相机可能不支持请求的分辨率)
    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if actual_w > 0 and actual_h > 0:
        CAMERA_WIDTH = actual_w
        CAMERA_HEIGHT = actual_h

    actual_fps = cap.get(cv2.CAP_PROP_FPS)
    actual_fc = int(cap.get(cv2.CAP_PROP_FOURCC))
    fc_str = ''.join(chr((actual_fc >> (8 * i)) & 0xFF) for i in range(4))
    print(f'[config] Camera: {actual_w}x{actual_h} @{actual_fps:.0f}fps '
          f'fourcc={fc_str} WB={WB_TEMPERATURE}K AE=auto')

    return actual_w, actual_h

# ============ HSV 颜色阈值 ============
HSV_BLUE = {
    'lower': np.array([100, 50, 40]),
    'upper': np.array([130, 255, 255]),
}

HSV_YELLOW = {
    'lower': np.array([20, 40, 40]),
    'upper': np.array([42, 255, 255]),
}
# 黄色二阶段验证: 轮廓内 S 均值必须 > 此值, 否则判为蓝色冒充
# (Realtek相机在WB=6000K下: 黄色S≈134, 蓝色S≈97, 阈值115分离)
YELLOW_S_MEAN_MIN = int(os.environ.get('VISION_YELLOW_S_MEAN', '115'))

HSV_WHITE = {
    'lower': np.array([0, 0, 230]),
    'upper': np.array([180, 18, 255]),   # S:25→18 V:210→230, 减少高光误检
}

# ============ 检测参数 ============
MIN_CONTOUR_AREA = 600
MIN_CONTOUR_AREA_YELLOW = 20000  # 黄色专用: 过滤背景暖色调小噪声
MAX_CONTOUR_AREA_YELLOW = 70000  # 黄色专用: 过滤整片暖色背景大轮廓
MAX_CONTOUR_AREA = 280000   # 280000 ≈ 91% of 640×480, 支持近距离大面积色块
MIN_ASPECT_RATIO = 0.3
MAX_ASPECT_RATIO = 3.0
MAX_TARGETS = 10

# ============ 串口通信 (支持环境变量覆盖) ============
UART_PORT = os.environ.get('VISION_UART', '/dev/ttyAS1')
UART_BAUD = 115200
UART_TIMEOUT = 0.01

# ============ 图像预处理 ============
USE_CLAHE = os.environ.get('VISION_CLAHE', '1') != '0'
CLAHE_CLIP_LIMIT = float(os.environ.get('VISION_CLAHE_CLIP', '1.5'))
CLAHE_TILE_SIZE = int(os.environ.get('VISION_CLAHE_TILE', '4'))
CLAHE_S_CHANNEL = os.environ.get('VISION_CLAHE_S', '0') != '0'  # 默认关闭S通道CLAHE (Realtek相机S分布被压缩)
ADAPTIVE_V_THRESHOLD = os.environ.get('VISION_ADAPTIVE_V', '1') != '0'

# ============ 跟踪器参数 ============
TRACKER_SMOOTHING = float(os.environ.get('VISION_TRACK_SMOOTH', '0.30'))
TRACKER_MAX_DIST = int(os.environ.get('VISION_TRACK_MAXDIST', '100'))
TRACKER_MAX_LOST = int(os.environ.get('VISION_TRACK_MAXLOST', '8'))
TRACKER_PREDICT = os.environ.get('VISION_TRACK_PREDICT', '1') != '0'

# ============ 帧率控制 ============
TARGET_FPS = int(os.environ.get('VISION_FPS', '30'))

# ============ 检测模式 ============
# True: 只检测己方颜色(看到己方块后退, 其余依赖光电传感器)
# False: 双色检测(蓝+黄全部检测, 分类友/敌)
DETECT_OWN_ONLY = os.environ.get('VISION_OWN_ONLY', '1') != '0'

# ============ 目标优先级模式 (仅DETECT_OWN_ONLY=False时生效) ============
# 'collect': N(中立)>E(敌方) — 以收集能量块得分为主
# 'attack':  E(敌方)>N(中立) — 以推敌方能量块为主
PRIORITY_MODE = os.environ.get('VISION_PRIORITY', 'collect')

# ============ 距离分段阈值 ============
DISTANCE_NEAR  = 12000   # 面积 > 此值 = 近距离
DISTANCE_FAR   = 3000    # 面积 < 此值 = 远距离

# ============ Watchdog ============
WATCHDOG_TIMEOUT = 30.0  # 连续无有效帧超时(s), 触发相机重启

# ============ 标定文件自动加载 ============
def _load_calibration():
    import json, os
    cal_file = os.path.join(os.path.dirname(__file__), 'hsv_calibration.json')
    if not os.path.exists(cal_file):
        return
    try:
        with open(cal_file) as f:
            cal = json.load(f)
        global HSV_BLUE, HSV_YELLOW, HSV_WHITE
        if 'blue' in cal:
            HSV_BLUE = {'lower': np.array(cal['blue']['lower']),
                        'upper': np.array(cal['blue']['upper'])}
        if 'yellow' in cal:
            HSV_YELLOW = {'lower': np.array(cal['yellow']['lower']),
                          'upper': np.array(cal['yellow']['upper'])}
        if 'white' in cal:
            HSV_WHITE = {'lower': np.array(cal['white']['lower']),
                         'upper': np.array(cal['white']['upper'])}
        print(f'[config] Loaded calibration from {cal_file}')
    except Exception as e:
        print(f'[config] Calibration load failed: {e}')

_load_calibration()
