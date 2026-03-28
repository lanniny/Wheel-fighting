# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

2025中国高校智能机器人创意大赛——轮式机器人格斗A。三合一仓库：**嵌入式控制固件**（STM32F407 + FreeRTOS）、**AI视觉感知**（Radxa Cubie A7Z + VIP9000 NPU）、**机械设计**（SolidWorks + 制造文件）。

**竞赛约束**: 机器人≤4KG，出发区≤30×30cm，2分钟/场，擂台2.4m×2.4m×6cm高，AprilTag能量块计分。

## 系统架构

```
┌─────────────────────────────────────────┐
│   Radxa Cubie A7Z (上位机/视觉处理)      │
│   全志A733 SoC, 2×A76+6×A55            │
│   VeriSilicon VIP9000 NPU (3TOPS@INT8)  │
│   YOLOv5目标检测, Debian Linux          │
└──────────────┬──────────────────────────┘
               │ UART 115200bps (USART2: PD5-TX, PD6-RX)
┌──────────────┴──────────────────────────┐
│   STM32F407VET6 (下位机/实时控制)        │
│   168MHz, 512KB Flash, FreeRTOS         │
│   电机PID闭环 + 传感器融合 + 状态机      │
└─────────────────────────────────────────┘
```

**上位机远程访问**: `ssh radxa@192.168.31.162`（密码: radxa）

## 项目结构

```
根目录/
├── Core/                   # STM32 用户代码（主要开发区）
│   ├── Inc/                # 头文件
│   └── Src/                # 源文件（motor, encoder, pid, robot_*, sensor等）
├── Drivers/                # STM32 HAL驱动 + CMSIS（CubeMX生成，勿手动修改）
├── freertos/               # FreeRTOS内核源码
├── MDK-ARM/                # Keil µVision工程文件
│   └── robot.uvprojx       # 主工程文件
├── robot.ioc               # STM32CubeMX配置（引脚/时钟/外设定义源）
├── RobotCombatAI/          # AI视觉感知子系统
│   ├── vision_perception/  # 核心推理模块
│   ├── scripts/inference/  # 推理入口脚本
│   └── models/             # NPU模型文件 (.nb for VIPLite, .om为旧格式)
├── 创意机器人/              # SolidWorks机械设计
├── 3d/                     # 3D打印文件（.3MF）
├── 总框架.md                # 完整系统架构设计文档
├── FreeRTOS框架设计.md      # RTOS任务设计文档
└── 方案总结.md              # 竞赛规则 + 备赛方案 + 采购清单
```

## 嵌入式固件架构 (Core/)

### 构建与烧录

- **IDE**: Keil MDK-ARM V5.32（ARM Compiler）
- **工程文件**: `MDK-ARM/robot.uvprojx`
- **CubeMX配置**: `robot.ioc`（修改引脚/外设后重新生成代码）
- **输出**: `MDK-ARM/build/robot.hex`

### MCU硬件资源映射

| 外设 | 用途 | 引脚/配置 |
|------|------|-----------|
| TIM4 CH1-CH4 | 电机PWM输出 (~20kHz) | PD12-PD15, ARR=4199 |
| TIM1/3/5/8 | 编码器模式（4路） | PE9/11, PA6/7, PA0/1, PC6/7 |
| ADC1 + DMA2_S0 | 红外测距×4（480采样周期） | PA4, PA5, PC4, PC5 |
| ADC2 + DMA2_S2 | 灰度传感器×2（边缘检测） | PC0, PC1 |
| USART2 + DMA1 | 与Cubie A7Z通信 | PD5(TX), PD6(RX), 115200bps |
| I2C1 | OLED显示 | PB6(SCL), PB7(SDA) |
| I2C2 | MPU-6050 IMU | PB10(SCL), PB11(SDA) |
| GPIO (PE0-4,12-14) | 红外避障×8 | 数字输入 |
| TIM7 | FreeRTOS系统节拍 | 1ms tick |

### FreeRTOS任务设计

```
ControlTask  (优先级3, 2ms周期)  → 编码器读取 → 边缘安全检查 → PID计算 → PWM输出
DecisionTask (优先级2, 20ms周期) → 状态机执行 → 策略决策 → 目标速度设定
DebugTask    (优先级1, 100ms周期)→ 串口调试输出 → 系统监控
```

**RTOS配置**: 抢占式调度, 堆48KB, 最大优先级8, tick=1ms

**注意**: 当前实现部分使用主循环轮询而非完整FreeRTOS任务调度，架构文档描述的是目标设计。

### 关键固件模块

| 文件 | 职责 |
|------|------|
| `Core/Src/main.c` | 入口、初始化、主循环 |
| `Core/Src/motor.c` | 电机PWM控制，H桥方向 |
| `Core/Src/encoder.c` | 编码器读取、速度计算 |
| `Core/Src/pid.c` | PID闭环控制器 |
| `Core/Src/robot_roaming.c` | 漫游/收集模式状态机 |
| `Core/Src/robot_up.c` | 上台策略 |
| `Core/Src/robot_fight.c` | 格斗/对抗策略 |
| `Core/Src/shade.c` | 灰度传感器（边缘检测） |
| `Core/Src/dis_sensor.c` | 红外测距处理 |
| `Core/Src/obstacle.c` | 红外避障读取 |
| `Core/Src/oled.c` | OLED显示驱动 |

### 电机控制参数

- **PWM频率**: ~20kHz (PSC=0, ARR=4199)
- **速度输入范围**: 0-1000 (SPEED_MAX_INPUT)
- **预设速度档位**: Low=300, Medium=600, High=800, TurnMedium=400, TurnSuper=600

### 状态机流程

```
IDLE → FIND_PLATFORM → CLIMBING → ON_PLATFORM → ATTACK → EDGE_AVOIDANCE → (循环)
```

边缘检测触发时立即覆盖为 EDGE_AVOIDANCE（安全优先）。

## AI视觉系统 (RobotCombatAI/)

运行平台：**Radxa Cubie A7Z + 全志A733 + VeriSilicon VIP9000 NPU (3 TOPS@INT8)**。使用 VIPLite 驱动进行推理，模型格式为 `.nb` (NBG)。

> **重要**: 此板 **不是** 瑞芯微方案，不可使用 RKNN。NPU 驱动节点为 `/dev/vipcore`。

### NPU 技术栈

| 项目 | 说明 |
|------|------|
| NPU 芯片 | VeriSilicon VIP9000 (3 TOPS@INT8) |
| 驱动节点 | `/dev/vipcore` |
| 模型格式 | `.nb` (NBG - Network Binary Graph) |
| 转换工具 | ACUITY Toolkit (x86 Docker 容器, A733 用 v2.0.10) |
| 推理运行时 | VIPLite C API (awnn) — **无官方 Python API** |
| SDK 仓库 | `github.com/ZIFENG278/ai-sdk.git` |
| NPU 版本 | A733 = v3, 软件版本 v2.0 |
| 支持框架 | TensorFlow, TFLite, PyTorch, Caffe, DarkNet, ONNX, Keras |

### 模块依赖

```
scripts/inference/realtime_vision.py  →  RealtimeVision(主管线)
    ├── vision_perception/rknn_detector.py  →  RKNNDetector (旧RKNN后端, 待替换)
    ├── vision_perception/target_selector.py  →  TargetSelector(目标优先级)
    ├── vision_perception/uart_comm.py  →  UARTComm(STM32通信)
    └── [待适配] ai-sdk/examples/yolov5/  →  VIPLite C推理程序

旧ACL后端 (已废弃):
    vision_perception/yolo_detection/objDet_yolov5.py  →  需替换为 VIPLite 方案
```

### 板端环境搭建

```bash
# SSH 连接
ssh radxa@192.168.31.162  # 密码: radxa

# 1. 确认 NPU 驱动
ls -l /dev/vipcore  # 应看到 crw-rw-rw-

# 2. 获取并编译 AI SDK
git clone https://github.com/ZIFENG278/ai-sdk.git
cd ai-sdk/examples/vpm_run
make AI_SDK_PLATFORM=a733 NPU_SW_VERSION=v2.0

# 3. 配置环境变量 (加入 ~/.bashrc)
export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:~/ai-sdk/viplite-tina/lib/aarch64-none-linux-gnu/v2.0/

# 4. Hello World 验证 (LeNet)
cp ../../lenet/model/v3/lenet.nb .
cp ../../lenet/input_data/lenet.dat .
cat > sample_lenet.txt <<EOF
[network]
./lenet.nb
[input]
./lenet.dat
EOF
./vpm_run -s sample_lenet.txt -l 1 --show_top5 1 -b 0

# 5. YOLOv5 推理
cd ../yolov5
make AI_SDK_PLATFORM=a733
make install AI_SDK_PLATFORM=a733 INSTALL_PREFIX=./
cd ./etc/npu/yolov5
./yolov5 ./model/yolov5.nb ./input_data/dog.jpg
```

### PC端模型转换 (ACUITY Toolkit)

```bash
# Docker 环境 (x86 Linux PC)
sudo docker load -i ubuntu-npu_v2.0.10.tar
sudo docker run --ipc=host -itd -v ${PWD}:/workspace --name allwinner_v2.0.10 ubuntu-npu:v2.0.10 /bin/bash

# 在 Docker 内转换 YOLOv5
cd ai-sdk/models && source env.sh v3
# 1) 固定 ONNX 输入维度
onnxsim yolov5s.onnx yolov5s-sim.onnx --overwrite-input-shape 1,3,640,640
# 2) 解析 → IR
./pegasus_import.sh yolov5s-sim/
# 3) 量化 (UINT8, 10张校准图)
./pegasus_quantize.sh yolov5s-sim/ uint8 10
# 4) 编译 → .nb
./pegasus_export_ovx.sh yolov5s-sim/ uint8
# 输出: yolov5s-sim/wksp/yolov5s-sim_uint8_nbg_unify/network_binary.nb
```

### 模型规格

| 模型 | 格式 | 输入 | 推理时间 | 用途 |
|------|------|------|---------|------|
| YOLOv5s | `.nb` (NBG) | 640×640 UINT8 | ~46ms | 3类圆柱体检测 |
| ResNet50 | `.nb` (NBG) | 224×224 UINT8 | 待测 | 分类(可选) |

> Radxa 提供预转换的 `yolov5.nb` 和 `resnet50.nb`，可直接使用。

### 视觉管线适配状态

| 模块 | 状态 | 说明 |
|------|------|------|
| `realtime_vision.py` | **待适配** | 需添加 VIPLite 后端 (backend='viplite') |
| `rknn_detector.py` | **不适用** | RKNN 是瑞芯微方案，Cubie A7Z 不支持 |
| `objDet_yolov5.py` | **已废弃** | ACL 昇腾方案，已不使用 |
| `target_selector.py` | **可复用** | 纯 Python 逻辑，与 NPU 无关 |
| `uart_comm.py` | **可复用** | 需确认串口设备节点 (可能从 /dev/ttyS4 变更) |

## 代码约定

### 嵌入式 (C)
- `Drivers/` 和 `freertos/` 由CubeMX/官方源码提供，**不要手动修改**
- 用户代码写在 `Core/Src/` 和 `Core/Inc/`，CubeMX重新生成时会保留 `USER CODE BEGIN/END` 区块内的代码
- 修改外设配置应通过 `robot.ioc` → CubeMX重新生成，而非直接改HAL初始化代码
- 中断优先级：控制循环(5) > DMA传感器(5-6) > UART通信(7) > 系统节拍(15)

### AI视觉 (VIPLite / Python)
- **禁止安装 RKNN**: Cubie A7Z 使用全志 A733 + VeriSilicon NPU，不是瑞芯微方案
- 板端推理使用 VIPLite C API (ai-sdk)，模型格式为 `.nb`
- 环境变量 `LD_LIBRARY_PATH` 必须包含 `ai-sdk/viplite-tina/lib/aarch64-none-linux-gnu/v2.0/`
- 模型转换在 x86 PC 上通过 ACUITY Toolkit Docker 容器完成 (A733 使用 v2.0.10 镜像)
- `target_selector.py` 和 `uart_comm.py` 为纯 Python 模块，可跨平台复用
- 推理管线适配方案: C 推理程序输出 → Python 解析 → 目标选择 → UART 发送

## 已知问题

- **视觉管线待适配**: `realtime_vision.py` 需添加 VIPLite 后端支持（当前仅支持 ACL/RKNN）
- **UART 端口待确认**: Cubie A7Z 的 GPIO UART 设备节点需在板上实际确认（SSH 执行 `ls /dev/ttyS*`）
- **旧 ACL 代码残留**: `objDet_yolov5.py`、`resnet_classification.py` 等昇腾专用代码已废弃，待清理
- **模型待转换**: 需将 YOLOv5 ONNX 模型通过 ACUITY Toolkit 转换为 `.nb` 格式（或使用 Radxa 预构建模型）
- FreeRTOS任务架构尚未完全实现，部分控制逻辑仍在主循环轮询中
