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

# ============ HSV 颜色阈值 ============
HSV_BLUE = {
    'lower': np.array([100, 120, 60]),
    'upper': np.array([130, 255, 255]),
}

HSV_YELLOW = {
    'lower': np.array([22, 120, 100]),
    'upper': np.array([38, 255, 255]),
}

HSV_WHITE = {
    'lower': np.array([0, 0, 210]),
    'upper': np.array([180, 25, 255]),   # S上限 30->25, 减少反光误检
}

# ============ 检测参数 ============
MIN_CONTOUR_AREA = 800
MAX_CONTOUR_AREA = 150000
MIN_ASPECT_RATIO = 0.3
MAX_ASPECT_RATIO = 3.0
MAX_TARGETS = 10

# ============ 串口通信 (支持环境变量覆盖) ============
UART_PORT = os.environ.get('VISION_UART', '/dev/ttyAS1')
UART_BAUD = 115200
UART_TIMEOUT = 0.01

# ============ 图像预处理 ============
USE_CLAHE = os.environ.get('VISION_CLAHE', '1') != '0'

# ============ 帧率控制 ============
TARGET_FPS = int(os.environ.get('VISION_FPS', '30'))

# ============ 目标优先级模式 ============
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
