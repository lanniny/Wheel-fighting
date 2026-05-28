"""
视觉系统配置 - 轮式格斗机器人 v4
所有 HSV 阈值需在赛场环境下重新标定
支持环境变量覆盖, 便于板端快速调试
"""
import os
import numpy as np
import subprocess


def _safe_int(env_key, default):
    val = os.environ.get(env_key)
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        print(f'[config] WARN: invalid {env_key}={val!r}, using default {default}')
        return default


def _safe_float(env_key, default):
    val = os.environ.get(env_key)
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        print(f'[config] WARN: invalid {env_key}={val!r}, using default {default}')
        return default

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

# v2 (B5): CAMERA_DEVICE 改 lazy 探测 - 避免 import config 阻塞
# 通过 module-level __getattr__ (PEP 562) 在首次访问时探测;
# 一旦赋值就变成常规属性, __getattr__ 不再触发
# (浮浮酱备注: cam_test / uart_test 等独立脚本之前会因为 _find_uart 阻塞 10s)
CAMERA_WIDTH = 640
CAMERA_HEIGHT = 480
CAMERA_FPS = 30
CAMERA_FOURCC = os.environ.get('VISION_FOURCC', 'YUYV')  # YUYV=稳定不卡死(300帧零故障); MJPG=快但USB Hub上反复hang

# ============ 白平衡 ============
AUTO_WB = int(os.environ.get('VISION_AUTO_WB', '0'))       # 0=固定WB, 1=自动WB
# 统一使用 4200K 冷色温 (蓝/黄队通用)
# 原因: WB>4600K 会导致蓝色能量块 H 偏移到黄色范围, 造成严重误检
# 实测: WB=4200K 蓝色检出8.2% 黄色误检2.2%(被排斥掩码消除)
WB_TEMPERATURE = _safe_int('VISION_WB_TEMP', 4200)
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

    # WB 验证: 某些 V4L2 驱动设置 WB 不生效, 需要重试
    if AUTO_WB == 0 and WB_TEMPERATURE > 0:
        actual_wb = cap.get(cv2.CAP_PROP_WB_TEMPERATURE)
        if abs(actual_wb - WB_TEMPERATURE) > 200:
            cap.set(cv2.CAP_PROP_AUTO_WB, 0)
            cap.set(cv2.CAP_PROP_WB_TEMPERATURE, WB_TEMPERATURE)
            actual_wb2 = cap.get(cv2.CAP_PROP_WB_TEMPERATURE)
            if abs(actual_wb2 - WB_TEMPERATURE) > 200:
                print(f'[WARN] WB mismatch: want={WB_TEMPERATURE} got={actual_wb2:.0f}')

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
    'lower': np.array([85, 50, 25]),
    'upper': np.array([125, 255, 255]),
}

HSV_YELLOW = {
    'lower': np.array([22, 55, 40]),
    'upper': np.array([48, 255, 255]),
}
# 黄色二阶段验证: 轮廓内 S 均值必须 > 此值, 否则判为蓝色冒充
# (Realtek相机在WB=6000K下: 黄色S≈134, 蓝色S≈97, 阈值120分离)
YELLOW_S_MEAN_MIN = _safe_int('VISION_YELLOW_S_MEAN', 0)

HSV_WHITE = {
    'lower': np.array([0, 0, 230]),
    'upper': np.array([180, 18, 255]),   # S:25→18 V:210→230, 减少高光误检
}

# v4 (2026-05-21): 恢复白色 HSV 检测 (修白色块被蓝色误识别 bug)
DETECT_WHITE = os.environ.get('VISION_DETECT_WHITE', '1') != '0'
# 白色目标膨胀像素 (建排斥掩码, 防止白色块边缘反光被蓝色吸收)
WHITE_EXCL_DILATE = int(os.environ.get('VISION_WHITE_EXCL_DILATE', '30'))

# ============ 检测参数 ============
MIN_CONTOUR_AREA = 1000   # 600→1000, 减少环境小噪点误检 (纯HSV模式)
MIN_CONTOUR_AREA_YELLOW = _safe_int('VISION_YELLOW_MIN_AREA', 1000)
MAX_CONTOUR_AREA_YELLOW = _safe_int('VISION_YELLOW_MAX_AREA', 15000)
MAX_CONTOUR_AREA = 280000   # 280000 ≈ 91% of 640×480, 支持近距离大面积色块
MIN_ASPECT_RATIO = 0.3
MAX_ASPECT_RATIO = 3.0
MAX_TARGETS = 10

# ============ 黄色增强过滤 (替代面积暴力阈值) ============
YELLOW_CIRCULARITY_MIN = float(os.environ.get('VISION_YELLOW_CIRC', '0.15'))  # 圆度下限
YELLOW_H_STD_MAX = int(os.environ.get('VISION_YELLOW_H_STD', '25'))           # H通道标准差上限 (放宽, 斜面光照不均)
FRAME_BOTTOM_EXCLUDE = float(os.environ.get('VISION_BOTTOM_EXCL', '0.12'))    # 忽略画面底部12%

# ============ 友方近距离报警 (双色模式防误发F) ============
FRIEND_ALERT_AREA = _safe_int('VISION_FRIEND_ALERT', 6000)         # 800→6000: 只有贴近大友方块才避让, 远处小误检不发F (修乱退)
FRIEND_ALERT_AREA_YELLOW = _safe_int('VISION_FRIEND_ALERT_Y', 6000)  # 3000→6000: 黄队同步上调 (地板暖色调误检更严重)

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
    env_uart = os.environ.get('VISION_UART')

    for attempt in range(max_retries):
        # 1) 环境变量指定
        if env_uart and os.path.exists(env_uart):
            if attempt > 0:
                print(f'[UART] Found {env_uart} on attempt {attempt + 1}')
            return env_uart

        if env_uart:
            if attempt < max_retries - 1:
                print(f'[UART] Waiting for configured {env_uart}, retry '
                      f'{attempt + 1}/{max_retries} in '
                      f'{retry_interval:.0f}s...')
                _time.sleep(retry_interval)
            continue

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

    if env_uart:
        print(f'[UART] WARNING: configured {env_uart} not found after retries')
        return env_uart

    print('[UART] WARNING: no device after retries, defaulting /dev/ttyUSB0')
    return '/dev/ttyUSB0'

# v2 (B5): UART_PORT 改 lazy 探测 - 见 __getattr__
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
TRACKER_CONFIRM_FRAMES = _safe_int('VISION_TRACK_CONFIRM', 2)
TRACKER_COLOR_SWITCH_FRAMES = int(os.environ.get('VISION_TRACK_COLOR_SWITCH', '5'))  # 颜色切换冷却帧数 (3→5, 防E↔F闪烁)

# ============ UART 发送频率 (可配置化, 2026-05-25 从 comm.py 硬编码迁出) ============
UART_NO_TARGET_INTERVAL = float(os.environ.get('VISION_NO_TARGET_INTERVAL', '0.05'))
UART_TARGET_INTERVAL = float(os.environ.get('VISION_TARGET_INTERVAL', '0.050'))
UART_FAST_INTERVAL = float(os.environ.get('VISION_FAST_INTERVAL', '0.033'))

# ============ 目标丢失保持 (Holdover) ============
LOST_TARGET_HOLDOVER_FRAMES = int(os.environ.get('VISION_HOLDOVER_FRAMES', '1'))  # 3→1: F类型不把单次假阳放大成3帧后退 (修乱退)
LOST_TARGET_AREA_DECAY = float(os.environ.get('VISION_HOLDOVER_AREA_DECAY', '0.7'))

# ============ 智能优先级评分 ============
PRIORITY_AREA_WEIGHT = float(os.environ.get('VISION_PRIORITY_AREA_W', '0.6'))
PRIORITY_CENTER_WEIGHT = float(os.environ.get('VISION_PRIORITY_CENTER_W', '0.3'))
PRIORITY_STABLE_WEIGHT = float(os.environ.get('VISION_PRIORITY_STABLE_W', '0.1'))

# ============ 帧率控制 ============
TARGET_FPS = _safe_int('VISION_FPS', 30)

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
WATCHDOG_TIMEOUT = 5.0  # 连续无有效帧超时(s), 触发相机重启 (30→5, 比赛只有120s不能等30s)

# ============ 检测优化 ============
DETECT_HALF_RES = os.environ.get('VISION_HALF_RES', '1') != '0'  # 半分辨率检测
DIRECTION_FLIP = os.environ.get('VISION_DIR_FLIP', '0') != '0'  # 方向翻转 (摄像头装反时启用)
CLOSE_RANGE_RATIO = float(os.environ.get('VISION_CLOSE_RATIO', '0.40'))  # 近距离回退占比阈值

# ============ 掉台回复 - 黑色(台面)检测 ============
HSV_BLACK = {
    'lower': np.array([0, 0, 0]),
    'upper': np.array([180,
                       int(os.environ.get('VISION_BLACK_S_MAX', '100')),
                       int(os.environ.get('VISION_BLACK_V_MAX', '60'))]),
}
# 迟滞阈值: ratio > HIGH → 开始发G, ratio < LOW → 停止发G, 防止 G↔X 震荡
DROP_BLACK_RATIO_HIGH = float(os.environ.get('VISION_DROP_RATIO_HIGH', '0.55'))  # G 启动阈值 (0.50→0.55, 降低阴影误触发)
DROP_BLACK_RATIO_LOW = float(os.environ.get('VISION_DROP_RATIO_LOW', '0.30'))    # G 退出阈值
DROP_BLACK_RATIO_THRESHOLD = DROP_BLACK_RATIO_HIGH  # 兼容旧引用
DROP_G_CONFIRM_FRAMES = _safe_int('VISION_DROP_CONFIRM', 5)
DROP_SEND_INTERVAL = float(os.environ.get('VISION_DROP_INTERVAL', '0.05'))  # 20Hz
DROP_V_OFFSET = int(os.environ.get('VISION_DROP_V_OFFSET', '15'))  # P25 + offset for adaptive V threshold
# 超时适配远程固件: SPIN+FORWARD(1000)+BACK(2000)+ESCAPE(850)≈4s/轮, 留5轮余量
DROP_RECOVERY_TIMEOUT = float(os.environ.get('VISION_DROP_TIMEOUT', '25.0'))
DROP_RATIO_EMA = float(os.environ.get('VISION_DROP_RATIO_EMA', '0.35'))  # ratio EMA (稍降→更平滑)
DROP_DIR_EMA = float(os.environ.get('VISION_DROP_DIR_EMA', '0.25'))      # direction EMA (稍降→更平滑)

# ============ 回声确认 (STM32回传) ============
ECHO_ENABLED = os.environ.get('VISION_ECHO', '1') != '0'         # 启用回声解析
ECHO_TIMEOUT = float(os.environ.get('VISION_ECHO_TIMEOUT', '1.0'))  # 回声超时(秒), 超时视为通信异常

# ============ 协议 v2 (向后兼容) ============
# 默认关闭, 旧 STM32 固件保持 v1 解析行为不变
# 启用后帧体追加 ts,conf,tid 三字段 ($type,cx,cy,area,dir,ts,conf,tid*CS\n)
# v1 STM32 解析器自动忽略多余字段 (vision_parser.c 用 sscanf 取前 5 字段)
PROTOCOL_V2 = os.environ.get('VISION_PROTOCOL_V2', '0') != '0'

# ============ MJPEG 流媒体 ============
STREAM_EVERY_N_FRAMES = int(os.environ.get('VISION_STREAM_EVERY', '3'))  # 每 N 帧发布一次 (30fps→10fps stream)

# ============ AprilTag 融合检测 ============
TAG_DETECT_ENABLED = os.environ.get('VISION_TAG', '1') != '0'
TAG_DETECT_INTERVAL = _safe_int('VISION_TAG_INTERVAL', 1)  # 全帧Tag扫描间隔 (Tag-Primary模式每帧扫描)
TAG_PRIMARY = os.environ.get('VISION_TAG_PRIMARY', '1') != '0'  # Tag主导: HSV无Tag确认→降级为N
TAG_BACKEND = os.environ.get('VISION_TAG_BACKEND', 'apriltag')  # apriltag(推荐)/aruco/qr
TAG_ID_NEUTRAL = 0   # 中立能量块
TAG_ID_BLUE = 1      # 蓝方能量块
TAG_ID_YELLOW = 2    # 黄方能量块
TAG_ROI_MARGIN = int(os.environ.get('VISION_TAG_ROI_MARGIN', '40'))    # ROI扩展边距(px), 颜色目标周围搜Tag
TAG_ROI_MATCH_DIST = int(os.environ.get('VISION_TAG_MATCH_DIST', '50'))  # Tag-颜色匹配最大距离(px), 80→50 收紧避免方向偏差
TAG_DECISION_MARGIN = float(os.environ.get('VISION_TAG_MARGIN', '30'))  # AprilTag decision_margin 最小值, 低于此值不信任
ORPHAN_TAG_EDGE_MARGIN = int(os.environ.get('VISION_TAG_EDGE_MARGIN', '60'))  # 孤儿Tag边缘排除区(px), 防场外Tag误导

# ============ Tag 辅助自适应 HSV (TAHSV) ============
ADAPTIVE_HSV_ENABLED = os.environ.get('VISION_ADAPTIVE_HSV', '1') != '0'

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

# ============ ONNX 推理后端配置 (高精度方案, CPU INT8) ============
# 实测 best_320_int8.onnx + ARM CPU 4 线程: 150ms/帧 (6.6 FPS), 精度 97%
# NPU 量化在 YOLOv8 sigmoid head 有硬件 bug (实测 score 跌 18x), 改用 ONNX CPU 兜底
ONNX_MODEL_PATH = os.environ.get('VISION_ONNX_MODEL',
                                 '/home/radxa/models/best_320_int8.onnx')
ONNX_CONF_THRESH = float(os.environ.get('VISION_ONNX_CONF', '0.4'))
ONNX_IOU_THRESH = float(os.environ.get('VISION_ONNX_IOU', '0.45'))
# A733 拓扑: 2xA76 (cpu6,7) + 6xA55 (cpu0-5)
# worker 绑核到 {6,7}, threads=2 匹配大核数, threads>2 会让多余线程等大核或抢主循环 A55
# 历史教训(2026-05-21): threads=4 实测 ONNX 从 17 FPS 跌到 5.7 FPS
ONNX_THREADS = int(os.environ.get('VISION_ONNX_THREADS', '2'))

# ── ONNX 异步 worker 控制 (v2, B4 收编) ──
# 异步推理: 主循环 detect() 不阻塞, worker 后台跑推理
ONNX_ASYNC = os.environ.get('VISION_ONNX_ASYNC', '1') != '0'
# _async_targets 时间戳超过此年龄视为失效 (防 worker 死亡时追幽灵目标)
ONNX_TARGET_MAX_AGE_S = float(
    os.environ.get('VISION_ONNX_TARGET_MAX_AGE_S', '0.5'))
# 统计窗口大小 (ring buffer 最近 N 帧)
ONNX_STATS_WINDOW = int(os.environ.get('VISION_ONNX_STATS_WIN', '100'))

# ============ FusedDetector 融合权重 (v2, B4 收编) ============
# 三路融合: AprilTag(1.0) > ONNX(0.7) > HSV(0.3); Tag 在 main.py 主循环处理
FUSE_W_HSV = float(os.environ.get('VISION_FUSE_W_HSV', '0.3'))
FUSE_W_ONNX = float(os.environ.get('VISION_FUSE_W_ONNX', '0.7'))
# HSV / ONNX 目标中心距离 < FUSE_MATCH_DIST_PX 视为同一目标
FUSE_MATCH_DIST_PX = int(os.environ.get('VISION_FUSE_MATCH_DIST', '80'))
# 距离过滤: 远处疑似 friend (area<FRIEND_MIN_AREA) + 近处 enemy (>=ENEMY_NEAR_AREA)
# 同时存在时, 过滤 friend 防止"远友 + 近敌"组合让机器人盲目后退
FUSE_FRIEND_MIN_AREA = int(os.environ.get('VISION_FUSE_FRIEND_MIN_AREA', '2000'))
FUSE_ENEMY_NEAR_AREA = int(os.environ.get('VISION_FUSE_ENEMY_NEAR_AREA', '6000'))

# v3 (2026-05-21, HSV 远端误识别修复):
# HSV-only 目标 (ONNX 未确认) 最小信任 area
#   场景: 远端环境(地板/墙/反光)被 HSV 误识别为蓝/黄, ONNX 因不在训练分布不会检测,
#   导致 fused 直接通过 HSV 假目标. 加 area 门槛过滤远端小目标.
FUSE_HSV_ONLY_MIN_AREA = int(os.environ.get('VISION_FUSE_HSV_ONLY_MIN_AREA', '800'))

# v4 (2026-05-21, 白色块误识别修复):
# 白色 (中立能量块) HSV-only 特殊 area 阈值
#   ONNX 训练数据对白色覆盖不足 (主人现场反馈), 几乎所有 white 目标都是 HSV-only
#   white = 中立, false positive 风险低 (机器人去推不会伤己), 应当比 blue/yellow 更宽松
FUSE_HSV_ONLY_WHITE_MIN_AREA = int(
    os.environ.get('VISION_FUSE_HSV_ONLY_WHITE_MIN_AREA', '3000'))

# friend 全局信任面积阈值 (无视 enemy 存在与否)
#   场景: 远端假 friend 让 attack 模式机器人不主动撞近端真 enemy
#   原 FUSE_FRIEND_MIN_AREA 只在 has_near_enemy 时触发, 这个是全局生效
FUSE_FRIEND_TRUST_AREA = int(os.environ.get('VISION_FUSE_FRIEND_TRUST_AREA', '3000'))  # 8000→3000, 防中距离真友方被过滤导致推自己的块

# HSV vs ONNX 颜色分歧时的策略:
#   'onnx'    - ONNX 胜 (旧行为, ONNX 权重 0.7 > HSV 0.3)
#   'hsv'     - HSV 胜 (能量块场景 HSV 更稳)
#   'neutral' - 降级为 neutral (最保守, 避免误判 friend/enemy)
#   'drop'    - 丢弃此目标 (无视)
FUSE_COLOR_CONFLICT_POLICY = os.environ.get(
    'VISION_FUSE_COLOR_CONFLICT', 'neutral')

# ============ 系统输出与日志 (v2, B4 收编) ============
# 检测日志路径 (RotatingFileHandler 写入)
LOG_PATH = os.environ.get('VISION_LOG', '/tmp/vision_det.log')
# 主循环状态行输出节流 (秒) - 替代 \r 单行刷新, 修 journalctl blob data
STATUS_PRINT_INTERVAL_S = float(
    os.environ.get('VISION_STATUS_INTERVAL_S', '5.0'))

# ============ ThermalGuard (P1, 2026-05-22 加入) ============
# CPU 热降频自动降级: 温度高于 HIGH 时关闭 ONNX 走 HSV-only, 低于 LOW 时恢复
# 来源: 2026-05-22 实测 CPU 持续 77°C ONNX 推理 4 分钟内劣化 2.7x,
#       触发 HSV/ONNX 颜色分歧, 全部目标降级为 white → STM32 全部进攻 bug
THERMAL_GUARD_ENABLED = os.environ.get('VISION_THERMAL_GUARD', '1') != '0'
THERMAL_HIGH_C = float(os.environ.get('VISION_THERMAL_HIGH', '72.0'))
THERMAL_LOW_C = float(os.environ.get('VISION_THERMAL_LOW', '65.0'))
THERMAL_CHECK_INTERVAL_S = float(os.environ.get('VISION_THERMAL_INTERVAL', '5.0'))

# 紧急后端强制覆盖: 'hsv' = 强制走 HSV-only (跳过 ONNX 初始化, 启动最快)
# 优先级最高, 比 --backend 参数高. 通常通过 systemctl edit 设, 赛场一键回退用.
VISION_BACKEND_OVERRIDE = os.environ.get('VISION_BACKEND_OVERRIDE', '').lower()


# ============ Lazy attribute 加载 (B5, PEP 562) ============
# 避免 import config 阻塞 10s 等待 UART/相机 (影响 cam_test 等独立脚本)
# 首次访问 config.CAMERA_DEVICE / config.UART_PORT 时才探测
_lazy_cache = {}


def __getattr__(name):
    """模块属性懒加载: 首次访问时执行设备探测."""
    if name == 'CAMERA_DEVICE':
        if 'CAMERA_DEVICE' not in _lazy_cache:
            _lazy_cache['CAMERA_DEVICE'] = find_camera()
        return _lazy_cache['CAMERA_DEVICE']
    if name == 'UART_PORT':
        if 'UART_PORT' not in _lazy_cache:
            _lazy_cache['UART_PORT'] = _find_uart()
        return _lazy_cache['UART_PORT']
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


def snapshot():
    """启动时打印一次完整配置快照, 便于赛场快速诊断."""
    lines = [
        '─' * 60,
        '[CONFIG] 视觉系统配置快照',
        '─' * 60,
        f'  Camera   : {find_camera()} {CAMERA_WIDTH}x{CAMERA_HEIGHT} '
        f'@{CAMERA_FPS}fps fourcc={CAMERA_FOURCC}',
        f'  WB       : AUTO={AUTO_WB} TEMP={WB_TEMPERATURE}K',
        f'  UART     : (lazy) baud={UART_BAUD} timeout={UART_TIMEOUT}',
        f'  Detect   : own_only={DETECT_OWN_ONLY} priority={PRIORITY_MODE} '
        f'half_res={DETECT_HALF_RES}',
        f'  Tracker  : smooth={TRACKER_SMOOTHING} maxdist={TRACKER_MAX_DIST} '
        f'maxlost={TRACKER_MAX_LOST} predict={TRACKER_PREDICT} '
        f'color_switch={TRACKER_COLOR_SWITCH_FRAMES}',
        f'  CLAHE    : enabled={USE_CLAHE} clip={CLAHE_CLIP_LIMIT} '
        f'tile={CLAHE_TILE_SIZE} S_ch={CLAHE_S_CHANNEL}',
        f'  Drop     : high={DROP_BLACK_RATIO_HIGH} low={DROP_BLACK_RATIO_LOW} '
        f'confirm={DROP_G_CONFIRM_FRAMES}',
        f'  Tag      : enabled={TAG_DETECT_ENABLED} backend={TAG_BACKEND} '
        f'interval={TAG_DETECT_INTERVAL} '
        f'margin={TAG_DECISION_MARGIN} edge={ORPHAN_TAG_EDGE_MARGIN}px',
        f'  ONNX     : model={ONNX_MODEL_PATH}',
        f'             conf={ONNX_CONF_THRESH} iou={ONNX_IOU_THRESH} '
        f'threads={ONNX_THREADS} async={ONNX_ASYNC}',
        f'             stats_win={ONNX_STATS_WINDOW} '
        f'target_max_age={ONNX_TARGET_MAX_AGE_S}s',
        f'  Fused    : w_hsv={FUSE_W_HSV} w_onnx={FUSE_W_ONNX} '
        f'match_dist={FUSE_MATCH_DIST_PX}px',
        f'             friend_min_area={FUSE_FRIEND_MIN_AREA} '
        f'enemy_near_area={FUSE_ENEMY_NEAR_AREA}',
        f'             hsv_only_min={FUSE_HSV_ONLY_MIN_AREA} '
        f'hsv_only_white={FUSE_HSV_ONLY_WHITE_MIN_AREA} '
        f'friend_trust={FUSE_FRIEND_TRUST_AREA} '
        f'conflict={FUSE_COLOR_CONFLICT_POLICY}',
        f'  Detect   : detect_white={DETECT_WHITE} '
        f'white_excl_dilate={WHITE_EXCL_DILATE}px '
        f'dir_flip={DIRECTION_FLIP} circ_min={YELLOW_CIRCULARITY_MIN}',
        f'  UART TX  : no_tgt={UART_NO_TARGET_INTERVAL}s '
        f'tgt={UART_TARGET_INTERVAL}s fast={UART_FAST_INTERVAL}s',
        f'  Holdover : frames={LOST_TARGET_HOLDOVER_FRAMES} '
        f'decay={LOST_TARGET_AREA_DECAY}',
        f'  Priority : area_w={PRIORITY_AREA_WEIGHT} '
        f'center_w={PRIORITY_CENTER_WEIGHT} stable_w={PRIORITY_STABLE_WEIGHT}',
        f'  Protocol : v2={PROTOCOL_V2} echo={ECHO_ENABLED} '
        f'echo_timeout={ECHO_TIMEOUT}s',
        f'  Log      : {LOG_PATH} status_interval={STATUS_PRINT_INTERVAL_S}s',
        f'  Thermal  : guard={THERMAL_GUARD_ENABLED} high={THERMAL_HIGH_C}°C '
        f'low={THERMAL_LOW_C}°C interval={THERMAL_CHECK_INTERVAL_S}s',
        f'  Override : backend={VISION_BACKEND_OVERRIDE or "(none)"}',
        '─' * 60,
    ]
    for line in lines:
        print(line, flush=True)
