[根目录](../CLAUDE.md) > **vision_upload (视觉系统 v6)**

# vision_upload -- 视觉系统 v6 (实际部署)

## 变更记录 (Changelog)

| 时间 | 变更 |
|------|------|
| 2026-05-21 | Sprint 1+2 激进重构: A1-A6 + B4/B5 + C3/C4 + D3 共 10 项 (Codex 评审采纳) |
| 2026-04-08 | 初始生成模块文档 |

## Sprint 1+2 (2026-05-21) 改动摘要

| ID | 改动 | 文件 |
|----|------|------|
| A1 | 主循环 `\r` 单行刷新 → 时间节流换行 print (修 journalctl blob data) | main.py |
| A2 | ONNX 推理统计改 ring buffer (最近 100 帧) + `_max_infer_ms` 单帧峰值 | onnx_detector.py |
| A3 | `_submit_async` 内 `frame.copy()` 防 V4L2 MMAP 数据竞争 | onnx_detector.py |
| A4 | `_async_targets_ts` 时间戳, 超 0.5s 视为失效返回 [] | onnx_detector.py |
| A5 | 清理板上 `config.py.bak_*` 残留 + `.gitignore` 加 `*.bak_*` | radxa, .gitignore |
| A6 | vision.service `ExecStartPre` echo 文案: ttyAS1 → ttyUSB0 | vision.service |
| B4 | 散落 env vars (`VISION_FUSE_*` / `VISION_ONNX_*` / `VISION_LOG`) 全部收编到 config.py | config.py + detectors |
| B5 | `CAMERA_DEVICE` / `UART_PORT` 改 PEP 562 `__getattr__` 懒求值 | config.py |
| C3 | `subprocess stty low_latency` 失败 warning (不再静默) | comm.py |
| C4 | `errno 5/6/19` 硬编码 → `errno.EIO/ENXIO/ENODEV` 常量 | comm.py |
| D3 | 删除 `comm.py:send_tag()` 死代码 (已被 `_send_tag_target` 取代) | comm.py |

启动时 `config.snapshot()` 输出 17 行完整配置快照, 赛场快速诊断用。

## Backlog (赛后再做, Codex 评审建议推后)

| ID | 推后任务 | 推后原因 |
|----|---------|---------|
| B1 | main.py `VisionSystem.run()` 471 行 mega-method 拆分 | 掉台迟滞状态机横跨整个方法, 赛前重构有引入静默 bug 风险, 必须先有集成测试 |
| B2 | MJPEG `StreamServer` 类抽取 (替代全局 `_stream_jpg`) | 收益低, 风险中等 |
| C1 | Camera 重连时 detector.reset() 统一钩子 | A4 时间戳过期已缓解大部分症状 |
| C2 | ONNX worker supervisor (健康监控 + 自动重启) | A4 时间戳过期已让主循环避免追幽灵, supervisor 锦上添花 |
| E1 | /health HTTP 端点 | `systemctl status` + tail log 已够用 |
| E2 | MJPEG JPEG 编码异步化 | 1-3ms 不是瓶颈 (相对 175ms ONNX), 典型过早优化 |
| D2 | 完整类型注解 (全公共 API) | 收益低, 优先稳定 |
| -- | 辅助脚本归档 (cam_test/uart_test/uart_debug 等) | 保留实地调试方便 |

## 已知硬件限制 (2026-05-21 实测)

- **CPU 热降频严重**: 大核温度持续 92-93°C (远超 60°C trip_point), 所有核被 kernel 强制限速到 **416MHz** (原 A55=1.79GHz / A76=2.0GHz)
- **后果**: ONNX 推理从 17 FPS → 5 FPS, 主循环 9.8 → 5.5 FPS
- **风扇**: PWM 已满转 255/255, 仍压不住
- **代码层无解**, 需主人物理排查散热



## 模块职责

运行在 Radxa Cubie A7Z 上位机上的 Python 视觉感知系统（当前实际部署版本）。基于 OpenCV HSV 颜色检测实现能量块识别，支持 AprilTag/ArUco 辅助检测，通过 UART 将目标信息发送给 STM32 下位机。内嵌 MJPEG 流媒体服务器支持远程实时查看。

与 `RobotCombatAI/` 的关系: `vision_upload/` 是独立重写的轻量版视觉系统，不依赖 NPU 推理，使用纯 CPU 颜色检测。`RobotCombatAI/` 是旧版 NPU 推理管线（YOLOv5 + VIPLite），两套系统可独立部署。

## 入口与启动

- **入口文件**: `main.py`
- **systemd 服务**: `vision.service` (板端自启动)
- **启动命令**:
  ```bash
  # 正常运行 (systemd)
  sudo systemctl start vision.service

  # 手动运行
  python3 main.py --color b --priority-mode collect --stream-port 8080

  # 标定模式
  python3 main.py --calibrate

  # 无 UART 调试
  python3 main.py --no-uart --debug
  ```

## 对外接口

### UART 通信协议 (与 STM32)

与 `Core/` 模块使用完全相同的协议:

**视觉 -> STM32**: `$type,cx,cy,area,dir*CS\n`

**STM32 -> 视觉**: 单字节指令
- `b`/`y`: 设置己方颜色 (蓝/黄)
- `s`/`p`: 开始/暂停识别
- `c`/`f`: 收集模式/战斗模式

### MJPEG 流媒体

- URL: `http://<板端IP>:8080/stream`
- 帧率: 每 3 帧发送一帧标注画面
- 质量: JPEG 65%

### 命令行参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--color` | `b` | 己方颜色 (b=蓝, y=黄) |
| `--priority-mode` | `collect` | 目标优先级 (collect: N>E, attack: E>N) |
| `--stream-port` | `8080` | MJPEG 流端口 |
| `--no-stream` | - | 禁用流媒体 |
| `--no-uart` | - | 不连接串口 |
| `--calibrate` | - | HSV 标定模式 |
| `--debug` | - | 调试模式 (保存帧) |
| `--tag-backend` | `None` | Tag 检测后端 (qr/aruco/apriltag) |
| `--mode` | `fight` | 初始模式 (fight=颜色, collect=Tag) |

## 关键依赖与配置

### Python 依赖

| 包 | 用途 |
|----|------|
| `opencv-python` (4.5+) | 图像采集/处理/检测 |
| `numpy` | 数值计算 |
| `pyserial` | UART 通信 |
| `pupil-apriltags` (可选) | AprilTag 检测后端 |

### 环境变量 (config.py 支持)

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `VISION_CAMERA` | 自动探测 | 相机设备路径 |
| `VISION_UART` | 自动探测 | UART 设备路径 |
| `VISION_WB_TEMP` | `4200` | 白平衡色温 (K) |
| `VISION_AUTO_WB` | `0` | 自动白平衡 (0=固定) |
| `VISION_FPS` | `30` | 目标帧率 |
| `VISION_CLAHE` | `1` | CLAHE 光照均衡 |
| `VISION_OWN_ONLY` | `0` | 单色检测模式 |
| `VISION_FOURCC` | `MJPG` | 相机编码格式 |
| `VISION_LOG` | `/tmp/vision_det.log` | 检测日志路径 |

### HSV 颜色阈值 (config.py)

| 颜色 | H | S | V |
|------|---|---|---|
| 蓝色 | 100-130 | 60-255 | 40-255 |
| 黄色 | 22-42 | 80-255 | 40-255 |
| 白色 | 0-180 | 0-18 | 230-255 |

**关键调参经验**:
- 固定白平衡 WB=4200K 是最关键参数，消除暖色调偏移
- 黄色需要专用面积过滤 (MIN=20000, MAX=70000)
- 黄色 S 均值验证 (>120) 防止蓝色冒充

## 数据模型

### Target 类 (detector.py)

```python
class Target:
    color: str          # 'blue'/'yellow'/'white'
    cx, cy: int         # 目标中心坐标
    x, y, w, h: int     # 边界框
    area: float         # 面积 (像素)
    direction: float    # 相对画面中心偏移 [-1, +1]
    distance_level: str # 'near'/'mid'/'far'
    solidity: float     # 凸度
```

### 检测管线流程

```
Camera.read() -> 预处理(CLAHE+GaussianBlur+HSV)
  -> 蓝色检测 -> 蓝色排斥掩码 -> 黄色检测
  -> 近距离回退检测 -> Tracker平滑
  -> 分类(friends/enemies/neutrals)
  -> 优先级选择 -> UART发送
  -> 流媒体标注发布
```

### Tracker (帧间跟踪平滑)

- EMA 自适应平滑 (快速运动低平滑, 慢速高平滑)
- 速度预测 (线性外推)
- 面积约束 (2x 变化限制)
- 颜色切换冷却 (防止闪烁)
- 新目标确认 (连续 N 帧出现才输出)

### UartComm 特性

- 自适应发送频率: 目标运动快 30Hz, 慢 20Hz, 无目标 5Hz
- 方向滑动窗口平滑
- 自动重连 (连续 3 次写错误触发)
- XOR 校验和
- 设备自动探测 (USB转TTL > GPIO UART)

## 测试与质量

- **无自动化测试**: 通过实际硬件 + 流媒体远程调试验证
- **标定工具**: `calibrate.py` (GUI/headless 两种模式)
- **独立流媒体调试**: `stream.py` 可单独启动
- **检测日志**: `/tmp/vision_det.log` 滚动记录，含性能分析 (PERF)
- **cam_test.py / uart_test.py**: 相机和串口独立测试脚本

## 常见问题 (FAQ)

**Q: 为什么不用 NPU 推理?**
A: HSV 颜色检测在 CPU 上足够快 (~15ms/帧)，且比赛能量块为纯色圆柱体，颜色检测已够用。NPU 方案 (`RobotCombatAI/realtime_vision.py`) 可在需要更精确检测时切换。

**Q: 白平衡为什么要固定?**
A: 自动白平衡会导致暖光环境下所有物体偏黄，蓝色能量块 H 值偏移到黄色范围造成严重误检。固定 WB=4200K (冷色温) 是解决方案。

**Q: DETECT_OWN_ONLY 模式是什么?**
A: 单色检测模式，只检测己方颜色能量块并发送 F(后退)，其他情况发送 X(自由行动)。适用于 STM32 端依赖红外传感器做主要寻敌的策略。

**Q: Tag 检测和颜色检测如何协同?**
A: 格斗模式下每帧做颜色检测，每 N 帧做 Tag 辅助检测。如果 Tag 结果与颜色目标空间匹配 (<80px)，用 Tag ID 覆盖颜色分类结果（更精确）。

## 相关文件清单

| 文件 | 行数 | 职责 |
|------|------|------|
| `main.py` | 589 | 主入口, VisionSystem 类, MJPEG 服务器, 检测日志 |
| `config.py` | 226 | 统一配置 (相机/HSV/UART/检测参数), 环境变量覆盖 |
| `detector.py` | 686 | ColorDetector + Tracker + TagDetector |
| `comm.py` | 324 | UartComm (UART 通信, 协议编码, 自动重连) |
| `stream.py` | 145 | 独立 MJPEG 流媒体服务器 (带检测标注) |
| `calibrate.py` | 232 | HSV 颜色标定工具 (GUI + headless) |
| `vision.service` | 15 | systemd 服务配置 |
| `cam_test.py` | -- | 相机独立测试 |
| `uart_test.py` | -- | 串口独立测试 |
