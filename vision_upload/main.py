#!/usr/bin/env python3
"""
轮式格斗机器人 - 视觉主程序 v3

改进 (vs v2):
  - 严格 30fps 帧率控制, 避免 CPU 空转或跑飞
  - UART 颜色心跳: 每5秒重发己方颜色, 防 STM32 丢失
  - 相机曝光/白平衡锁定: 减少赛场灯光干扰
  - 调试帧异步写入: 不阻塞主检测循环

用法:
    python3 main.py              # 正常运行 (连STM32)
    python3 main.py --debug      # 调试模式 (保存标注帧)
    python3 main.py --calibrate  # 标定模式 (打印HSV值)
    python3 main.py --no-uart    # 不连串口 (本地测试)
    python3 main.py --color y    # 指定己方颜色为黄色
"""
import sys
import time
import argparse
import threading
import cv2

import config
from detector import ColorDetector
from comm import UartComm


class Camera:
    """摄像头管理, 支持断线自动重连 + 曝光锁定"""

    def __init__(self):
        self.cap = None
        self._reconnect_interval = 2.0
        self._last_reconnect = 0.0

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
        # 锁定曝光和白平衡
        ColorDetector.set_camera_props(self.cap)
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
        print('\nCamera read failed, reconnecting...')
        self.release()
        return self._try_reconnect()

    def _try_reconnect(self):
        now = time.time()
        if now - self._last_reconnect < self._reconnect_interval:
            return None
        self._last_reconnect = now
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
        self._tx_count = 0
        self._debug_thread = None

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
        roi = hsv[h // 2 - 25:h // 2 + 25, w // 2 - 25:w // 2 + 25]
        hm, sm, vm = roi[:, :, 0].mean(), roi[:, :, 1].mean(), roi[:, :, 2].mean()
        hs, ss, vs = roi[:, :, 0].std(), roi[:, :, 1].std(), roi[:, :, 2].std()
        sys.stdout.write(
            f'\rCenter HSV: H={hm:.0f}+-{hs:.0f}  '
            f'S={sm:.0f}+-{ss:.0f}  '
            f'V={vm:.0f}+-{vs:.0f}  '
            f'FPS={self._fps:.1f}   ')
        sys.stdout.flush()

    def _async_save_debug(self, frame):
        """后台线程写调试帧, 不阻塞主循环"""
        if self._debug_thread and self._debug_thread.is_alive():
            return  # 上一帧还没写完, 跳过
        def _write(f):
            cv2.imwrite('/tmp/vision_debug.jpg', f)
        self._debug_thread = threading.Thread(target=_write, args=(frame,), daemon=True)
        self._debug_thread.start()

    def run(self):
        if not self.camera.open():
            print('Waiting for camera...')

        if self.use_uart:
            self.comm.open()

        print('Vision system running. Ctrl+C to stop.')
        print(f'Mode  : {"calibrate" if self.calibrate else "debug" if self.debug else "normal"}')
        print(f'Color : {self.comm.my_color}')
        print(f'UART  : {"on" if self.use_uart else "off"}')
        print(f'CLAHE : {"on" if getattr(config, "USE_CLAHE", True) else "off"}')
        print('-' * 55)

        frame_idx = 0
        frame_dt = 1.0 / config.TARGET_FPS   # 目标帧间隔
        next_frame = time.perf_counter()

        try:
            while True:
                t0 = time.perf_counter()

                # 1. 读帧
                frame = self.camera.read()
                if frame is None:
                    time.sleep(0.05)
                    next_frame = time.perf_counter() + frame_dt
                    continue

                # 2. 读STM32指令
                cmd = self.comm.read_command()
                if cmd:
                    print(f'\n[UART] Cmd: {cmd!r} my_color={self.comm.my_color}')

                # 3. 颜色心跳
                if self.use_uart:
                    self.comm.send_color_heartbeat()

                # 4. 标定模式
                if self.calibrate:
                    self._calibrate_frame(frame)
                    self._update_fps()
                    self._sleep_until(next_frame)
                    next_frame += frame_dt
                    continue

                # 5. 检测
                targets = self.detector.detect(frame)
                friends, enemies, neutrals = self.detector.classify(
                    targets, self.comm.my_color)

                # 6. 发送给STM32
                self.comm.send_from_detection(friends, enemies, neutrals)
                self._tx_count += 1

                # 7. 状态输出
                self._update_fps()
                frame_idx += 1
                dt_ms = (time.perf_counter() - t0) * 1000

                if frame_idx % 15 == 0:
                    ptype, pt = self.detector.get_priority_target(
                        friends, enemies, neutrals)
                    pstr = ''
                    if pt:
                        pstr = (f' >> {ptype}({pt.cx},{pt.cy} '
                                f'dir={pt.direction:+.2f})')
                    uart_s = 'OK' if (self.comm.ser and self.comm._tx_errors == 0) else 'ERR'
                    sys.stdout.write(
                        f'\r[{self._fps:5.1f}fps {dt_ms:4.1f}ms] '
                        f'UART:{uart_s} TX:{self._tx_count} '
                        f'E:{len(enemies)} N:{len(neutrals)} F:{len(friends)}'
                        f'{pstr}      ')
                    sys.stdout.flush()

                # 8. 调试帧
                if self.debug and frame_idx % 30 == 0:
                    dbg = self.detector.draw_targets(
                        frame.copy(), targets, self.comm.my_color)
                    info = (f'FPS:{self._fps:.0f} {dt_ms:.0f}ms '
                            f'MY:{self.comm.my_color} '
                            f'E:{len(enemies)} N:{len(neutrals)} F:{len(friends)}')
                    cv2.putText(dbg, info, (10, 25),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
                    self._async_save_debug(dbg)

                # 9. 帧率限速
                self._sleep_until(next_frame)
                next_frame += frame_dt

        except KeyboardInterrupt:
            print('\nStopping...')
        finally:
            self.camera.release()
            self.comm.close()
            print('Vision system stopped.')

    @staticmethod
    def _sleep_until(target):
        """精确睡眠到目标 perf_counter 时刻"""
        remaining = target - time.perf_counter()
        if remaining > 0.001:
            time.sleep(remaining)


def main():
    parser = argparse.ArgumentParser(description='Robot Vision System v3')
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
