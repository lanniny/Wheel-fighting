#!/usr/bin/env python3
"""
轮式格斗机器人 - 视觉主程序 v2
新增: 摄像头断线自动重连, 优雅退出, 性能统计
用法:
    python3 main.py              # 正常运行(连STM32)
    python3 main.py --debug      # 调试模式(保存标注帧)
    python3 main.py --calibrate  # 标定模式(打印HSV值)
    python3 main.py --no-uart    # 不连串口(本地测试)
"""
import sys
import os
import time
import argparse
import cv2
import numpy as np

import config
from detector import ColorDetector
from comm import UartComm


class Camera:
    """摄像头管理, 支持断线自动重连"""

    def __init__(self):
        self.cap = None
        self._reconnect_interval = 2.0
        self._last_reconnect = 0

    def open(self):
        dev = config.CAMERA_DEVICE
        print(f'Opening camera: {dev}')
        self.cap = cv2.VideoCapture(dev)
        if not self.cap.isOpened():
            print(f'ERROR: cannot open {dev}')
            return False
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_HEIGHT)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        # 预热
        for _ in range(5):
            self.cap.read()
        print(f'Camera ready: {config.CAMERA_WIDTH}x{config.CAMERA_HEIGHT}')
        return True

    def read(self):
        """读取一帧, 失败时尝试重连"""
        if self.cap is None or not self.cap.isOpened():
            return self._try_reconnect()

        ret, frame = self.cap.read()
        if ret:
            return frame

        # 读取失败, 尝试重连
        print('\nCamera read failed, reconnecting...')
        self.release()
        return self._try_reconnect()

    def _try_reconnect(self):
        now = time.time()
        if now - self._last_reconnect < self._reconnect_interval:
            return None
        self._last_reconnect = now

        # 重新探测设备
        config.CAMERA_DEVICE = config.find_camera()
        if self.open():
            print('Camera reconnected!')
            ret, frame = self.cap.read()
            return frame if ret else None
        return None

    def release(self):
        if self.cap:
            self.cap.release()
            self.cap = None


class VisionSystem:
    def __init__(self, debug=False, calibrate=False, use_uart=True):
        self.debug = debug
        self.calibrate = calibrate
        self.use_uart = use_uart
        self.camera = Camera()
        self.detector = ColorDetector(enable_tracking=True)
        self.comm = UartComm()
        self._frame_count = 0
        self._fps_timer = time.time()
        self._fps = 0.0

    def _update_fps(self):
        self._frame_count += 1
        elapsed = time.time() - self._fps_timer
        if elapsed >= 1.0:
            self._fps = self._frame_count / elapsed
            self._frame_count = 0
            self._fps_timer = time.time()

    def _calibrate_frame(self, frame):
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        h, w = frame.shape[:2]
        roi = hsv[h//2-25:h//2+25, w//2-25:w//2+25]
        hm, sm, vm = roi[:,:,0].mean(), roi[:,:,1].mean(), roi[:,:,2].mean()
        hs, ss, vs = roi[:,:,0].std(), roi[:,:,1].std(), roi[:,:,2].std()
        sys.stdout.write(
            f'\rCenter HSV: H={hm:.0f}+-{hs:.0f}  '
            f'S={sm:.0f}+-{ss:.0f}  '
            f'V={vm:.0f}+-{vs:.0f}  '
            f'FPS={self._fps:.1f}   ')
        sys.stdout.flush()

    def run(self):
        if not self.camera.open():
            print('Waiting for camera...')

        if self.use_uart:
            self.comm.open()

        print('Vision system running. Ctrl+C to stop.')
        print(f'Mode: {"calibrate" if self.calibrate else "debug" if self.debug else "normal"}')
        print(f'My color: {self.comm.my_color}')
        print('-' * 50)

        frame_idx = 0

        try:
            while True:
                t0 = time.time()

                # 1. 读帧 (含自动重连)
                frame = self.camera.read()
                if frame is None:
                    time.sleep(0.1)
                    continue

                # 2. 读STM32指令
                cmd = self.comm.read_command()
                if cmd:
                    print(f'\n[UART] Command: {cmd} (my_color={self.comm.my_color})')

                # 3. 标定模式
                if self.calibrate:
                    self._calibrate_frame(frame)
                    self._update_fps()
                    continue

                # 4. 检测
                targets = self.detector.detect(frame)
                friends, enemies, neutrals = self.detector.classify(
                    targets, self.comm.my_color)

                # 5. 发送给STM32
                self.comm.send_from_detection(friends, enemies, neutrals)

                # 6. 状态输出
                self._update_fps()
                frame_idx += 1
                dt_ms = (time.time() - t0) * 1000

                if frame_idx % 15 == 0:
                    prio_type, prio_t = self.detector.get_priority_target(
                        friends, enemies, neutrals)
                    prio_str = ''
                    if prio_t:
                        prio_str = f' >> {prio_type}({prio_t.cx},{prio_t.cy} dir={prio_t.direction:+.2f})'
                    sys.stdout.write(
                        f'\r[{self._fps:5.1f}fps {dt_ms:4.1f}ms] '
                        f'E:{len(enemies)} N:{len(neutrals)} F:{len(friends)}'
                        f'{prio_str}      ')
                    sys.stdout.flush()

                # 7. 调试帧保存
                if self.debug and frame_idx % 30 == 0:
                    dbg = self.detector.draw_targets(
                        frame.copy(), targets, self.comm.my_color)
                    info = (f'FPS:{self._fps:.0f} {dt_ms:.0f}ms '
                            f'MY:{self.comm.my_color} '
                            f'E:{len(enemies)} N:{len(neutrals)} F:{len(friends)}')
                    cv2.putText(dbg, info, (10, 25),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
                    cv2.imwrite('/tmp/vision_debug.jpg', dbg)

        except KeyboardInterrupt:
            print('\nStopping...')
        finally:
            self.camera.release()
            self.comm.close()
            print('Vision system stopped.')


def main():
    parser = argparse.ArgumentParser(description='Robot Vision System v2')
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--calibrate', action='store_true')
    parser.add_argument('--no-uart', action='store_true')
    parser.add_argument('--color', choices=['b', 'y'], default='b')
    args = parser.parse_args()

    vision = VisionSystem(
        debug=args.debug,
        calibrate=args.calibrate,
        use_uart=not args.no_uart,
    )
    vision.comm.my_color = args.color
    vision.run()


if __name__ == '__main__':
    main()
