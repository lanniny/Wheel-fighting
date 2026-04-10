#!/usr/bin/env python3
"""HSV 诊断: 抓一帧分析黄色检测失败原因"""
import cv2
import numpy as np
import sys
sys.path.insert(0, "/home/radxa/vision_upload")
import config

cap = cv2.VideoCapture(config.find_camera())
config.setup_camera(cap)
for _ in range(10):
    cap.read()
ret, frame = cap.read()
cap.release()
if not ret:
    print("CAPTURE FAILED")
    sys.exit(1)

hsv = cv2.cvtColor(cv2.GaussianBlur(frame, (3, 3), 0), cv2.COLOR_BGR2HSV)
h, w = frame.shape[:2]
print(f"Frame: {w}x{h}")

# 中心区域 HSV 统计
roi = hsv[h // 4:3 * h // 4, w // 4:3 * w // 4]
print(f"Center H: mean={roi[:,:,0].mean():.1f} std={roi[:,:,0].std():.1f} "
      f"range=[{roi[:,:,0].min()},{roi[:,:,0].max()}]")
print(f"Center S: mean={roi[:,:,1].mean():.1f} std={roi[:,:,1].std():.1f} "
      f"range=[{roi[:,:,1].min()},{roi[:,:,1].max()}]")
print(f"Center V: mean={roi[:,:,2].mean():.1f} std={roi[:,:,2].std():.1f} "
      f"range=[{roi[:,:,2].min()},{roi[:,:,2].max()}]")

# 不同黄色阈值的掩码像素数
thresholds = [
    ("H22-42 S80+", [22, 80, 40], [42, 255, 255]),
    ("H15-50 S40+", [15, 40, 40], [50, 255, 255]),
    ("H10-55 S20+", [10, 20, 30], [55, 255, 255]),
    ("H20-45 S50+", [20, 50, 40], [45, 255, 255]),
]
print()
for name, lo, hi in thresholds:
    mask = cv2.inRange(hsv, np.array(lo), np.array(hi))
    px = cv2.countNonZero(mask)
    print(f"Yellow [{name}]: {px} px ({px / (h * w) * 100:.1f}%)")

# 用当前配置阈值做轮廓分析
lower = np.array([22, 80, 40])
upper = np.array([42, 255, 255])
mask = cv2.inRange(hsv, lower, upper)
kernel_o = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
kernel_c = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel_o)
mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel_c)
cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
cnts = sorted(cnts, key=cv2.contourArea, reverse=True)
print(f"\nYellow contours: {len(cnts)}")
for i, c in enumerate(cnts[:8]):
    a = cv2.contourArea(c)
    if a < 50:
        break
    x, y, cw, ch = cv2.boundingRect(c)
    peri = cv2.arcLength(c, True)
    circ = 4 * 3.14159 * a / (peri * peri) if peri > 0 else 0
    hull = cv2.contourArea(cv2.convexHull(c))
    sol = a / hull if hull > 0 else 0
    cmask = np.zeros(hsv.shape[:2], dtype=np.uint8)
    cv2.drawContours(cmask, [c], -1, 255, -1)
    h_px = hsv[:, :, 0][cmask > 0]
    s_px = hsv[:, :, 1][cmask > 0]
    h_std = h_px.std() if len(h_px) > 0 else 0
    s_mean = s_px.mean() if len(s_px) > 0 else 0
    btm = (y + ch) / h
    # 判定是否会被过滤
    rej = []
    if a < 375:
        rej.append("area<375(half)")
    if circ < 0.35:
        rej.append(f"circ={circ:.2f}<0.35")
    if a < 1250 and h_std > 18:
        rej.append(f"Hstd={h_std:.1f}>18")
    if s_mean < 100:
        rej.append(f"Smean={s_mean:.0f}<100")
    if btm > 0.88:
        rej.append(f"btm={btm:.2f}>0.88")
    tag = " REJECT:" + ",".join(rej) if rej else " PASS"
    print(f"  #{i}: area={a:.0f} pos=({x},{y}) {cw}x{ch} "
          f"circ={circ:.3f} sol={sol:.3f} Hstd={h_std:.1f} "
          f"Smean={s_mean:.1f} btm={btm:.2f}{tag}")

cv2.imwrite("/tmp/diag_frame.jpg", frame)
print("\nSaved /tmp/diag_frame.jpg")
