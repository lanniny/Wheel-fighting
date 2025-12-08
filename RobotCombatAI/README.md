# 🤖 创意机器人轮式格斗竞赛 - AI视觉感知系统

## 📋 项目概述

基于OrangePi AIpro + 华为昇腾310B1 NPU的机器人格斗AI视觉感知系统，支持目标检测、分类和精确定位。

## 🏗️ 项目结构

```
RobotCombatAI/
├── vision_perception/          # 视觉感知核心模块
│   ├── yolo_detection/        # YOLOv5目标检测
│   ├── resnet_classification/ # ResNet50图像分类
│   └── acl_utils/            # 昇腾ACL工具库
├── models/                    # 模型存储
│   ├── onnx_models/          # ONNX格式模型
│   ├── om_models/            # 昇腾OM格式模型
│   └── model_backups/        # 模型备份
├── data/                      # 数据管理
│   ├── test_images/          # 测试图片
│   ├── training_data/        # 训练数据
│   └── annotations/          # 标注文件
├── scripts/                   # 脚本工具
│   ├── setup/                # 环境配置脚本
│   ├── inference/            # 推理脚本
│   ├── evaluation/           # 评估脚本
│   └── training/             # 训练脚本
├── utils/                     # 工具函数
│   ├── coordinate_utils/     # 坐标转换工具
│   ├── image_utils/          # 图像处理工具
│   └── performance_utils/     # 性能监控工具
├── config/                    # 配置文件
│   ├── model_configs/        # 模型配置
│   └── system_configs/       # 系统配置
└── docs/                      # 项目文档
```

## 🎯 核心功能

### 1. YOLOv5目标检测
- **模型**: yolov5s_cylinder_cls3_20230822_640640_f32.om
- **功能**: 检测3类圆柱体目标，输出精确坐标
- **精度**: 97%+置信度检测
- **坐标**: 输出(x1,y1,x2,y2)边界框

### 2. ResNet50图像分类
- **模型**: resnet50.om
- **功能**: 1000类图像识别
- **输入尺寸**: 224×224
- **输出**: 类别标签和置信度

### 3. 坐标定位系统
- **像素级精度**: 精确到像素的目标定位
- **多目标支持**: 可同时检测多个目标
- **实时处理**: NPU加速推理

## 🚀 快速开始

### 环境要求
- OrangePi AIpro (20T)
- 华为昇腾310B1 NPU
- Python 3.8+
- OpenCV 4.9+
- PyTorch 2.1+

### 安装依赖
```bash
# 设置昇腾环境
. /usr/local/Ascend/ascend-toolkit/set_env.sh

# 安装Python依赖
pip install numpy opencv-python torch
```

### 运行YOLOv5检测
```bash
cd scripts/inference
python3 yolov5_detection.py
```

### 运行ResNet50分类
```bash
cd scripts/inference
python3 resnet_classification.py
```

## 📊 性能指标

| 模型 | 推理时间 | 精度 | 适用场景 |
|------|----------|------|----------|
| YOLOv5 | ~15ms | 97% | 目标定位 |
| ResNet50 | ~10ms | 90%+ | 图像分类 |

## 🎮 应用场景

1. **自主导航**: 基于视觉的路径规划
2. **目标追踪**: 实时跟踪敌人和能量块
3. **精确打击**: 像素级目标定位
4. **态势感知**: 多目标同时检测

## ⚙️ 配置说明

### 模型配置
```python
# YOLOv5配置
YOLOV5_CONFIG = {
    'model_path': '../models/om_models/yolov5s_cylinder_cls3_20230822_640640_f32.om',
    'input_size': (640, 640),
    'conf_threshold': 0.25,
    'iou_threshold': 0.45
}
```

### 系统配置
```python
# 昇腾环境配置
ASCEND_CONFIG = {
    'device_id': 0,
    'toolkit_home': '/usr/local/Ascend/ascend-toolkit/8.0.0',
    'opp_path': '/usr/local/Ascend/ascend-toolkit/8.0.0/opp'
}
```

## 🔧 开发指南

### 添加新模型
1. 将ONNX模型放入`models/onnx_models/`
2. 使用ATC转换工具转换为OM格式
3. 更新配置文件
4. 编写推理脚本

### 数据标注
1. 将图片放入`data/training_data/`
2. 使用标注工具创建标注文件
3. 将标注文件放入`data/annotations/`

## 📝 更新日志

### v1.0.0 (2025-12-08)
- ✅ YOLOv5目标检测系统集成
- ✅ ResNet50图像分类集成
- ✅ 坐标输出系统实现
- ✅ 项目结构搭建完成

## 👥 贡献者

- 开发团队：OrangePi AIpro + 华为昇腾联合研发

## 📞 技术支持

- 华为昇腾开发者社区
- OrangePi技术支持

---

**🎯 让AI为机器人格斗注入智能力量！**