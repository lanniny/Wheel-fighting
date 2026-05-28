# Cubie A7Z 环境快照 (2026-05-06)

> Wave 1.3 SSH 探活产物。SSH 别名 `cubie-radxa` (192.168.225.215, user `radxa`)。后续脚本与文档应以本快照为基准。

## 系统层

| 项 | 值 |
|----|----|
| Kernel | `Linux radxa-cubie-a7z 5.15.147-11-a733 SMP PREEMPT aarch64` (Tina) |
| 体系结构 | aarch64 |
| 内存 | 7.7 GiB total / 6.2 GiB free |
| Swap | 3.9 GiB (未用) |
| CPU | 8 核 (A733: 2×A76 + 6×A55) |
| 磁盘 | `/dev/sda3` 233 GB / 已用 6.3 GB / 可用 217 GB |

## NPU 与 AI SDK

| 项 | 值 |
|----|----|
| NPU 设备节点 | `/dev/vipcore` ✓ (crw-rw-rw-) |
| ai-sdk 路径 | `/home/radxa/ai-sdk` (含 `viplite-tina/`, `examples/`, `machinfo/`, `scripts/`, `log/`) |
| VIPLite 库 (生产) | `/home/radxa/ai-sdk/viplite-tina/lib/aarch64-none-linux-gnu/v2.0/` ← **必用此版本** (匹配 A733 v2.0.10 ACUITY) |
| VIPLite 库 (兼容) | 还存在 `glibc-gcc13_2_0/v2.0/`、`v1.13/`、`glibc-gcc11_3_0/v1.13/` 等多版本，**勿误用** |
| 关键 .so | `libNBGlinker.so`, `libVIPhal.so`, `libVIPlite.so`, `libVIPuser.so` |
| 内置示例 | `~/ai-sdk/examples/` 含 `libawnn_viplite/`、`libawutils/`、**`yolov5/`** ← 直接复用做基线对照 |
| 模型存放目录 | **不存在** `~/models/` ← 部署时需新建 |

## Python 与依赖

| 包 | 版本 | 备注 |
|----|------|------|
| Python | 3.9.2 | 不必升级，能跑 |
| opencv-python (cv2) | 4.5.1 | 满足检测；如需 DNN/ONNX runtime 推理需额外装 |
| numpy | 1.19.5 | 旧但稳定 |
| pyserial | 3.5b0 | OK |
| dt-apriltags | 3.1.7 | ✓ 已就绪，给 `--tag-backend=apriltag` 用 |
| **torch** | **缺** | 训练只能在 PC 上做 |
| **onnxruntime** | **缺** | NPU 失败时无法 CPU 兜底；视情况补装 |
| **ultralytics** | **缺** | 同上 |

## 视觉部署现状 (重要漂移!)

| 项 | 值 |
|----|----|
| 部署目录 | `/home/radxa/vision_upload/` |
| **部署版本** | **v5** (`main.py` 文件头标注 "视觉主程序 v5") |
| **本地仓库版本** | **v6** (含 echo 确认 / 掉台迟滞 / 性能分析 / Watchdog) |
| systemd unit | `/etc/systemd/system/vision.service` (enabled, vendor preset: enabled) |
| 服务状态 | **active (running)**，自 2026-04-14 15:43:53 CST 起运行 3 周 1 天 |
| 启动命令 | `python3 main.py --color b --priority-mode attack` |
| 主进程 | PID 447，内存 268.1 M，CPU 累计 13min25s |
| 部署目录额外文件 | `cam_test.py`, `diag_hsv.py`, `uart_capture.py`, `uart_debug.py`, `uart_test.py`, `vision.service.new` (本地无) |
| `__pycache__/` | 存在，部署有跑过 |

> ⚠️ **DEPLOYMENT DRIFT**：本地 v6 ≫ 板端 v5。Wave 2 lane B 完成后必须重新部署。部署目录里那几个调试脚本 (`cam_test.py`, `diag_hsv.py`, `uart_*.py`) 在本地 git 里没有；不要在 sync 时盲目删除 — 先备份。

## I/O 设备

| 设备 | 状态 |
|------|------|
| `/dev/video0`, `/dev/video1` | 存在 (双相机) |
| `/dev/ttySTM32` | symlink → `/dev/ttyUSB0` (UART 桥接已就位) |
| `v4l2-ctl` | **未安装** (zsh: command not found) — 后续如需查相机能力，先 `apt install v4l-utils` |

## 已知操作约束

1. SSH 通过 skill 别名 `cubie-radxa` 访问；`~/.ssh/config` 中已配 `password: radxa`。
2. 部署目录写权限属 `radxa`；`vision.service` 由 root 拉起 (`/etc/systemd/system/`)，重启需 `sudo systemctl restart vision`。
3. NPU 测试时 `LD_LIBRARY_PATH` 必须显式添加：
   ```bash
   export LD_LIBRARY_PATH=/home/radxa/ai-sdk/viplite-tina/lib/aarch64-none-linux-gnu/v2.0:$LD_LIBRARY_PATH
   ```
4. 由于板端无 PyTorch / ACUITY，**模型转换必须在 PC 端 Docker 容器**做完后再 scp `.nb` 上来。
