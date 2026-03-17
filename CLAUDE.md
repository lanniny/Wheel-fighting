# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

2025中国高校智能机器人创意大赛——轮式机器人格斗A。三合一仓库：**嵌入式控制固件**（STM32F407 + FreeRTOS）、**AI视觉感知**（OrangePi AIpro + 昇腾NPU）、**机械设计**（SolidWorks + 制造文件）。

**竞赛约束**: 机器人≤4KG，出发区≤30×30cm，2分钟/场，擂台2.4m×2.4m×6cm高，AprilTag能量块计分。

## 系统架构

```
┌─────────────────────────────────────────┐
│   OrangePi AIpro (上位机/视觉处理)       │
│   YOLOv5目标检测 + ResNet50分类          │
│   昇腾310B1 NPU, Python 3.8+           │
└──────────────┬──────────────────────────┘
               │ UART 115200bps (USART2: PD5-TX, PD6-RX)
┌──────────────┴──────────────────────────┐
│   STM32F407VET6 (下位机/实时控制)        │
│   168MHz, 512KB Flash, FreeRTOS         │
│   电机PID闭环 + 传感器融合 + 状态机      │
└─────────────────────────────────────────┘
```

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
│   └── models/om_models/   # 昇腾OM模型文件
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
| USART2 + DMA1 | 与OrangePi通信 | PD5(TX), PD6(RX), 115200bps |
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

运行平台：**OrangePi AIpro + 华为昇腾310B1 NPU**。使用昇腾ACL进行推理，不走标准PyTorch路径。

### 模块依赖

```
scripts/inference/yolov5_detection.py  →  YOLOv5Detector(高层API)
    └── vision_perception/yolo_detection/objDet_yolov5.py  →  Model(ACL基类) / YoloV5
        ├── acl (昇腾SDK)
        └── det_utils.py (letterbox, NMS via torchvision, 坐标还原, bbox绘制)

scripts/inference/resnet_classification.py  →  ResNet50Classifier(独立ACL调用)
    ├── acl
    └── vision_perception/resnet_classification/label.py (ImageNet 1000类映射)
```

### 运行命令

```bash
# 昇腾环境初始化（OrangePi上执行）
source /usr/local/Ascend/ascend-toolkit/set_env.sh

# YOLOv5目标检测
cd RobotCombatAI/scripts/inference
python3 yolov5_detection.py --image ../../data/test_images/xxx.jpg --save

# ResNet50分类
python3 resnet_classification.py --image ../../data/test_images/xxx.jpg

# Python依赖
pip install numpy opencv-python torch torchvision
```

### 模型规格

| 模型 | 文件 | 输入 | 推理时间 | 用途 |
|------|------|------|---------|------|
| YOLOv5s | `models/om_models/yolov5s_cylinder_cls3_*.om` | 640×640 | ~15ms | 3类圆柱体检测 |
| ResNet50 | `models/om_models/resnet50.om` | 224×224 | ~10ms | ImageNet 1000类分类 |

## 代码约定

### 嵌入式 (C)
- `Drivers/` 和 `freertos/` 由CubeMX/官方源码提供，**不要手动修改**
- 用户代码写在 `Core/Src/` 和 `Core/Inc/`，CubeMX重新生成时会保留 `USER CODE BEGIN/END` 区块内的代码
- 修改外设配置应通过 `robot.ioc` → CubeMX重新生成，而非直接改HAL初始化代码
- 中断优先级：控制循环(5) > DMA传感器(5-6) > UART通信(7) > 系统节拍(15)

### AI视觉 (Python)
- ACL资源必须严格配对：`init_acl`↔`deinit_acl`，`Model.__init__`↔`Model.release`
- 模型输入要求 `np.ascontiguousarray`，数据类型须匹配模型规格（float32/float16）
- `det_utils.py` 的NMS依赖 `torchvision.ops.nms`，本地开发需安装PyTorch+torchvision
- 环境变量 `ASCEND_TOOLKIT_HOME` 和 `ASCEND_OPP_PATH` 必须在运行前设置

## 已知问题

- `RobotCombatAI/scripts/inference/resnet_classification.py:201` — 字典字面量缺少逗号（`'all_scores'` 与 `'preprocess_time'` 之间），SyntaxError
- ResNet50分类器未复用 `objDet_yolov5.py` 的 `Model` 基类，ACL调用逻辑重复
- FreeRTOS任务架构尚未完全实现，部分控制逻辑仍在主循环轮询中
