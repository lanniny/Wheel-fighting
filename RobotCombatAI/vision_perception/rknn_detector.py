#!/usr/bin/env python3
"""
RKNN YOLOv5 检测器 - 适配瑞芯微 RK3576/RK3588 NPU
使用 rknn-toolkit-lite2 进行推理
"""

import logging
import numpy as np
import cv2

logger = logging.getLogger(__name__)

try:
    from rknnlite.api import RKNNLite
except ImportError:
    RKNNLite = None
    logger.warning("rknnlite not installed, RKNN backend unavailable")

# YOLOv5 anchors (default COCO)
ANCHORS = [
    [[10, 13], [16, 30], [33, 23]],     # stride 8
    [[30, 61], [62, 45], [59, 119]],     # stride 16
    [[116, 90], [156, 198], [373, 326]], # stride 32
]
STRIDES = [8, 16, 32]


def letterbox(img, new_shape=(640, 640), color=(114, 114, 114)):
    """Resize image with letterbox padding."""
    shape = img.shape[:2]  # [h, w]
    r = min(new_shape[0] / shape[0], new_shape[1] / shape[1])
    new_unpad = (int(round(shape[1] * r)), int(round(shape[0] * r)))
    dw = (new_shape[1] - new_unpad[0]) / 2
    dh = (new_shape[0] - new_unpad[1]) / 2

    if shape[::-1] != new_unpad:
        img = cv2.resize(img, new_unpad, interpolation=cv2.INTER_LINEAR)

    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    img = cv2.copyMakeBorder(img, top, bottom, left, right,
                              cv2.BORDER_CONSTANT, value=color)
    return img, r, (dw, dh)


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def yolov5_post_process(outputs, conf_thres=0.25, iou_thres=0.45,
                         img_size=640, num_classes=3):
    """
    YOLOv5 后处理: 解码3个特征图输出 -> [x1,y1,x2,y2,conf,cls]

    Args:
        outputs: list of 3 numpy arrays from RKNN inference
        conf_thres: confidence threshold
        iou_thres: IoU threshold for NMS
        img_size: model input size
        num_classes: number of classes

    Returns:
        numpy array shape (N, 6) -> [x1,y1,x2,y2,conf,cls]
    """
    all_boxes = []

    for i, feat in enumerate(outputs):
        # feat shape: (1, num_anchors, h, w, 5+num_classes) or (1, h*w*anchors, 5+num_classes)
        if len(feat.shape) == 5:
            # (1, 3, h, w, 5+nc)
            feat = feat[0]  # remove batch
        elif len(feat.shape) == 4:
            # (1, 3*(5+nc), h, w) -> reshape
            bs, c, h, w = feat.shape
            na = 3
            nc = c // na - 5
            feat = feat.reshape(na, 5 + nc, h, w).transpose(0, 2, 3, 1)
        elif len(feat.shape) == 3:
            # (1, h*w*3, 5+nc) - already flattened
            feat = feat[0]
            # Process as flat predictions
            conf = sigmoid(feat[:, 4])
            mask = conf > conf_thres
            feat = feat[mask]
            if len(feat) == 0:
                continue
            box_xy = sigmoid(feat[:, :2])
            box_wh = feat[:, 2:4]
            cls_scores = sigmoid(feat[:, 5:])
            cls_id = np.argmax(cls_scores, axis=1)
            cls_conf = cls_scores[np.arange(len(cls_scores)), cls_id]
            total_conf = sigmoid(feat[:, 4]) * cls_conf

            # Convert to x1y1x2y2 (rough, grid info lost)
            x1 = (box_xy[:, 0] - box_wh[:, 0] / 2) * img_size
            y1 = (box_xy[:, 1] - box_wh[:, 1] / 2) * img_size
            x2 = (box_xy[:, 0] + box_wh[:, 0] / 2) * img_size
            y2 = (box_xy[:, 1] + box_wh[:, 1] / 2) * img_size

            for j in range(len(feat)):
                all_boxes.append([x1[j], y1[j], x2[j], y2[j],
                                  total_conf[j], cls_id[j]])
            continue
        else:
            continue

        na, h, w, nc_plus5 = feat.shape
        stride = STRIDES[i]
        anchors = np.array(ANCHORS[i], dtype=np.float32)

        # Create grid
        grid_y, grid_x = np.meshgrid(np.arange(h), np.arange(w), indexing='ij')

        for a in range(na):
            pred = feat[a]  # (h, w, 5+nc)
            box_xy = sigmoid(pred[:, :, :2])
            box_wh = pred[:, :, 2:4]
            obj_conf = sigmoid(pred[:, :, 4])
            cls_pred = sigmoid(pred[:, :, 5:])

            # Decode
            bx = (box_xy[:, :, 0] * 2 - 0.5 + grid_x) * stride
            by = (box_xy[:, :, 1] * 2 - 0.5 + grid_y) * stride
            bw = (box_wh[:, :, 0] * 2) ** 2 * anchors[a][0]
            bh = (box_wh[:, :, 1] * 2) ** 2 * anchors[a][1]

            x1 = bx - bw / 2
            y1 = by - bh / 2
            x2 = bx + bw / 2
            y2 = by + bh / 2

            cls_id = np.argmax(cls_pred, axis=2)
            cls_conf = np.max(cls_pred, axis=2)
            total_conf = obj_conf * cls_conf

            mask = total_conf > conf_thres
            idxs = np.where(mask)

            for idx in range(len(idxs[0])):
                r, c = idxs[0][idx], idxs[1][idx]
                all_boxes.append([
                    x1[r, c], y1[r, c], x2[r, c], y2[r, c],
                    total_conf[r, c], cls_id[r, c]
                ])

    if len(all_boxes) == 0:
        return np.array([]).reshape(0, 6)

    boxes = np.array(all_boxes, dtype=np.float32)

    # NMS
    boxes = _nms(boxes, iou_thres)
    return boxes


def _nms(boxes, iou_thres):
    """Simple NMS implementation."""
    if len(boxes) == 0:
        return boxes

    x1 = boxes[:, 0]
    y1 = boxes[:, 1]
    x2 = boxes[:, 2]
    y2 = boxes[:, 3]
    scores = boxes[:, 4]

    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]

    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(i)

        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])

        w = np.maximum(0.0, xx2 - xx1)
        h = np.maximum(0.0, yy2 - yy1)
        inter = w * h
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-6)

        inds = np.where(iou <= iou_thres)[0]
        order = order[inds + 1]

    return boxes[keep]


class RKNNDetector:
    """RKNN YOLOv5 检测器"""

    def __init__(self, model_path, input_size=640, conf_thres=0.25,
                 iou_thres=0.45, num_classes=3):
        if RKNNLite is None:
            raise RuntimeError("rknnlite not installed: pip install rknn-toolkit-lite2")

        self.input_size = input_size
        self.conf_thres = conf_thres
        self.iou_thres = iou_thres
        self.num_classes = num_classes

        logger.info("Loading RKNN model: %s", model_path)
        self.rknn = RKNNLite()

        ret = self.rknn.load_rknn(model_path)
        if ret != 0:
            raise RuntimeError(f"Failed to load RKNN model: {ret}")

        ret = self.rknn.init_runtime()
        if ret != 0:
            raise RuntimeError(f"Failed to init RKNN runtime: {ret}")

        logger.info("RKNN model loaded successfully")

    def infer(self, frame):
        """
        执行推理, 接口与 ACL YoloV5.infer() 兼容

        Args:
            frame: BGR numpy array from cv2

        Returns:
            numpy array shape (N, 6) -> [x1,y1,x2,y2,conf,cls]
            坐标已还原到原图尺寸
        """
        orig_h, orig_w = frame.shape[:2]

        # 预处理: letterbox + BGR->RGB
        img, ratio, (dw, dh) = letterbox(frame, (self.input_size, self.input_size))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        # RKNN推理
        outputs = self.rknn.inference(inputs=[img])

        # 后处理
        dets = yolov5_post_process(
            outputs,
            conf_thres=self.conf_thres,
            iou_thres=self.iou_thres,
            img_size=self.input_size,
            num_classes=self.num_classes
        )

        if len(dets) == 0:
            return np.array([]).reshape(0, 6)

        # 坐标还原到原图
        dets[:, 0] = (dets[:, 0] - dw) / ratio
        dets[:, 1] = (dets[:, 1] - dh) / ratio
        dets[:, 2] = (dets[:, 2] - dw) / ratio
        dets[:, 3] = (dets[:, 3] - dh) / ratio

        # 裁剪到图像边界
        dets[:, 0] = np.clip(dets[:, 0], 0, orig_w)
        dets[:, 1] = np.clip(dets[:, 1], 0, orig_h)
        dets[:, 2] = np.clip(dets[:, 2], 0, orig_w)
        dets[:, 3] = np.clip(dets[:, 3], 0, orig_h)

        return dets

    def release_resource(self):
        if self.rknn is not None:
            self.rknn.release()
            self.rknn = None
            logger.info("RKNN resources released")
