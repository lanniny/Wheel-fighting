"""adaptive_hsv.py — Tag-Assisted Adaptive HSV (TAHSV)

AprilTag detection is lighting-invariant (black/white contrast).
When a Tag is detected, the surrounding pixels ARE the block's true color.
This module samples those pixels and dynamically adjusts HSV thresholds
to match the current environment — zero manual calibration needed.

Data flow:
  Tag detected → sample annulus HSV → rolling window → percentile bounds
  → EMA smooth → clamp to safe range → update config.HSV_YELLOW/BLUE

Integration: main.py calls feed_tag() after each Tag detection.
"""
import cv2
import numpy as np
from collections import deque

import config


class AdaptiveHSV:

    def __init__(self):
        self._defaults = {}
        for name, key in [('blue', 'HSV_BLUE'), ('yellow', 'HSV_YELLOW')]:
            src = getattr(config, key)
            self._defaults[name] = {
                'lower': src['lower'].copy(),
                'upper': src['upper'].copy(),
            }

        self._samples = {
            'blue': deque(maxlen=500),
            'yellow': deque(maxlen=500),
        }

        self._clamp_h = 12
        self._clamp_s = 12
        self._clamp_v = 15
        self._margin_h = 5
        self._margin_s = 12
        self._margin_v = 15

        self._min_samples = 60
        self._update_interval = 20
        self._feed_count = {'blue': 0, 'yellow': 0}
        self._active = {'blue': False, 'yellow': False}
        self._ema_alpha = 0.20
        self._ema_state = {'blue': None, 'yellow': None}

        self._min_tag_area = 400
        self._max_tag_area = 25000

        print('[adaptive_hsv] initialized (waiting for Tags...)', flush=True)

    def feed_tag(self, bgr_frame, tag, my_color='b'):
        tag_id = int(tag.get('id', -1))
        color_name = self._id_to_color(tag_id)
        if color_name is None:
            return

        area = tag.get('area', 0)
        if area < self._min_tag_area or area > self._max_tag_area:
            return

        pixels = self._sample_ring(bgr_frame, tag)
        if len(pixels) < 15:
            return

        self._samples[color_name].extend(pixels)
        self._feed_count[color_name] += 1

        if self._feed_count[color_name] % self._update_interval == 0:
            self._recompute(color_name)

    @staticmethod
    def _id_to_color(tag_id):
        n = getattr(config, 'TAG_ID_NEUTRAL', 0)
        b = getattr(config, 'TAG_ID_BLUE', 1)
        y = getattr(config, 'TAG_ID_YELLOW', 2)
        if tag_id == b:
            return 'blue'
        if tag_id == y:
            return 'yellow'
        return None

    @staticmethod
    def _sample_ring(bgr_frame, tag):
        cx, cy = tag['cx'], tag['cy']
        area = tag.get('area', 0)
        half = max(12, int(area ** 0.5) // 2)
        margin = max(8, int(half * 0.5))

        fh, fw = bgr_frame.shape[:2]
        ox1 = max(0, cx - half - margin)
        oy1 = max(0, cy - half - margin)
        ox2 = min(fw, cx + half + margin)
        oy2 = min(fh, cy + half + margin)

        roi_bgr = bgr_frame[oy1:oy2, ox1:ox2]
        if roi_bgr.size < 100:
            return []
        roi_hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)

        rh, rw = roi_hsv.shape[:2]
        ix1 = max(0, cx - half) - ox1
        iy1 = max(0, cy - half) - oy1
        ix2 = min(fw, cx + half) - ox1
        iy2 = min(fh, cy + half) - oy1
        ix1 = max(0, min(ix1, rw))
        iy1 = max(0, min(iy1, rh))
        ix2 = max(0, min(ix2, rw))
        iy2 = max(0, min(iy2, rh))

        mask = np.ones((rh, rw), dtype=bool)
        if ix2 > ix1 and iy2 > iy1:
            mask[iy1:iy2, ix1:ix2] = False
        mask &= (roi_hsv[:, :, 1] > 25)
        mask &= (roi_hsv[:, :, 2] > 35)

        return [tuple(p) for p in roi_hsv[mask]]

    def _recompute(self, color_name):
        """Expand-only strategy: adaptive can widen the default range but never narrow it.

        H range: union of (default, learned) — prevents over-narrowing
        S/V lower: min of (default, learned) — only relaxes, never tightens
        Clamp: prevents runaway expansion beyond safe bounds
        """
        samples = list(self._samples[color_name])
        if len(samples) < self._min_samples:
            return

        arr = np.array(samples, dtype=np.float32)
        h_v, s_v, v_v = arr[:, 0], arr[:, 1], arr[:, 2]
        d = self._defaults[color_name]

        # Learned bounds from Tag samples (with margin)
        learned_lh = np.percentile(h_v, 3) - self._margin_h
        learned_uh = np.percentile(h_v, 97) + self._margin_h
        learned_ls = np.percentile(s_v, 3) - self._margin_s
        learned_lv = np.percentile(v_v, 3) - self._margin_v

        # Expand-only: union with defaults (can only widen, never narrow)
        new_lh = min(float(d['lower'][0]), learned_lh)
        new_uh = max(float(d['upper'][0]), learned_uh)
        new_ls = min(float(d['lower'][1]), learned_ls)
        new_lv = min(float(d['lower'][2]), learned_lv)

        # Clamp: prevent runaway expansion
        new_lh = max(float(d['lower'][0]) - self._clamp_h, new_lh, 0.0)
        new_uh = min(float(d['upper'][0]) + self._clamp_h, new_uh, 180.0)
        new_ls = max(float(d['lower'][1]) - self._clamp_s, new_ls, 0.0)
        new_lv = max(float(d['lower'][2]) - self._clamp_v, new_lv, 0.0)

        if new_uh <= new_lh + 8:
            return

        # EMA smooth
        alpha = self._ema_alpha
        old = self._ema_state[color_name]
        if old is not None:
            new_lh = old[0] * (1 - alpha) + new_lh * alpha
            new_uh = old[1] * (1 - alpha) + new_uh * alpha
            new_ls = old[2] * (1 - alpha) + new_ls * alpha
            new_lv = old[3] * (1 - alpha) + new_lv * alpha
        self._ema_state[color_name] = (new_lh, new_uh, new_ls, new_lv)

        # Apply to config
        cfg_key = f'HSV_{color_name.upper()}'
        target = getattr(config, cfg_key, None)
        if target is None:
            return

        old_lower = list(target['lower'])
        old_upper_h = int(target['upper'][0])

        target['lower'][0] = int(round(new_lh))
        target['lower'][1] = int(round(new_ls))
        target['lower'][2] = int(round(new_lv))
        target['upper'][0] = int(round(new_uh))

        was_active = self._active[color_name]
        self._active[color_name] = True

        if not was_active:
            print(f'[adaptive_hsv] {color_name} ACTIVATED '
                  f'L:{old_lower} U_H:{old_upper_h} → '
                  f'L:{list(target["lower"])} U_H:{int(target["upper"][0])} '
                  f'(n={len(samples)})', flush=True)
        elif self._feed_count[color_name] % (self._update_interval * 5) == 0:
            print(f'[adaptive_hsv] {color_name} '
                  f'L:{list(target["lower"])} U_H:{int(target["upper"][0])} '
                  f'(n={len(samples)}, feeds={self._feed_count[color_name]})',
                  flush=True)

    @property
    def status_str(self):
        parts = []
        for c in ['blue', 'yellow']:
            st = 'ON' if self._active[c] else 'wait'
            n = len(self._samples[c])
            parts.append(f'{c}:{st}({n})')
        return ' '.join(parts)
