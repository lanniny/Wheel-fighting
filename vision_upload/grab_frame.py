#!/usr/bin/env python3
"""从本机 MJPEG 流抓一帧存盘 (不占摄像头, 走 vision 已发布的 HTTP 流)。"""
import sys
import urllib.request

url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080/stream"
out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/now.jpg"

try:
    stream = urllib.request.urlopen(url, timeout=6)
except Exception as e:
    print(f"ERROR: 打开流失败 {e}")
    sys.exit(1)

buf = b""
for _ in range(2000):
    chunk = stream.read(4096)
    if not chunk:
        break
    buf += chunk
    a = buf.find(b"\xff\xd8")          # JPEG SOI
    b = buf.find(b"\xff\xd9", a + 2)   # JPEG EOI
    if a != -1 and b != -1:
        with open(out, "wb") as f:
            f.write(buf[a:b + 2])
        print(f"OK: saved {b + 2 - a} bytes -> {out}")
        sys.exit(0)
    if len(buf) > 600000:
        break
print("ERROR: 未能从流中提取完整JPEG")
sys.exit(1)
