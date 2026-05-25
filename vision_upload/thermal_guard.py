"""thermal_guard.py — CPU 温度感知自动降级守护.

设计目标:
  Cubie A7Z 在长时间 ONNX 推理时 CPU 大核 (cpu6,7 A76) 温度会持续 75-93°C,
  kernel 触发热降频后 ONNX 推理时间从 58ms 飙升到 159ms+, 颜色判断准确度下降,
  最终触发 HSV/ONNX 颜色分歧, 把蓝/黄目标误判为 white (中立) → STM32 收不到
  正确的 E/F 信号 → 全部进攻.

策略:
  - 周期采样 /sys/class/thermal/thermal_zone*/temp (单位 m°C)
  - 取所有 zone 的最高温度 (大核优先)
  - 温度 >= HIGH (默认 72°C): 触发 throttle → 通知 FusedDetector 关闭 ONNX
  - 温度 <= LOW (默认 65°C): 解除 throttle → 恢复 ONNX
  - 滞回设计 (7°C 死区) 避免边界震荡

被守护对象:
  detector 必须实现 `set_throttled(bool, reason: str = '')` 方法;
  FusedDetector / OnnxDetector 已实现, ColorDetector 没有 ONNX 不需要守护.

赛场紧急止血:
  SIGUSR1 信号: 强制切换 throttle 状态 (人工干预, 不受温度约束)
    kill -USR1 <vision_pid>
"""
import os
import glob
import time
import signal
import threading

import config


def read_cpu_temp_celsius():
    """读所有 thermal zone, 返回最高温度 (°C). 异常返回 None.

    Cubie A7Z 通常有 4-8 个 zone, 不同 zone 对应不同 cluster.
    取最高值确保不漏过热的核心.
    """
    zones = glob.glob('/sys/class/thermal/thermal_zone*/temp')
    if not zones:
        return None
    max_temp_mc = 0
    for z in zones:
        try:
            with open(z) as f:
                v = int(f.read().strip())
            if v > max_temp_mc:
                max_temp_mc = v
        except Exception:
            continue
    if max_temp_mc == 0:
        return None
    return max_temp_mc / 1000.0  # m°C → °C


class ThermalGuard:
    """温度感知守护线程, 自动 toggle detector 的 throttled 状态."""

    def __init__(self, detector,
                 high_c=None, low_c=None,
                 check_interval_s=None,
                 enabled=None):
        # 优先级: 参数 > env > config 默认
        self.high_c = float(
            high_c if high_c is not None
            else os.environ.get('VISION_THERMAL_HIGH',
                                getattr(config, 'THERMAL_HIGH_C', 72.0)))
        self.low_c = float(
            low_c if low_c is not None
            else os.environ.get('VISION_THERMAL_LOW',
                                getattr(config, 'THERMAL_LOW_C', 65.0)))
        self.check_interval_s = float(
            check_interval_s if check_interval_s is not None
            else os.environ.get('VISION_THERMAL_INTERVAL',
                                getattr(config, 'THERMAL_CHECK_INTERVAL_S', 5.0)))
        if enabled is None:
            env = os.environ.get('VISION_THERMAL_GUARD')
            if env is not None:
                enabled = env != '0'
            else:
                enabled = getattr(config, 'THERMAL_GUARD_ENABLED', True)
        self.enabled = bool(enabled)

        if self.high_c <= self.low_c:
            raise ValueError(
                f'thermal_guard: HIGH({self.high_c}) must > LOW({self.low_c})')

        self.detector = detector
        self._stop = False
        self._thread = None
        self._throttled = False           # 当前状态
        self._manual_override = False     # SIGUSR1 触发的人工状态, 不被温度覆盖
        self._last_temp = None
        self._sample_count = 0
        self._lock = threading.Lock()

        # 验证 detector 接口
        if not hasattr(detector, 'set_throttled'):
            print('[thermal_guard] WARN: detector has no set_throttled(), '
                  'guard 仅监控温度不实际降级', flush=True)

    def start(self):
        if not self.enabled:
            print('[thermal_guard] disabled (VISION_THERMAL_GUARD=0)', flush=True)
            return
        # 立即采样一次 (启动温度报告)
        t = read_cpu_temp_celsius()
        if t is None:
            print('[thermal_guard] WARN: cannot read /sys/class/thermal/, '
                  'guard 禁用', flush=True)
            return
        print(f'[thermal_guard] started: high={self.high_c}°C low={self.low_c}°C '
              f'interval={self.check_interval_s}s current={t:.1f}°C',
              flush=True)
        # 注册 SIGUSR1 紧急 toggle
        try:
            signal.signal(signal.SIGUSR1, self._on_sigusr1)
            print('[thermal_guard] SIGUSR1 hooked (kill -USR1 <pid> 切 throttle)',
                  flush=True)
        except Exception as e:
            print(f'[thermal_guard] SIGUSR1 hook failed: {e}', flush=True)

        self._thread = threading.Thread(
            target=self._loop, daemon=True, name='thermal-guard')
        self._thread.start()

    def stop(self):
        self._stop = True
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _on_sigusr1(self, signum, frame):
        """紧急人工 toggle, 不被温度覆盖直到下次 SIGUSR2."""
        with self._lock:
            self._manual_override = not self._manual_override
            target = not self._throttled
            self._apply(target,
                        reason=f'SIGUSR1 manual override={self._manual_override}')

    def _loop(self):
        while not self._stop:
            try:
                t = read_cpu_temp_celsius()
                if t is not None:
                    self._last_temp = t
                    self._sample_count += 1
                    self._evaluate(t)
                    # 每 12 次采样 (默认 60s) 输出一次温度日志
                    if self._sample_count % 12 == 1:
                        state = 'THROTTLED' if self._throttled else 'NORMAL'
                        print(f'[thermal_guard] temp={t:.1f}°C state={state}'
                              + (' (manual)' if self._manual_override else ''),
                              flush=True)
            except Exception as e:
                print(f'[thermal_guard] loop error: {e}', flush=True)
            time.sleep(self.check_interval_s)

    def _evaluate(self, temp_c):
        """温度滞回判断: 在 [low, high] 区间内保持当前状态."""
        if self._manual_override:
            return  # 人工覆盖期间不自动调整
        with self._lock:
            if not self._throttled and temp_c >= self.high_c:
                self._apply(True,
                            reason=f'temp {temp_c:.1f}°C >= HIGH {self.high_c}°C')
            elif self._throttled and temp_c <= self.low_c:
                self._apply(False,
                            reason=f'temp {temp_c:.1f}°C <= LOW {self.low_c}°C')

    def _apply(self, throttled, reason=''):
        """调用 detector.set_throttled() 并打印状态变化."""
        if throttled == self._throttled:
            return
        self._throttled = throttled
        action = 'THROTTLE ON (ONNX OFF, HSV-only)' if throttled \
            else 'THROTTLE OFF (ONNX restored)'
        print(f'[thermal_guard] *** {action} *** ({reason})', flush=True)
        if hasattr(self.detector, 'set_throttled'):
            try:
                self.detector.set_throttled(throttled, reason=reason)
            except Exception as e:
                print(f'[thermal_guard] detector.set_throttled failed: {e}',
                      flush=True)

    @property
    def state(self):
        """供主循环查询状态 (用于 status 行打印)."""
        return {
            'throttled': self._throttled,
            'manual': self._manual_override,
            'temp_c': self._last_temp,
            'samples': self._sample_count,
        }
