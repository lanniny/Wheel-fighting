#!/bin/bash
# -*- coding: utf-8 -*-
"""
环境配置脚本
用于设置华为昇腾开发环境

作者: RobotCombatAI团队
日期: 2025-12-08
"""

echo "🔧 配置RobotCombatAI开发环境..."

# 设置环境变量
export ASCEND_TOOLKIT_HOME=/usr/local/Ascend/ascend-toolkit/8.0.0
export ASCEND_OPP_PATH=/usr/local/Ascend/ascend-toolkit/8.0.0/opp
export PYTHONPATH=/usr/local/Ascend/ascend-toolkit/8.0.0/python/site-packages/acl:$PYTHONPATH
export LD_LIBRARY_PATH=/usr/local/Ascend/ascend-toolkit/8.0.0/lib64:/usr/local/Ascend/ascend-toolkit/8.0.0/aarch64-linux/lib64:$LD_LIBRARY_PATH
export PATH=/usr/local/Ascend/ascend-toolkit/8.0.0/aarch64-linux/bin:$PATH

echo "✅ 昇腾环境变量设置完成"

# 检查NPU状态
echo "📊 检查NPU状态..."
npu-smi info

# 安装Python依赖
echo "📦 检查Python依赖..."

# 检查numpy
if python3 -c "import numpy; print('numpy:', numpy.__version__)" 2>/dev/null; then
    echo "✅ numpy 已安装"
else
    echo "❌ numpy 未安装，正在安装..."
    pip3 install numpy
fi

# 检查opencv
if python3 -c "import cv2; print('opencv:', cv2.__version__)" 2>/dev/null; then
    echo "✅ opencv 已安装"
else
    echo "❌ opencv 未安装，正在安装..."
    pip3 install opencv-python
fi

# 检查torch
if python3 -c "import torch; print('torch:', torch.__version__)" 2>/dev/null; then
    echo "✅ torch 已安装"
else
    echo "❌ torch 未安装，正在安装..."
    pip3 install torch torchvision
fi

echo "🎯 环境配置完成！"
echo ""
echo "📋 快速开始："
echo "cd /home/HwHiAiUser/RobotCombatAI"
echo "python3 scripts/inference/yolov5_detection.py --help"
echo "python3 scripts/inference/resnet_classification.py --help"