"""临时验证: 模拟"已上台卡DROP + 黄队(my_color=y)"场景, 验证修正版是否正确发F。
跑真实 detect_black_ratio + detect_tags + 修正版发F条件(not发G + ratio<th_low + 己方F)。
跑法: stop vision.service 释放相机, 再 python3 /tmp/mock_drop_f.py
"""
import sys
sys.path.insert(0, '/home/radxa/vision_upload')
import cv2
import config
from detector import ColorDetector, TagDetector

MY_COLOR = 'y'  # 假装己方=黄队
dev = config.find_camera()
cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
if not cap.isOpened():
    cap = cv2.VideoCapture(dev)
if not cap.isOpened():
    print('[mock] ERROR cannot open camera')
    sys.exit(1)
config.setup_camera(cap)
for _ in range(8):
    cap.read()
ret, frame = cap.read()
if not ret or frame is None:
    print('[mock] ERROR read failed')
    sys.exit(1)

det = ColorDetector(enable_tracking=False)
tagdet = TagDetector()
th_low = getattr(config, 'DROP_BLACK_RATIO_LOW', 0.30)

print('===== 模拟: 已上台卡DROP + 黄队(my_color=y) =====')
# 1) 黑色检测 — 判断是否"疑似已上台"(ratio<th_low)
try:
    ratio, bcx, bcy, bdir, dbg = det.detect_black_ratio(frame)
except Exception as e:
    ratio = -1.0
    print(f'[mock] detect_black_ratio 异常: {e!r}')
state = ('疑似已上台 (ratio<th_low → 允许发F)' if 0 <= ratio < th_low
         else 'ratio>=th_low → 视作冲台中(不发F, 保G)')
print(f'黑色 ratio={ratio:.2f}  th_low={th_low}  →  {state}')

# 2) Tag检测 + 黄队分类
tags = tagdet.detect_tags(frame)
print(f'识别到 {len(tags)} 个 Tag:')
own_fs = []
for tg in tags:
    cls = TagDetector.classify_tag(tg['id'], MY_COLOR)
    mark = '  ←己方(F)' if cls == 'F' else ''
    print(f"   id={tg['id']} area={tg.get('area')} cx={tg.get('cx')} "
          f"→ 黄队classify={cls}{mark}")
    if cls == 'F':
        own_fs.append(tg)

# 3) 模拟修正版 DROP-F 判定 (sending_G=False 假设未在冲台)
sending_G = False
print('----- 修正版判定: (not发G) and (ratio<th_low) and (有己方F) -----')
if (not sending_G) and 0 <= ratio < th_low and own_fs:
    best = max(own_fs, key=lambda t: t.get('area', 0))
    print(f'✅ 会发 F !  己方黄块 id={best["id"]} '
          f'area={best.get("area")} cx={best.get("cx")}')
else:
    rs = []
    if ratio < 0:
        rs.append('黑色检测异常')
    elif ratio >= th_low:
        rs.append(f'ratio {ratio:.2f} >= th_low {th_low} (判作冲台中)')
    if not own_fs:
        rs.append('画面无黄队(己方)Tag')
    print(f'❌ 不发F (会发 X)。原因: {"; ".join(rs) if rs else "未知"}')
cap.release()
print('[mock] done')
