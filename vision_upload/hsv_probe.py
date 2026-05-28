#!/usr/bin/env python3
"""Grab a frame from MJPEG stream and analyze yellow HSV distribution."""
import cv2, numpy as np, sys, urllib.request

url = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8080/stream'
try:
    req = urllib.request.urlopen(url, timeout=3)
    data = req.read(200000)
    req.close()
except Exception as e:
    print(f'Stream fetch failed: {e}')
    sys.exit(1)

start = data.find(b'\xff\xd8')
end = data.find(b'\xff\xd9', start)
if start < 0 or end < 0:
    print('No JPEG found in stream data')
    sys.exit(1)

jpg = data[start:end+2]
frame = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
if frame is None:
    print('JPEG decode failed')
    sys.exit(1)

cv2.imwrite('/tmp/hsv_yellow_sample.jpg', frame)
hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
h, w = frame.shape[:2]
print(f'Frame: {w}x{h}')

# current config yellow range
yl = np.array([22, 80, 40])
yu = np.array([42, 255, 255])
ymask = cv2.inRange(hsv, yl, yu)
ypx = cv2.countNonZero(ymask)
pct = ypx * 100 / (w * h)
print(f'Yellow mask [22-42, S>=80, V>=40]: {ypx} px ({pct:.2f}%)')

# broad warm-hue scan
hmask = (hsv[:,:,0] >= 10) & (hsv[:,:,0] <= 55) & (hsv[:,:,1] >= 30) & (hsv[:,:,2] >= 20)
n = int(hmask.sum())
if n > 100:
    yh = hsv[:,:,0][hmask]
    ys = hsv[:,:,1][hmask]
    yv = hsv[:,:,2][hmask]
    print(f'Warm pixels ({n}):')
    print(f'  H: min={yh.min()} p5={int(np.percentile(yh,5))} mean={yh.mean():.1f} p95={int(np.percentile(yh,95))} max={yh.max()}')
    print(f'  S: min={ys.min()} p5={int(np.percentile(ys,5))} mean={ys.mean():.1f} p95={int(np.percentile(ys,95))} max={ys.max()}')
    print(f'  V: min={yv.min()} p5={int(np.percentile(yv,5))} mean={yv.mean():.1f} p95={int(np.percentile(yv,95))} max={yv.max()}')
else:
    print(f'Very few warm pixels: {n}')

# relaxed S threshold scan
rmask = (hsv[:,:,0] >= 18) & (hsv[:,:,0] <= 48) & (hsv[:,:,1] >= 50) & (hsv[:,:,2] >= 30)
rpx = int(rmask.sum())
rpct = rpx * 100 / (w * h)
print(f'Relaxed [18-48, S>=50, V>=30]: {rpx} px ({rpct:.2f}%)')
if rpx > 100:
    rs = hsv[:,:,1][rmask]
    print(f'  S: mean={rs.mean():.0f} p5={int(np.percentile(rs,5))} p25={int(np.percentile(rs,25))}')

# super relaxed
smask = (hsv[:,:,0] >= 15) & (hsv[:,:,0] <= 50) & (hsv[:,:,1] >= 30) & (hsv[:,:,2] >= 25)
spx = int(smask.sum())
print(f'Super relaxed [15-50, S>=30, V>=25]: {spx} px ({spx*100/(w*h):.2f}%)')

print('Saved /tmp/hsv_yellow_sample.jpg')
