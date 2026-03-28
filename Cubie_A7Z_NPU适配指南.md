# Radxa Cubie A7Z NPU 适配指南

> 从 OrangePi AIpro (昇腾310B1) 迁移到 Radxa Cubie A7Z (全志A733 + VeriSilicon VIP9000)

## 一、硬件平台概述

### 1.1 核心规格

| 项目 | 规格 |
|------|------|
| 开发板 | Radxa Cubie A7Z (65×30mm) |
| SoC | 全志 Allwinner A733 |
| CPU | 2× Cortex-A76 + 6× Cortex-A55 (最高 2.0GHz) |
| NPU | VeriSilicon VIP9000 (3 TOPS@INT8) |
| GPU | Imagination BXM-4-64 MC1 |
| MCU | RISC-V E902 (200MHz) 协处理器 |
| 内存 | LPDDR4/4x (1~16GB) |
| 存储 | UFS 3.0 (板载) + MicroSD |
| 连接 | WiFi 6 + Bluetooth 5.4 |
| 摄像头 | 4-lane MIPI CSI (可拆分 2×2-lane) |
| 扩展 | 40-pin GPIO + PCIe 3.0 x1 |
| 视频输出 | Micro HDMI (4K@60fps) |
| OS | Debian / Buildroot / Android 13 |

### 1.2 与旧平台对比

| 项目 | OrangePi AIpro (旧) | Cubie A7Z (新) | 影响 |
|------|---------------------|----------------|------|
| NPU 算力 | 8 TOPS | 3 TOPS | 推理帧率可能下降 |
| NPU 类型 | 华为昇腾 | VeriSilicon VIP9000 | API 完全不同 |
| 模型格式 | `.om` | `.nb` (NBG) | 需要重新转换模型 |
| 推理 API | ACL (Python) | VIPLite (C) | 需要重写推理代码 |
| 板子尺寸 | 85×56mm | 65×30mm | 更紧凑，利于装配 |

---

## 二、板端环境确认

### 2.1 SSH 连接

```bash
ssh radxa@192.168.31.162
# ��码: radxa
```

### 2.2 NPU 驱动检查

```bash
# 确认 NPU 驱动节点存在
ls -l /dev/vipcore
# 预期输出: crw-rw-rw- 1 root root ... /dev/vipcore

# 如果不存在，检查内核模块
lsmod | grep vip
dmesg | grep -i npu
```

> **警告**: 如果 `/dev/vipcore` 不存在，说明系统镜像未包含 NPU 驱动，需更换官方镜像。

### 2.3 确认 UART 串口

```bash
# 列出所有可用串口设备
ls /dev/ttyS*

# 检查 GPIO UART 对应的设备节点
# Cubie A7Z 40-pin 可用 UART:
#   UART0: Pin7(TX PB0), Pin11(RX PB1)
#   UART3: Pin5(TX PJ22), Pin3(RX PJ23)
# 实际设备名取决于 dtb 配置
```

### 2.4 确认摄像头

```bash
# 列出视频设备
ls /dev/video*

# 测试 USB 摄像头
v4l2-ctl --list-devices
```

---

## 三、AI SDK 编译 (板端)

### 3.1 获取 SDK

```bash
cd ~
git clone https://github.com/ZIFENG278/ai-sdk.git
# 备选: git clone https://github.com/radxa-edge/ai-sdk.git
```

### 3.2 编译 vpm_run (通用模型测试工具)

```bash
cd ~/ai-sdk/examples/vpm_run
make AI_SDK_PLATFORM=a733 NPU_SW_VERSION=v2.0
ls -l vpm_run  # 确认生成可执行文件
```

### 3.3 配置环境变量

```bash
# 添加到 ~/.bashrc
echo 'export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:~/ai-sdk/viplite-tina/lib/aarch64-none-linux-gnu/v2.0/' >> ~/.bashrc
source ~/.bashrc
```

### 3.4 Hello World 验证 (LeNet 手写数字识别)

```bash
cd ~/ai-sdk/examples/vpm_run

# 准备模型和数据
cp ../../lenet/model/v3/lenet.nb .
cp ../../lenet/input_data/lenet.dat .

# 创建配置文件
cat > sample_lenet.txt <<EOF
[network]
./lenet.nb
[input]
./lenet.dat
EOF

# 执行推理
./vpm_run -s sample_lenet.txt -l 1 --show_top5 1 -b 0
```

**预期输出**:
```
init vip lite, driver version=...
vip lite init OK.
run time for this network 0: 222 us.
******* nb TOP5 ********
 --- Top5 ---
  0: 1.000000    <-- 识别成功！
```

### 3.5 编译 YOLOv5 推理程序

```bash
cd ~/ai-sdk/examples/yolov5
make AI_SDK_PLATFORM=a733
make install AI_SDK_PLATFORM=a733 INSTALL_PREFIX=./
```

### 3.6 运行 YOLOv5 推理

```bash
cd ./etc/npu/yolov5
./yolov5 ./model/yolov5.nb ./input_data/dog.jpg
```

**预期输出**:
```
detection num: 3
dog: 86% [x1, y1, x2, y2]
...
Total processing time: ~45.75ms
```

---

## 四、PC 端模型转换 (ACUITY Toolkit)

### 4.1 环境准备

**要求**: x86 Linux PC (或 Windows + WSL2) + Docker

```bash
# 安装 Docker (Ubuntu)
sudo apt-get update
sudo apt-get install docker-ce docker-ce-cli containerd.io
```

### 4.2 加载 ACUITY Docker 镜像

```bash
# 下载 A733 版本 Docker 镜像 (从 Radxa/全志资源站)
unzip docker_images_v2.0.x.zip
cd docker_images_v2.0.x
unzip ubuntu-npu_v2.0.10.tar.zip
sudo docker load -i ubuntu-npu_v2.0.10.tar
```

### 4.3 创建 Docker 容器

```bash
mkdir -p ~/docker_data && cd ~/docker_data
sudo docker run --ipc=host -itd \
  -v ${PWD}:/workspace \
  --name allwinner_v2.0.10 \
  ubuntu-npu:v2.0.10 /bin/bash

# 进入容器
sudo docker exec -it allwinner_v2.0.10 /bin/bash
```

### 4.4 YOLOv5 模型转换步骤

在 Docker 容器内执行:

```bash
# 1. 获取 SDK 和脚本
git clone https://github.com/ZIFENG278/ai-sdk.git
cd ai-sdk/models
source env.sh v3   # A733 使用 v3
cp ../scripts/* .

# 2. 准备模型
mkdir yolov5s-sim && cd yolov5s-sim
wget https://github.com/ultralytics/yolov5/releases/download/v6.0/yolov5s.onnx

# 3. 固定输入维度
pip3 install onnxsim onnxruntime
onnxsim yolov5s.onnx yolov5s-sim.onnx --overwrite-input-shape 1,3,640,640

# 4. 准备量化校准数据集
# 创建 dataset.txt，每行一个校准图片路径
ls /path/to/calibration_images/*.jpg > dataset.txt

# 5. 创建 I/O 配置
cat > inputs_outputs.txt <<EOF
--inputs images --input-size-list '3,640,640' --outputs '350 498 646'
EOF

# 6. 返回上级目录执行转换
cd ..

# 7. 解析模型 → IR 格式
./pegasus_import.sh yolov5s-sim/
# 生成: yolov5s-sim.json (结构) + yolov5s-sim.data (权重)

# 8. 配置预处理参数
# 修改 yolov5s-sim_inputmeta.yml:
#   scale: 0.00392157  (即 1/255)
#   reverse_channel: true  (BGR→RGB)

# 9. 量化 (UINT8, 10 张校准图)
./pegasus_quantize.sh yolov5s-sim/ uint8 10

# 10. 编译为 NBG
./pegasus_export_ovx.sh yolov5s-sim/ uint8
```

**输出文件**: `yolov5s-sim/wksp/yolov5s-sim_uint8_nbg_unify/network_binary.nb`

将此 `.nb` 文件拷贝到板子上即可使用。

### 4.5 ResNet50 模型转换

```bash
cd ai-sdk/models
mkdir resnet50-sim && cd resnet50-sim
wget https://github.com/onnx/models/raw/main/validated/vision/classification/resnet/model/resnet50-v2-7.onnx
onnxsim resnet50-v2-7.onnx resnet50-sim.onnx --overwrite-input-shape 1,3,224,224
cd ..

# 解析
./pegasus_import.sh resnet50-sim/

# 配置预处理 (ImageNet 归一化)
# 修改 resnet50-sim_inputmeta.yml:
#   mean: [123.675, 116.28, 103.53]
#   scale: [0.01712, 0.01751, 0.01743]

# 量化与编译
./pegasus_quantize.sh resnet50-sim/ uint8 10
./pegasus_export_ovx.sh resnet50-sim/ uint8
```

### 4.6 使用预构建模型 (快捷方式)

Radxa 在 ai-sdk 仓库中提供预转换模型:
- `ai-sdk/examples/yolov5/etc/npu/yolov5/model/yolov5.nb`
- `ai-sdk/examples/resnet50/etc/npu/resnet50/model/resnet50.nb`

> 注意: 预构建的 YOLOv5 是 COCO 80 类，我们的竞赛模型是自训练的 3 类圆柱体检测，需自行转换。

---

## 五、视觉管线适配方案

### 5.1 现有架构

```
Python: camera(cv2) → ACL/RKNN推理 → TargetSelector → UART发送
```

### 5.2 适配方案对比

| 方案 | 描述 | 优点 | 缺点 | 推荐度 |
|------|------|------|------|--------|
| A: C推理 + Python编排 | Python 调用 C 推理程序 | 最小改动，保持现有框架 | 进程间通信开销 | ★★★★ |
| B: 纯 C 管线 | 全部用 C 重写 | 最低延迟 | 完全重写，调试困难 | ★★ |
| C: Python ctypes | Python 通过 ctypes 调 .so | 兼容度最高 | 需编写 C wrapper | ★★★ |

### 5.3 推荐方案 A 实现思路

```
realtime_vision.py (Python 主控)
    ├── cv2.VideoCapture() → 采集帧 → 保存临时图片
    ├── subprocess.run(['./yolov5_infer', 'model.nb', 'temp.jpg']) → 获取检测结果
    ├── 解析 stdout 输出 → [x1,y1,x2,y2,conf,cls] 列表
    ├── TargetSelector.select(detections) → 最优目标
    └── UARTComm.send_target() → 发送到 STM32
```

**关键改动**:
1. `realtime_vision.py`: 新增 `backend='viplite'` 选项
2. 编写 C 推理 wrapper 程序 (基于 ai-sdk/examples/yolov5 修改)
3. `uart_comm.py`: 修改默认串口设备节点
4. `target_selector.py`: 无需修改

### 5.4 方案 C 替代实现 (ctypes)

如果 subprocess 延迟不可接受，可编写共享库:

```c
// viplite_yolov5.c → 编译为 libviplite_yolov5.so
int detect(const char* model_path, const unsigned char* img_data,
           int width, int height, float* results, int max_results);
```

```python
# Python 端
import ctypes
lib = ctypes.CDLL('./libviplite_yolov5.so')
# 调用 detect 函数
```

---

## 六、常见问题与排错

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| `/dev/vipcore` 不存在 | 系统镜像未包含 NPU 驱动 | 刷写官方 Debian 镜像 |
| `error while loading shared libraries` | 缺少 VIPLite 库 | 检查 `LD_LIBRARY_PATH` 配置 |
| `vpm_run: Is a directory` | 未编译，直接运行了目录 | 进入目录执行 `make` 编译 |
| 想安装 `python3-rknnlite2` | 方向错误！ | Cubie A7Z 不是瑞芯微，禁止安装 RKNN |
| Docker 拉取镜像失败 | 镜像需离线加载 | 下载 .tar 包，使用 `docker load -i` |
| wget 下载 404 | 预编译包链接失效 | 使用源码编译 (本文方式) |
| 推理结果全零 | 模型与平台不匹配 | 确认使用 v3 版本 NBG 模型 |
| UART 不通 | 设备节点错误 | 在板上确认 `ls /dev/ttyS*` 并测试 |

---

## 七、参考资料

- [Radxa Cubie A7Z 官方文档](https://docs.radxa.com/en/cubie/a7z)
- [NPU 开发指南](https://docs.radxa.com/en/cubie/a7z/app-dev/npu-dev)
- [AI-SDK 仓库](https://github.com/ZIFENG278/ai-sdk.git)
- [Cubie A7Z NPU 实战指南 (社区)](https://github.com/jackhe183/radxa-dev)
- [ACUITY Toolkit 使用示例](https://docs.radxa.com/en/cubie/a7a/app-dev/npu-dev/cubie_acuity_usage)
- [acuitylite PyPI 包](https://pypi.org/project/acuitylite/)
