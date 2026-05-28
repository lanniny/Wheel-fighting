# 集成与联调 SOP (Wave 3 产物)

> 从代码合并到实机验证的端到端步骤。涉及 STM32 烧录、Cubie 部署、视觉训练、NPU 推理。

## 速查

| 我想... | 跳到 |
|---------|------|
| 烧录上游合并后的固件 | [§1](#1-stm32-固件烧录) |
| 推 vision_upload v6 到 Cubie | [§2](#2-vision_upload-部署到-cubie) |
| 跑通颜色检测验证 | [§3](#3-视觉---stm32-联调验证) |
| 训练 YOLOv5 + 部署到 NPU | [§4](#4-yolov5-训练-→-npu-部署) |
| 故障排查 | [§5](#5-故障排查) |

---

## 1. STM32 固件烧录

合并 lane A 之后：

```bash
# 1.1 在本地 Windows / Keil 环境
#     - 打开 MDK-ARM/<工程>.uvprojx
#     - Project → Manage → Project Items, 添加 Core/Src/jy62.c
#     - Rebuild (期望 0 Error)
#     - 用 ST-Link 连接 STM32 + 板上电
#     - F8 / Download

# 1.2 验证启动:
#     - OLED 应显示初始化信息
#     - 串口 (USART2 PD5/PD6, 115200) 应能用 STM32 → Cubie 单字节命令通信
```

实机串口诊断 (先不接 Cubie，用 USB-TTL 替代)：

```bash
# Windows 上用 PuTTY/MobaXterm 连 USB-TTL @ 115200
# STM32 启动后会推送 'b' 或 'y' (蓝/黄队伍) 一个字节
# 用 PC 端 echo 一帧:  $E,320,240,5000,+25*4A    (注意 \n 结尾)
# 看 OLED 是否显示目标坐标
```

JY62 验证：

```c
// 在 main.c 顶部把 JY62_TEST_MODE 改为 1, 重新烧录
// 串口 (USART2) 会以 100ms 周期输出 roll,pitch,yaw,gz
// 转动板子 yaw 应单调变化, 静置时 roll/pitch 接近 0
```

## 2. vision_upload 部署到 Cubie

```bash
cd G:/tasks/机器人创新设计大赛
# 干跑 (不实际推送)
bash tools/deploy/deploy_to_cubie.sh tools/deploy/dummy.nb --vision --dry-run

# 仅同步 vision_upload 目录, 重启服务 (不需要 .nb)
# 注意: deploy_to_cubie.sh 强制要求 .nb, 这里我们绕过, 用 ssh-skill 直接同步:
python ~/.claude/skills/ssh/scripts/ssh_upload.py cubie-radxa \
    "vision_upload/main.py" "/home/radxa/vision_upload/main.py"
python ~/.claude/skills/ssh/scripts/ssh_upload.py cubie-radxa \
    "vision_upload/comm.py" "/home/radxa/vision_upload/comm.py"
python ~/.claude/skills/ssh/scripts/ssh_upload.py cubie-radxa \
    "vision_upload/config.py" "/home/radxa/vision_upload/config.py"
python ~/.claude/skills/ssh/scripts/ssh_upload.py cubie-radxa \
    "vision_upload/detector.py" "/home/radxa/vision_upload/detector.py"

# 重启服务
python ~/.claude/skills/ssh/scripts/ssh_execute.py cubie-radxa \
    "sudo systemctl restart vision && sleep 2 && systemctl is-active vision"

# 看日志
python ~/.claude/skills/ssh/scripts/ssh_execute.py cubie-radxa \
    "tail -30 /tmp/vision_det.log"
```

## 3. 视觉 ↔ STM32 联调验证

### 3.1 MJPEG 流确认

浏览器打开 `http://192.168.225.215:8080`，应看到带检测框的实时视频。

```bash
# 也可以从命令行截一帧:
curl -s -o /tmp/snap.jpg "http://192.168.225.215:8080/stream" -m 1 || true
# 不过 stream 是 multipart 流, curl 拿到的不是单帧 jpg, 用浏览器更直接
```

### 3.2 串口握手 (`b`/`y` → STM32)

```bash
# 在 STM32 主控开机后, 它会发送 'b' 或 'y' 字节
# vision.service 的 main.py 通过 UART 接收并设置 my_color
# 验证:
python ~/.claude/skills/ssh/scripts/ssh_execute.py cubie-radxa \
    "tail -50 /tmp/vision_det.log | grep -E 'UART_CMD|color='"
```

### 3.3 视觉 → STM32 数据帧

```bash
# 在 Cubie 上抓串口 RX 端 (假设 STM32 发命令很少, 主要是视觉发数据)
python ~/.claude/skills/ssh/scripts/ssh_execute.py cubie-radxa \
    "stty -F /dev/ttySTM32 115200 -echo raw && head -c 256 /dev/ttySTM32 | xxd"
# 期望: 看到 $E,xxx,yyy,...,*CS\n 这种字节流, 也可能看到 STM32 echo 回声
```

### 3.4 echo 回声健康

```bash
python ~/.claude/skills/ssh/scripts/ssh_execute.py cubie-radxa \
    "tail -100 /tmp/vision_det.log | grep -E 'echo|ACK|LOST' | tail -10"
# 期望:
#   echo=E lag~30-80ms 健康
#   连续 LOST 表示 STM32 端没回复 (固件问题或 USART2 阻塞)
```

### 3.5 协议 v2 切换 (可选验证)

```bash
# 在 Cubie 设置环境变量启用 v2:
python ~/.claude/skills/ssh/scripts/ssh_execute.py cubie-radxa \
    "sudo systemctl edit vision --full" 
# (这个是交互式, 不能 ssh-skill 跑; 改成在文件里加 Environment=VISION_PROTOCOL_V2=1)

# 或临时手跑:
python ~/.claude/skills/ssh/scripts/ssh_execute.py cubie-radxa \
    "sudo systemctl stop vision && cd /home/radxa/vision_upload && \
     VISION_PROTOCOL_V2=1 python3 main.py --color b --priority-mode attack"
# 拼出来的帧形如: $E,320,240,5000,+25,12345,50,0*5C
# STM32 v1 (未升级) 仍能解析前 5 字段, 多余字段被丢弃
```

## 4. YOLOv5 训练 → NPU 部署

完整流程 (PC 侧):

```bash
cd tools/training
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 4.1 采集 (Cubie vision.service 必须先运行, 否则 MJPEG 无流)
python 01_capture.py --url http://192.168.225.215:8080/stream --count 1500 --interval 0.3

# 4.2 自动标注
python 02_pseudo_label.py --vis dataset/preview
# 浏览 dataset/preview 检查质量, 用 labelImg 修正 dataset/labels_auto/ → dataset/labels/

# 4.3 划分 train/val
python split_dataset.py --val 0.15

# 4.4 训练
python 03_train.py --epochs 100 --batch 16
# 训练完产物: runs/train/robot_yolov5n/weights/best.pt

# 4.5 ONNX 导出
python 04_export_onnx.py --weights runs/train/robot_yolov5n/weights/best.pt
# 产物: runs/train/robot_yolov5n/weights/best.onnx

# 4.6 ACUITY 转 .nb (Linux x86 + Docker)
cd ../deploy
mkdir -p quant_dataset
cp ../training/dataset/images/val/*.jpg quant_dataset/   # 校准集
bash convert_acuity.sh ../training/runs/train/robot_yolov5n/weights/best.onnx
# 产物: best.nb

# 4.7 部署到 Cubie
bash deploy_to_cubie.sh best.nb --vision --restart

# 4.8 启用 NPU 后端
python ~/.claude/skills/ssh/scripts/ssh_execute.py cubie-radxa \
    "sudo systemctl edit vision --full"
# 在 [Service] 段加:
#   Environment=VISION_BACKEND=npu
#   Environment=LD_LIBRARY_PATH=/home/radxa/ai-sdk/viplite-tina/lib/aarch64-none-linux-gnu/v2.0
# 保存退出, 服务自动重启
```

## 5. 故障排查

### vision.service 启动失败

```bash
python ~/.claude/skills/ssh/scripts/ssh_execute.py cubie-radxa \
    "sudo systemctl status vision -l --no-pager | head -50; \
     journalctl -u vision -n 50 --no-pager"
```

### 摄像头打不开

```bash
python ~/.claude/skills/ssh/scripts/ssh_execute.py cubie-radxa \
    "ls /dev/video* && fuser -v /dev/video0 2>&1"
# 多人争用 → kill 占用进程
```

### UART 不通

```bash
python ~/.claude/skills/ssh/scripts/ssh_execute.py cubie-radxa \
    "ls -la /dev/ttySTM32 /dev/ttyUSB* && dmesg | tail -20"
# /dev/ttySTM32 应是 symlink → /dev/ttyUSB0
# 没有 USB 设备号 = STM32 USB 转 TTL 桥未连或未识别
```

### NPU 推理失败

```bash
python ~/.claude/skills/ssh/scripts/ssh_execute.py cubie-radxa \
    "ls -la /dev/vipcore && \
     LD_LIBRARY_PATH=/home/radxa/ai-sdk/viplite-tina/lib/aarch64-none-linux-gnu/v2.0 \
     /home/radxa/ai-sdk/examples/yolov5/yolov5 /home/radxa/models/best.nb /tmp/test.jpg 2>&1"
# 错误对照:
#   vipUnLockNbgFile failed → .nb 与 SDK 版本不匹配
#   vipCreateNetwork failed → 内存/权限
#   no detection → conf threshold 太高或模型欠拟合
```

### STM32 端 RX 错误计数高

```bash
# 在 robot_fight.c / OLED 里加调试输出:
#   uint32_t total, success, cserr;
#   Vision_GetStats(&total, &success, &cserr);
#   每 1s 显示
# cserr 高 → UART 干扰或 vision_upload 校验和算错
# total 不增 → DMA 卡死, HAL_UART_ErrorCallback 应该兜底 (lane E 已加)
```

## 6. 验收清单 (Wave 3 出口)

- [ ] **代码层**: `Core/` 已合并上游, vision_upload v6 patches 已应用, tools/training tools/deploy 脚本已就位
- [ ] **构建层**: STM32 固件 Keil rebuild 0 error；vision_upload Python `python -c "import main, comm, detector, config"` 0 error
- [ ] **部署层**: 板端 vision.service active 且 main.py 标 v6
- [ ] **集成层**: MJPEG 流可访问 (浏览器/curl)；STM32 → Cubie 单字节命令链路通；视觉 → STM32 帧链路通 (echo healthy)
- [ ] **训练层** (可选): 至少跑通一次端到端：1500 张采集 → 训 50 epoch → ONNX → .nb → 部署 → vision_upload --backend=npu 跑出第一帧检测
- [ ] **回退路径**: 任一层失败可回到上一可用版本 (git checkout, deployment script 重推 v5 备份)
