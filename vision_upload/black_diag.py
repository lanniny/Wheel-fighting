"""临时诊断: 抓当前相机一帧, 分析外壁/台面的黑色检测情况。
用于调"上台黑色阈值"——看当前外壁的 V/S 分布 + 各阈值下黑色比例,
判断要把 BLACK_V_MAX / BLACK_S_MAX 调到多少, 外壁才能被识别为黑色发 G。
跑法: 先 stop vision.service 释放相机, 再 python3 /tmp/black_diag.py
"""
import sys
sys.path.insert(0, '/home/radxa/vision_upload')
import cv2
import numpy as np
import config

dev = config.find_camera()
print(f'[diag] camera = {dev}')
cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
if not cap.isOpened():
    cap = cv2.VideoCapture(dev)
if not cap.isOpened():
    print('[diag] ERROR: cannot open camera')
    sys.exit(1)
config.setup_camera(cap)
for _ in range(8):
    cap.read()
ret, frame = cap.read()
if not ret or frame is None:
    print('[diag] ERROR: read failed')
    sys.exit(1)

hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

print('=== 外壁画面 HSV 统计 ===')
print('V(亮度): mean=%.0f p10=%.0f p25=%.0f p50=%.0f p75=%.0f'
      % (V.mean(), np.percentile(V, 10), np.percentile(V, 25),
         np.percentile(V, 50), np.percentile(V, 75)))
print('S(饱和): mean=%.0f p25=%.0f p50=%.0f p75=%.0f p90=%.0f'
      % (S.mean(), np.percentile(S, 25), np.percentile(S, 50),
         np.percentile(S, 75), np.percentile(S, 90)))

cur_v = int(config.HSV_BLACK['upper'][2])
cur_s = int(config.HSV_BLACK['upper'][1])
cur_mask = (V < cur_v) & (S < cur_s)
print('=== 当前阈值黑色比例 ===')
print('当前 V<%d 且 S<%d → 黑色 %.1f%%  (发G需>55%%, 退出<30%%)'
      % (cur_v, cur_s, 100.0 * cur_mask.mean()))

print('=== 阈值扫描 (帮调参: 找能让外壁>55%%的组合) ===')
print('        S<100   S<150   S<200   S<255')
for vmax in [60, 80, 100, 120, 150]:
    cells = []
    for smax in [100, 150, 200, 255]:
        m = (V < vmax) & (S < smax)
        cells.append('%5.0f%%' % (100.0 * m.mean()))
    print('V<%3d:  %s' % (vmax, '  '.join(cells)))

try:
    from detector import ColorDetector
    det = ColorDetector(enable_tracking=False)
    if hasattr(det, 'detect_black_ratio'):
        r, bcx, bcy, bdir, dbg = det.detect_black_ratio(frame)
        print('=== detect_black_ratio (实际DROP逻辑) ===')
        print('ratio=%.1f%% blob=%.1f%% cx=%d dir=%+.2f'
              % (100 * r, 100 * dbg.get('blob_ratio', 0), bcx, bdir))
except Exception as e:
    print(f'[diag] detect_black_ratio skip: {e!r}')

cap.release()
print('[diag] done')
