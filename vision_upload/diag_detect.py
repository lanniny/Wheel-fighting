#!/usr/bin/env python3
"""诊断: 开摄像头抓一帧, 跑 detect_own(蓝), 画框存图, 打印误检面积/位置。"""
import sys
import cv2
import config
from detector import ColorDetector

my_color = sys.argv[1] if len(sys.argv) > 1 else "b"
out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/diag.jpg"

dev = config.find_camera()
cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
if not cap.isOpened():
    cap = cv2.VideoCapture(dev)
if not cap.isOpened():
    print(f"ERROR: cannot open {dev}")
    sys.exit(1)
config.setup_camera(cap)

det = ColorDetector(enable_tracking=False)
# 丢帧等 AE/WB 稳定
for _ in range(12):
    cap.read()
ret, frame = cap.read()
if not ret or frame is None:
    print("ERROR: read frame failed")
    cap.release()
    sys.exit(1)

targets = det.detect_own(frame, my_color)
cname = "BLUE" if my_color == "b" else "YELLOW"
print(f"[DIAG] my_color={my_color} 检测到 {len(targets)} 个{cname}目标:")
for t in targets:
    print(f"  area={t.area:.0f} cx={t.cx} cy={t.cy} dir={t.direction:+.2f} "
          f"wh={t.w}x{t.h} solidity={t.solidity:.2f}")
    cv2.rectangle(frame, (t.x, t.y), (t.x + t.w, t.y + t.h), (0, 0, 255), 3)
    cv2.putText(frame, f"{cname} a={t.area:.0f}", (t.x, max(20, t.y - 8)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

cv2.imwrite(out, frame)
print(f"[DIAG] saved -> {out}")
cap.release()
