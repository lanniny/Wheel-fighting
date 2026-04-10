#!/usr/bin/env python3
"""Blue block validation with WB=4200."""
import cv2, numpy as np, time, sys
sys.path.insert(0, ".")
import config
from detector import ColorDetector

cap = cv2.VideoCapture("/dev/video0")
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3)
cap.set(cv2.CAP_PROP_AUTO_WB, 0)
cap.set(cv2.CAP_PROP_WB_TEMPERATURE, 4200)
time.sleep(1.5)
for _ in range(10):
    cap.read()

det = ColorDetector(enable_tracking=False)

print("=== Blue block + BLUE mode ===")
for i in range(6):
    r, f = cap.read()
    if not r: continue
    t = det.detect_own(f, "b")
    if t:
        print("F%d: OWN=%d area=%d cx=%d dir=%+.2f" % (i, len(t), int(t[0].area), t[0].cx, t[0].direction))
    else:
        print("F%d: OWN=0 MISS" % i)
    time.sleep(0.1)

print("")
print("=== Blue block + YELLOW mode (must be 0) ===")
for i in range(6):
    r, f = cap.read()
    if not r: continue
    t = det.detect_own(f, "y")
    if t:
        print("F%d: OWN=%d area=%d (FP!)" % (i, len(t), int(t[0].area)))
    else:
        print("F%d: OWN=0 OK" % i)
    time.sleep(0.1)

cap.release()
print("Done")
