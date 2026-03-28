#!/bin/bash
# Cubie A7Z 视觉系统一键部署脚本
# 在板卡上执行: bash deploy_cubie_a7z.sh
set -e

echo "=== Radxa Cubie A7Z Vision System Deployment ==="

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }

# 1. 检查 NPU 驱动
echo ""
echo "--- Step 1: NPU Driver Check ---"
if [ -e /dev/vipcore ]; then
    ok "NPU driver found: /dev/vipcore"
else
    fail "NPU driver not found! Please use official Radxa Debian image."
fi

# 2. 安装系统依赖
echo ""
echo "--- Step 2: Install Dependencies ---"
sudo apt update -qq
sudo apt install -y python3-pip python3-opencv python3-serial git make gcc
ok "System dependencies installed"

# 3. 克隆/更新 ai-sdk
echo ""
echo "--- Step 3: AI SDK Setup ---"
AI_SDK_DIR="$HOME/ai-sdk"
if [ -d "$AI_SDK_DIR" ]; then
    echo "ai-sdk exists, updating..."
    cd "$AI_SDK_DIR" && git pull || true
else
    echo "Cloning ai-sdk..."
    git clone https://github.com/ZIFENG278/ai-sdk.git "$AI_SDK_DIR"
fi

# 4. 编译 yolov5 推理程序
echo ""
echo "--- Step 4: Compile YOLOv5 Inference ---"
cd "$AI_SDK_DIR/examples/yolov5"
make clean 2>/dev/null || true
make AI_SDK_PLATFORM=a733
make install AI_SDK_PLATFORM=a733 INSTALL_PREFIX=./
if [ -f "./etc/npu/yolov5/yolov5" ]; then
    ok "YOLOv5 inference binary compiled"
else
    fail "Compilation failed!"
fi

# 5. 配置环境变量
echo ""
echo "--- Step 5: Environment Variables ---"
VIPLITE_LIB="$AI_SDK_DIR/viplite-tina/lib/aarch64-none-linux-gnu/v2.0/"
if ! grep -q "viplite-tina" "$HOME/.bashrc" 2>/dev/null; then
    echo "export LD_LIBRARY_PATH=\$LD_LIBRARY_PATH:$VIPLITE_LIB" >> "$HOME/.bashrc"
    ok "LD_LIBRARY_PATH added to ~/.bashrc"
else
    ok "LD_LIBRARY_PATH already configured"
fi
export LD_LIBRARY_PATH="$LD_LIBRARY_PATH:$VIPLITE_LIB"

# 6. 验证 NPU 推理
echo ""
echo "--- Step 6: NPU Inference Test ---"
cd "$AI_SDK_DIR/examples/yolov5/etc/npu/yolov5"
if ./yolov5 ./model/yolov5.nb ./input_data/dog.jpg 2>&1 | grep -q "detection num"; then
    ok "NPU inference works!"
else
    warn "NPU inference test did not produce expected output"
fi

# 7. 检查摄像头
echo ""
echo "--- Step 7: Camera Check ---"
if ls /dev/video* >/dev/null 2>&1; then
    ok "Camera device found: $(ls /dev/video* | head -1)"
else
    warn "No camera device found (connect USB camera)"
fi

# 8. 检查 UART 端口
echo ""
echo "--- Step 8: UART Check ---"
echo "Available serial ports:"
ls /dev/ttyS* 2>/dev/null || echo "  (none found)"

# 9. 安装 systemd 服务
echo ""
echo "--- Step 9: Systemd Service ---"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_FILE="$SCRIPT_DIR/../vision_service.service"
if [ -f "$SERVICE_FILE" ]; then
    sudo cp "$SERVICE_FILE" /etc/systemd/system/vision_service.service
    sudo systemctl daemon-reload
    ok "Service installed (use 'sudo systemctl enable --now vision_service' to start)"
else
    warn "Service file not found: $SERVICE_FILE"
fi

echo ""
echo "=== Deployment Complete ==="
echo ""
echo "Next steps:"
echo "  1. Verify UART port from Step 8 output"
echo "  2. Update /etc/systemd/system/vision_service.service if needed"
echo "  3. Run manually: python3 ~/RobotCombatAI/scripts/inference/realtime_vision.py --backend viplite"
echo "  4. Enable auto-start: sudo systemctl enable --now vision_service"
