#!/usr/bin/env python3
"""Diagnose floor vs energy block HSV differences."""
import cv2, numpy as np, sys, urllib.request

url = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8080/stream'
req = urllib.request.urlopen(url, timeout=3)
data = req.read(200000)
req.close()
start = data.find(b'\xff\xd8')
end = data.find(b'\xff\xd9', start)
jpg = data[start:end+2]
frame = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
h, w = frame.shape[:2]

# Split frame into zones
top = hsv[:h//3, :]        # upper 1/3 (likely floor/wall)
mid = hsv[h//3:2*h//3, :]  # middle 1/3 (energy block area)
bot = hsv[2*h//3:, :]      # bottom 1/3 (close floor)

for name, zone in [('TOP(floor/wall)', top), ('MID(block)', mid), ('BOT(close floor)', bot)]:
    ymask = (zone[:,:,0] >= 18) & (zone[:,:,0] <= 48) & (zone[:,:,1] >= 55) & (zone[:,:,2] >= 40)
    n = int(ymask.sum())
    total = zone.shape[0] * zone.shape[1]
    pct = n * 100 / total
    if n > 50:
        s_vals = zone[:,:,1][ymask]
        v_vals = zone[:,:,2][ymask]
        print(f'{name}: {n} px ({pct:.1f}%)  S=[{s_vals.min()},{int(np.percentile(s_vals,25))},{s_vals.mean():.0f},{int(np.percentile(s_vals,75))},{s_vals.max()}]  V=[{v_vals.min()},{int(np.percentile(v_vals,25))},{v_vals.mean():.0f},{int(np.percentile(v_vals,75))},{v_vals.max()}]')
    else:
        print(f'{name}: {n} px ({pct:.1f}%)')

# Test different S thresholds
print('\n--- S threshold sweep ---')
for s_min in [55, 60, 65, 70, 75, 80]:
    mask = cv2.inRange(hsv, np.array([18, s_min, 40]), np.array([48, 255, 255]))
    px = cv2.countNonZero(mask)
    # count contours with area > 1000
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    big = [c for c in cnts if cv2.contourArea(c) > 500]
    biggest = max((cv2.contourArea(c) for c in cnts), default=0)
    print(f'  S>={s_min}: {px:6d} px ({px*100/(w*h):.2f}%)  contours(>500)={len(big)}  max_area={int(biggest)}')
