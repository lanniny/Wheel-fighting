[根目录](../CLAUDE.md) > **RobotCombatAI (AI视觉感知子系统)**

# RobotCombatAI -- AI 视觉感知子系统 (NPU 推理管线)

## 变更记录 (Changelog)

| 时间 | 变更 |
|------|------|
| 2026-04-08 | 初始生成模块文档 |

## 模块职责

基于 YOLOv5 目标检测的 AI 视觉感知系统。设计用于在 Radxa Cubie A7Z 上通过 VeriSilicon VIP9000 NPU 加速推理，检测比赛中的 3 类圆柱体能量块（红/蓝/白），通过 UART 将目标信息发送给 STM32。

**当前状态**: 此模块为旧版 NPU 推理管线架构，已实现多后端支持（VIPLite/RKNN/ACL），但当前实际比赛部署使用的是 `vision_upload/` 的颜色检测方案。本模块适用于需要更精确目标检测（形状+颜色联合判断）的场景。

## 入口与启动

- **入口文件**: `scripts/inference/realtime_vision.py`
- **启动命令**:
  ```bash
  # VIPLite 后端 (推荐, A733 NPU)
  python3 scripts/inference/realtime_vision.py \
    --backend viplite --model models/yolov5.nb --team-color b

  # 自动检测后端
  python3 scripts/inference/realtime_vision.py --backend auto

  # RKNN 后端 (瑞芯微平台, 本项目不适用)
  python3 scripts/inference/realtime_vision.py --backend rknn --model xxx.rknn
  ```

## 对外接口

### UART 通信协议

与 `Core/` 和 `vision_upload/` 使用完全相同的协议:
`$type,cx,cy,area,dir*CS\n`

### 推理后端接口

所有检测器实现统一的 `infer(frame)` 接口:
```python
def infer(self, frame: np.ndarray) -> np.ndarray:
    """
    Args:
        frame: BGR numpy array from cv2
    Returns:
        numpy array shape (N, 6) -> [x1, y1, x2, y2, conf, cls]
    """
```

## 关键依赖与配置

### 推理后端

| 后端 | 模型格式 | 平台 | 状态 |
|------|----------|------|------|
| VIPLite | `.nb` (NBG) | 全志 A733 + VIP9000 | 可用 (持久进程 + subprocess 回退) |
| RKNN | `.rknn` | 瑞芯微 | 不适用 (本项目非瑞芯微平台) |
| ACL | `.om` | 华为昇腾 | 已废弃 (旧 OrangePi AIpro 平台) |

### VIPLite 检测器特性 (viplite_detector.py)

- **持久进程模式**: 通过 wrapper 脚本维护长驻 C 推理进程，避免逐帧 fork 开销
- **超时保护**: select() 超时 10s，自动降级到 subprocess 回退
- **帧交换**: 通过 `/dev/shm/` RAM 磁盘传递 JPEG 帧
- **输出解析**: 正则匹配 ai-sdk yolov5 的 stdout 格式

### 目标选择器 (target_selector.py)

- **类别映射**: class_id -> E/F/N (根据队伍颜色动态映射)
  - 蓝方: 红=敌(E), 蓝=友(F), 白=中立(N)
  - 黄方: 红=友(F), 蓝=敌(E), 白=中立(N)
- **优先级**: N(中立, 100) > E(敌方, 80) > F(友方, -50)
- **EMA 平滑**: alpha=0.3, 同类型目标位置/面积/方向平滑
- **目标粘滞**: 连续 3 帧新类型才切换，防止闪烁

## 数据模型

### 模型规格

| 模型 | 格式 | 输入 | 推理时间 | 类别 |
|------|------|------|---------|------|
| YOLOv5s | `.nb` (NBG) | 640x640 UINT8 | ~46ms (NPU) | 3 类圆柱体 |
| YOLOv5s | `.om` (昇腾) | 640x640 FP32 | ~15ms (旧平台) | 3 类圆柱体 |
| ResNet50 | `.nb`/`.om` | 224x224 UINT8 | 待测 | 分类(可选) |

### RealtimeVision 管线

```
Camera(640x480) -> [可选 CLAHE] -> YOLOv5 infer
  -> TargetSelector.select(detections)
  -> UARTComm.send_target_throttled()
```

## 测试与质量

- **无自动化测试**
- **调试方式**: `--display` 参数启用 OpenCV 预览窗口
- **日志**: Python logging, 每 5 秒输出 FPS / 目标 / 丢帧统计

## 常见问题 (FAQ)

**Q: 本模块和 vision_upload/ 的区别?**
A: 本模块使用 YOLOv5 NPU 推理做目标检测（识别形状+颜色），`vision_upload/` 使用纯 CPU HSV 颜色检测。NPU 方案更精确但延迟更高（~46ms vs ~15ms），颜色方案简单但对光照敏感。

**Q: RKNN 后端能用吗?**
A: 不能。Radxa Cubie A7Z 使用全志 A733 SoC + VeriSilicon NPU，不是瑞芯微方案。RKNN 后端代码保留但不适用。

**Q: 旧 ACL 代码还能用吗?**
A: 不能。ACL 是华为昇腾 NPU 的推理框架，旧版 OrangePi AIpro 平台专用，当前硬件不支持。

## 相关文件清单

### 活跃代码

| 文件 | 行数 | 职责 |
|------|------|------|
| `scripts/inference/realtime_vision.py` | 396 | 主管线: 相机采集 + 推理 + 发送 |
| `vision_perception/viplite_detector.py` | 317 | VIPLite NPU 检测器 (持久进程) |
| `vision_perception/target_selector.py` | 166 | 多目标优先级选择 + EMA 平滑 |
| `vision_perception/uart_comm.py` | 174 | UART 通信 (线程安全, 自动重连) |
| `scripts/vision_service.service` | -- | systemd 服务配置 |
| `scripts/setup/deploy_cubie_a7z.sh` | -- | 板端部署脚本 |

### 旧版/废弃代码

| 文件 | 状态 | 说明 |
|------|------|------|
| `vision_perception/rknn_detector.py` | 不适用 | RKNN 后端 (瑞芯微平台) |
| `vision_perception/yolo_detection/objDet_yolov5.py` | 已废弃 | ACL 昇腾后端 |
| `vision_perception/resnet_classification/` | 已废弃 | ResNet50 分类 (昇腾) |
| `scripts/inference/yolov5_detection.py` | 已废弃 | 旧版推理入口 |
| `scripts/inference/resnet_classification.py` | 已废弃 | 旧版分类入口 |

### 模型文件

| 路径 | 格式 | 说明 |
|------|------|------|
| `models/onnx_models/yolov5s_*.onnx` | ONNX | 源模型 (PC 端转换用) |
| `models/om_models/yolov5s_*.om` | OM | 昇腾格式 (已废弃) |
| `models/om_models/resnet50.om` | OM | 昇腾格式 (已废弃) |
