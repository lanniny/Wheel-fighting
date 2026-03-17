#!/usr/bin/env python3
"""
HSV 颜色标定工具 - 赛前在赛场环境下运行
功能:
  1. 实时显示摄像头画面 + HSV mask
  2. 点击画面上任意点查看 HSV 值
  3. 自动统计选定区域的 HSV 范围
  4. 保存标定结果到 hsv_calibration.json

用法:
    python3 calibrate.py                  # 启动标定, 结果保存到文件
    python3 calibrate.py --color blue     # 只标定蓝色
    python3 calibrate.py --apply          # 将上次保存的标定结果加载到config
"""
import sys
import os
import json
import time
import cv2
import numpy as np

import config

CALIB_FILE = os.path.join(os.path.dirname(__file__), 'hsv_calibration.json')

# 每种颜色的采样点
samples = {'blue': [], 'yellow': [], 'white': []}
current_color = 'blue'


def on_mouse(event, x, y, flags, param):
    """鼠标点击回调: 采集 HSV 样本点"""
    if event == cv2.EVENT_LBUTTONDOWN:
        hsv_frame = param['hsv']
        h, s, v = hsv_frame[y, x]
        samples[current_color].append((int(h), int(s), int(v)))
        print(f'  [{current_color}] +sample ({h}, {s}, {v})  '
              f'total: {len(samples[current_color])}')


def compute_range(sample_list, margin_h=10, margin_s=40, margin_v=40):
    """从采样点计算 HSV 阈值范围"""
    if len(sample_list) < 3:
        return None
    arr = np.array(sample_list)
    lower = np.array([
        max(0, arr[:, 0].min() - margin_h),
        max(0, arr[:, 1].min() - margin_s),
        max(0, arr[:, 2].min() - margin_v),
    ], dtype=int)
    upper = np.array([
        min(179, arr[:, 0].max() + margin_h),
        min(255, arr[:, 1].max() + margin_s),
        min(255, arr[:, 2].max() + margin_v),
    ], dtype=int)
    return lower.tolist(), upper.tolist()


def save_calibration(results):
    """保存标定结果到 JSON"""
    with open(CALIB_FILE, 'w') as f:
        json.dump(results, f, indent=2)
    print(f'\nCalibration saved to {CALIB_FILE}')


def load_calibration():
    """加载标定结果"""
    if not os.path.exists(CALIB_FILE):
        print(f'No calibration file: {CALIB_FILE}')
        return None
    with open(CALIB_FILE, 'r') as f:
        return json.load(f)


def apply_calibration():
    """将标定结果打印为可粘贴到 config.py 的格式"""
    cal = load_calibration()
    if not cal:
        return
    print('\n# ---- Paste into config.py ----')
    for color in ['blue', 'yellow', 'white']:
        if color in cal:
            lo = cal[color]['lower']
            hi = cal[color]['upper']
            name = f'HSV_{color.upper()}'
            print(f"{name} = {{")
            print(f"    'lower': np.array({lo}),")
            print(f"    'upper': np.array({hi}),")
            print(f"}}")
    print('# ---- End ----\n')


def main():
    import argparse
    parser = argparse.ArgumentParser(description='HSV Color Calibration Tool')
    parser.add_argument('--color', choices=['blue', 'yellow', 'white'],
                        help='Only calibrate one color')
    parser.add_argument('--apply', action='store_true',
                        help='Print saved calibration as config.py snippet')
    parser.add_argument('--headless', action='store_true',
                        help='Headless mode: auto-sample center region')
    args = parser.parse_args()

    if args.apply:
        apply_calibration()
        return

    global current_color
    colors_to_cal = [args.color] if args.color else ['blue', 'yellow', 'white']

    # Open camera
    dev = config.find_camera()
    print(f'Camera: {dev}')
    cap = cv2.VideoCapture(dev)
    if not cap.isOpened():
        print('Cannot open camera')
        return
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_HEIGHT)
    for _ in range(5):
        cap.read()

    results = {}

    if args.headless:
        # 无头模式: 自动采样画面中心区域
        for color in colors_to_cal:
            current_color = color
            input(f'\n==> Aim camera at a {color.upper()} object, then press Enter...')
            for _ in range(10):
                cap.read()
            ret, frame = cap.read()
            if not ret:
                print('Read failed')
                continue
            hsv = cv2.cvtColor(cv2.GaussianBlur(frame, (5, 5), 0),
                               cv2.COLOR_BGR2HSV)
            h, w = frame.shape[:2]
            roi = hsv[h//2-40:h//2+40, w//2-40:w//2+40]
            for y in range(0, roi.shape[0], 4):
                for x in range(0, roi.shape[1], 4):
                    samples[color].append(tuple(int(v) for v in roi[y, x]))

            rng = compute_range(samples[color])
            if rng:
                results[color] = {'lower': rng[0], 'upper': rng[1],
                                  'samples': len(samples[color])}
                print(f'  {color}: lower={rng[0]} upper={rng[1]} '
                      f'({len(samples[color])} samples)')
            else:
                print(f'  {color}: not enough samples')
    else:
        # GUI 模式 (需要显示器)
        print('\nControls:')
        print('  Click: sample HSV at point')
        print('  1/2/3: switch to blue/yellow/white')
        print('  r: reset current color samples')
        print('  c: compute and show ranges')
        print('  s: save and exit')
        print('  q: quit without saving')

        cv2.namedWindow('Calibrate')
        param = {'hsv': None}
        cv2.setMouseCallback('Calibrate', on_mouse, param)

        while True:
            ret, frame = cap.read()
            if not ret:
                continue
            blurred = cv2.GaussianBlur(frame, (5, 5), 0)
            hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)
            param['hsv'] = hsv

            # 显示当前颜色和采样数
            info = (f'Color: {current_color.upper()} '
                    f'Samples: {len(samples[current_color])} '
                    f'[1=B 2=Y 3=W r=reset c=calc s=save q=quit]')
            display = frame.copy()
            cv2.putText(display, info, (10, 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

            # 中心区域标记
            h, w = frame.shape[:2]
            cv2.rectangle(display, (w//2-40, h//2-40), (w//2+40, h//2+40),
                          (0, 255, 255), 1)

            cv2.imshow('Calibrate', display)
            key = cv2.waitKey(30) & 0xFF

            if key == ord('1'):
                current_color = 'blue'
                print(f'Switched to: {current_color}')
            elif key == ord('2'):
                current_color = 'yellow'
                print(f'Switched to: {current_color}')
            elif key == ord('3'):
                current_color = 'white'
                print(f'Switched to: {current_color}')
            elif key == ord('r'):
                samples[current_color] = []
                print(f'Reset {current_color} samples')
            elif key == ord('c'):
                for c in colors_to_cal:
                    rng = compute_range(samples[c])
                    if rng:
                        print(f'  {c}: lower={rng[0]} upper={rng[1]}')
                    else:
                        print(f'  {c}: need >= 3 samples')
            elif key == ord('s'):
                for c in colors_to_cal:
                    rng = compute_range(samples[c])
                    if rng:
                        results[c] = {'lower': rng[0], 'upper': rng[1],
                                      'samples': len(samples[c])}
                break
            elif key == ord('q'):
                cap.release()
                cv2.destroyAllWindows()
                print('Cancelled.')
                return

        cv2.destroyAllWindows()

    cap.release()

    if results:
        save_calibration(results)
        apply_calibration()
    else:
        print('No calibration data to save.')


if __name__ == '__main__':
    main()
