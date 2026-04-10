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
# 统一使用 4200K 冷色温 (蓝/黄队通用)
# 原因: WB>4600K 会导致蓝色能量块 H 偏移到黄色范围, 造成严重误检
# 实测: WB=4200K 蓝色检出8.2% 黄色误检2.2%(被排斥掩码消除)
WB_TEMPERATURE = int(os.environ.get('VISION_WB_TEMP', '4200'))
WB_BLUE = 4200   # 蓝队色温 (保留兼容)
WB_YELLOW = 4200  # 黄队色温 (原6000K→4200K, 修复蓝色误检为黄色)


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
    'lower': np.array([100, 60, 40]),
    'upper': np.array([130, 255, 255]),
}

HSV_YELLOW = {
    'lower': np.array([22, 80, 40]),
    'upper': np.array([42, 255, 255]),
}
# 黄色二阶段验证: 轮廓内 S 均值必须 > 此值, 否则判为蓝色冒充
# (Realtek相机在WB=6000K下: 黄色S≈134, 蓝色S≈97, 阈值120分离)
YELLOW_S_MEAN_MIN = int(os.environ.get('VISION_YELLOW_S_MEAN', '70'))

HSV_WHITE = {
    'lower': np.array([0, 0, 230]),
    'upper': np.array([180, 18, 255]),   # S:25→18 V:210→230, 减少高光误检
}

# ============ 检测参数 ============
MIN_CONTOUR_AREA = 600
MIN_CONTOUR_AREA_YELLOW = int(os.environ.get('VISION_YELLOW_MIN_AREA', '600'))    # 降低下限, 检测远距离窄条黄色
MAX_CONTOUR_AREA_YELLOW = int(os.environ.get('VISION_YELLOW_MAX_AREA', '280000'))  # 与蓝色一致, 支持近距离大面积
MAX_CONTOUR_AREA = 280000   # 280000 ≈ 91% of 640×480, 支持近距离大面积色块
MIN_ASPECT_RATIO = 0.3
MAX_ASPECT_RATIO = 3.0
MAX_TARGETS = 10

# ============ 黄色增强过滤 (替代面积暴力阈值) ============
YELLOW_CIRCULARITY_MIN = float(os.environ.get('VISION_YELLOW_CIRC', '0.15'))  # 圆度下限, AprilTag使能量块外形不规则
YELLOW_H_STD_MAX = int(os.environ.get('VISION_YELLOW_H_STD', '25'))           # H通道标准差上限 (放宽, 斜面光照不均)
FRAME_BOTTOM_EXCLUDE = float(os.environ.get('VISION_BOTTOM_EXCL', '0.12'))    # 忽略画面底部12%

# ============ 友方近距离报警 (双色模式防误发F) ============
FRIEND_ALERT_AREA = int(os.environ.get('VISION_FRIEND_ALERT', '15000'))  # 友方面积>此值才发F

# ============ 串口通信 (支持环境变量覆盖) ============
def _find_uart():
    """自动探测 UART 设备 (容错增强版)。

    优先级: 环境变量 > udev符号链接 > USB设备扫描 > GPIO UART
    开机时 USB 枚举可能滞后, 最多重试 UART_PROBE_RETRIES 次。
    """
    import glob
    import time as _time

    max_retries = int(os.environ.get('VISION_UART_RETRIES', '5'))
    retry_interval = 2.0  # 秒

    for attempt in range(max_retries):
        # 1) 环境变量指定
        env_uart = os.environ.get('VISION_UART')
        if env_uart and os.path.exists(env_uart):
            if attempt > 0:
                print(f'[UART] Found {env_uart} on attempt {attempt + 1}')
            return env_uart

        # 2) udev 稳定符号链接 (不受 USB 设备号漂移影响)
        if os.path.exists('/dev/ttySTM32'):
            real = os.path.realpath('/dev/ttySTM32')
            print(f'[UART] Symlink /dev/ttySTM32 -> {real}')
            return '/dev/ttySTM32'

        # 3) USB 设备扫描
        usb_devs = sorted(glob.glob('/dev/ttyUSB*'))
        if usb_devs:
            print(f'[UART] Auto-detected: {usb_devs[0]}')
            return usb_devs[0]

        # 4) GPIO UART 回退
        if os.path.exists('/dev/ttyAS1'):
            print('[UART] Fallback to GPIO: /dev/ttyAS1')
            return '/dev/ttyAS1'

        # 没找到, 等待 USB 枚举
        if attempt < max_retries - 1:
            print(f'[UART] No device found, retry {attempt + 1}/{max_retries} '
                  f'in {retry_interval:.0f}s (USB enumeration may be slow)...')
            _time.sleep(retry_interval)

    print('[UART] WARNING: no device after retries, defaulting /dev/ttyUSB0')
    return '/dev/ttyUSB0'

UART_PORT = _find_uart()
UART_BAUD = 115200
UART_TIMEOUT = 0.005  # 读超时 10ms→5ms, 减少阻塞

# ============ 图像预处理 ============
USE_CLAHE = os.environ.get('VISION_CLAHE', '1') != '0'
CLAHE_CLIP_LIMIT = float(os.environ.get('VISION_CLAHE_CLIP', '1.5'))
CLAHE_TILE_SIZE = int(os.environ.get('VISION_CLAHE_TILE', '4'))
CLAHE_S_CHANNEL = os.environ.get('VISION_CLAHE_S', '0') != '0'  # 默认关闭S通道CLAHE (Realtek相机S分布被压缩)
ADAPTIVE_V_THRESHOLD = os.environ.get('VISION_ADAPTIVE_V', '1') != '0'

# ============ 跟踪器参数 ============
TRACKER_SMOOTHING = float(os.environ.get('VISION_TRACK_SMOOTH', '0.30'))
TRACKER_MAX_DIST = int(os.environ.get('VISION_TRACK_MAXDIST', '100'))
TRACKER_MAX_LOST = int(os.environ.get('VISION_TRACK_MAXLOST', '5'))
TRACKER_PREDICT = os.environ.get('VISION_TRACK_PREDICT', '1') != '0'
TRACKER_CONFIRM_FRAMES = int(os.environ.get('VISION_TRACK_CONFIRM', '2'))

# ============ 帧率控制 ============
TARGET_FPS = int(os.environ.get('VISION_FPS', '30'))

# ============ 检测模式 ============
# False: 双色检测(蓝+黄全部检测, 分类友/敌) — 推荐, STM32 能区分 E/F/N
# True: 只检测己方颜色(看到己方块后退, 其余依赖光电传感器) — STM32 只收到 F/X
DETECT_OWN_ONLY = os.environ.get('VISION_OWN_ONLY', '0') != '0'

# ============ 目标优先级模式 (仅DETECT_OWN_ONLY=False时生效) ============
# 'collect': N(中立)>E(敌方) — 以收集能量块得分为主
# 'attack':  E(敌方)>N(中立) — 以推敌方能量块为主
PRIORITY_MODE = os.environ.get('VISION_PRIORITY', 'collect')

# ============ 距离分段阈值 ============
DISTANCE_NEAR  = 12000   # 面积 > 此值 = 近距离
DISTANCE_FAR   = 3000    # 面积 < 此值 = 远距离

# ============ Watchdog ============
WATCHDOG_TIMEOUT = 30.0  # 连续无有效帧超时(s), 触发相机重启

# ============ 检测优化 ============
DETECT_HALF_RES = os.environ.get('VISION_HALF_RES', '1') != '0'  # 半分辨率检测
ROI_PREDICT = os.environ.get('VISION_ROI_PREDICT', '1') != '0'   # ROI 预测加速
ROI_FULL_SCAN_INTERVAL = int(os.environ.get('VISION_ROI_SCAN', '5'))  # 全帧扫描间隔
DIRECTION_SMOOTH_WINDOW = int(os.environ.get('VISION_DIR_SMOOTH', '3'))  # 方向平滑窗口
CLOSE_RANGE_RATIO = float(os.environ.get('VISION_CLOSE_RATIO', '0.40'))  # 近距离回退占比阈值

# ============ 掉台回复 - 黑色(台面)检测 ============
HSV_BLACK = {
    'lower': np.array([0, 0, 0]),
    'upper': np.array([180,
                       int(os.environ.get('VISION_BLACK_S_MAX', '100')),
                       int(os.environ.get('VISION_BLACK_V_MAX', '60'))]),
}
# 迟滞阈值: ratio > HIGH → 开始发G, ratio < LOW → 停止发G, 防止 G↔X 震荡
DROP_BLACK_RATIO_HIGH = float(os.environ.get('VISION_DROP_RATIO_HIGH', '0.50'))  # G 启动阈值
DROP_BLACK_RATIO_LOW = float(os.environ.get('VISION_DROP_RATIO_LOW', '0.30'))    # G 退出阈值
DROP_BLACK_RATIO_THRESHOLD = DROP_BLACK_RATIO_HIGH  # 兼容旧引用
DROP_G_CONFIRM_FRAMES = int(os.environ.get('VISION_DROP_CONFIRM', '3'))  # 连续N帧超阈值才首次发G
DROP_SEND_INTERVAL = float(os.environ.get('VISION_DROP_INTERVAL', '0.05'))  # 20Hz
DROP_V_OFFSET = int(os.environ.get('VISION_DROP_V_OFFSET', '15'))  # P25 + offset for adaptive V threshold
# 超时适配远程固件: SPIN+FORWARD(1000)+BACK(2000)+ESCAPE(850)≈4s/轮, 留5轮余量
DROP_RECOVERY_TIMEOUT = float(os.environ.get('VISION_DROP_TIMEOUT', '25.0'))
DROP_RATIO_EMA = float(os.environ.get('VISION_DROP_RATIO_EMA', '0.35'))  # ratio EMA (稍降→更平滑)
DROP_DIR_EMA = float(os.environ.get('VISION_DROP_DIR_EMA', '0.25'))      # direction EMA (稍降→更平滑)

# ============ 回声确认 (STM32回传) ============
ECHO_ENABLED = os.environ.get('VISION_ECHO', '1') != '0'         # 启用回声解析
ECHO_TIMEOUT = float(os.environ.get('VISION_ECHO_TIMEOUT', '1.0'))  # 回声超时(秒), 超时视为通信异常

# ============ AprilTag 融合检测 ============
TAG_DETECT_ENABLED = os.environ.get('VISION_TAG', '1') != '0'
TAG_DETECT_INTERVAL = int(os.environ.get('VISION_TAG_INTERVAL', '3'))  # 全帧Tag扫描间隔 (仅无颜色目标时)
TAG_BACKEND = os.environ.get('VISION_TAG_BACKEND', 'apriltag')  # apriltag(推荐)/aruco/qr
TAG_ID_NEUTRAL = 0   # 中立能量块
TAG_ID_BLUE = 1      # 蓝方能量块
TAG_ID_YELLOW = 2    # 黄方能量块
TAG_ROI_MARGIN = int(os.environ.get('VISION_TAG_ROI_MARGIN', '40'))    # ROI扩展边距(px), 颜色目标周围搜Tag
TAG_ROI_MATCH_DIST = int(os.environ.get('VISION_TAG_MATCH_DIST', '80'))  # Tag-颜色匹配最大距离(px)

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
