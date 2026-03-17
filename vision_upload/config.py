"""
视觉系统配置 - 轮式格斗机器人
所有 HSV 阈值需在赛场环境下重新标定
"""
import numpy as np
import subprocess

# ============ 摄像头自动探测 ============
def find_camera():
    """自动查找 USB 摄像头设备号"""
    try:
        out = subprocess.check_output(['v4l2-ctl', '--list-devices'],
                                       stderr=subprocess.STDOUT, text=True)
        lines = out.split('\n')
        for i, line in enumerate(lines):
            if 'USB' in line or 'Camera' in line or 'camera' in line:
                # 下一行是设备路径
                for j in range(i+1, min(i+3, len(lines))):
                    dev = lines[j].strip()
                    if dev.startswith('/dev/video'):
                        return dev
    except Exception:
        pass
    # 回退: 尝试常见路径
    import os
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
    'lower': np.array([0, 0, 200]),
    'upper': np.array([180, 40, 255]),
}

# ============ 检测参数 ============
MIN_CONTOUR_AREA = 800
MAX_CONTOUR_AREA = 150000
MIN_ASPECT_RATIO = 0.3
MAX_ASPECT_RATIO = 3.0
MAX_TARGETS = 10

# ============ 串口通信 ============
UART_PORT = '/dev/ttyS10'
UART_BAUD = 115200
UART_TIMEOUT = 0.01

# ============ 图像预处理 ============
USE_CLAHE = True     # CLAHE 光照均衡 (对V通道), 赛场灯光不均时推荐开启

# ============ 帧率控制 ============
TARGET_FPS = 30

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
