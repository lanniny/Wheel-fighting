# Lane A 合并总结 (上游 wxinxiang8-ai/robot-fighting → 本地 Core/)

## 已落地的改动

### 1. 文件覆盖 (Core/Inc/, Core/Src/)

整个 `Core/Inc/*.h` 和 `Core/Src/*.c` 已用上游版本覆盖 (除 `Core/CLAUDE.md` 文档保留)。

| 文件 | 状态 |
|------|------|
| `Core/Inc/jy62.h` | **新增** (上游引入) |
| `Core/Src/jy62.c` | **新增** (上游引入) |
| `Core/Inc/usart.h` | 更新 (新增 USART3 init 声明) |
| `Core/Src/usart.c` | 更新 (新增 MX_USART3_UART_Init + MspInit, PB10/PB11 切到 USART3 AF7) |
| `Core/Src/main.c` | 更新 (244→553 行: BACKUP_TEST/JY62_TEST/PID_DEBUG 测试模式 + Encoder/JY62 init hook) |
| `Core/Src/robot_fight.c` | 更新 (373→600 行: IR4/IR11 分类 + IR1/2 同时触发后退 + JY62 yaw 锁) |
| `Core/Src/robot_backup.c` | 更新 (回台逻辑重写) |
| `Core/Src/robot_roaming.c` | 更新 (后方安全规则) |
| `Core/Src/motor.c` | 更新 (PID 闭环 + 编码器反馈) |
| `Core/Src/vision_parser.c` | 更新 (USART3 RxEvent 分发到 JY62 + lane E 协议 v2 改动) |
| `Core/Src/robot_control.c` | 更新 (在 update 循环 hook `JY62_Update()`) |
| 其余 `*.c/*.h` | 更新 (CRLF 整理 + 微调) |
| `Core/CLAUDE.md` | **保留** (本地文档, 上游不跟踪) |

### 2. 未触碰

| 路径 | 原因 |
|------|------|
| `Drivers/` | 上游与本地实际内容完全相同 (差异仅 CRLF) |
| `freertos/` | 同上 |
| `MDK-ARM/.pack/` | Keil vendor pack, 不变 |
| `MDK-ARM/RTE_Components.h`, `startup_stm32f407xx.s` | 启动文件, 上游本地一致 |
| `vision_upload/`, `RobotCombatAI/`, `创意机器人/`, `3d/` | 不在 lane A 范围 |

## ⚠ 用户必须手工完成的步骤

### A. Keil/IDE 工程添加 jy62.c

**仓库中没有任何 `.uvprojx` 或 `.ioc` 文件被追踪** (`git ls-files` 验证)。Keil 工程是用户本地私有的。

合并后请打开 Keil MDK：

1. **Project → Manage → Project Items**, 在 `Application/User/Core` (或同等组) 添加 `Core/Src/jy62.c`
2. **Options for Target → C/C++ → Include Paths** 确保 `Core/Inc` 已包含 (一般默认就有, 因为新增的 `jy62.h` 在那)
3. 直接 Rebuild。预期 build 顺序: 编译 jy62.c → 编译被改动的 main.c, robot_fight.c, motor.c 等 → 链接

### B. CubeMX (.ioc) 引脚切换 — 可选

如果你**仍然用 CubeMX 维护 .ioc 文件** (CLAUDE.md 提到 `robot.ioc` 但仓库未追踪)：

| 引脚 | 旧 (本地 .ioc 假设) | 新 (上游代码已用) |
|------|----------------|-----------------|
| PB10 | I2C2_SCL | USART3_TX (AF7) |
| PB11 | I2C2_SDA | USART3_RX (AF7) |

需要在 CubeMX 中：
1. 关闭 I2C2 (右键 → Disable I2C2)
2. 启用 USART3 异步模式 + DMA1_Stream1 (RX) + DMA1_Stream3 (TX)
3. PB10/PB11 复用 GPIO_AF7_USART3
4. 重新生成代码 — 会与已合并的 `usart.c` MspInit 一致

如果你不维护 .ioc (直接编辑生成代码)，**无需任何操作**——`usart.c` 已经把 USART3 init/MspInit 直接写好。

### C. JY62 物理接线

确认 JY62 IMU 模块的：
- TX → STM32 PB10 (USART3_RX，反过来连)
- RX → STM32 PB11 (USART3_TX)
- VCC/GND
- 默认波特率 115200

如果 JY62 模块波特率不是 115200，先用 PC 上的 JY62 配置工具改到 115200，否则 `jy62.c` 解析不出来。

## 验证 build 不出错的最快方式

```bash
# 在 Keil 中 Rebuild All, 看输出
# 期望: 0 Error, 0 Warning (除少量 unused variable 警告)
# 若 jy62.c 报 undefined reference 之类, 检查是否已加入工程组
```

如果你不在 Keil 里编译 (用其他工具链)：

```bash
# arm-none-eabi-gcc 仅做语法检查 (不链接)
arm-none-eabi-gcc -c -mcpu=cortex-m4 -mthumb \
    -ICore/Inc -IDrivers/CMSIS/Include -IDrivers/CMSIS/Device/ST/STM32F4xx/Include \
    -IDrivers/STM32F4xx_HAL_Driver/Inc -Ifreertos/include -Ifreertos/portable/GCC/ARM_CM4F \
    -DSTM32F407xx -DUSE_HAL_DRIVER \
    Core/Src/jy62.c -o /tmp/jy62.o
```

## 回滚方案

若集成后行为异常，回滚到合并前：

```bash
git diff HEAD -- Core/ > /tmp/lane-a-merged.patch
git checkout HEAD -- Core/Inc/ Core/Src/
# 还原后, 上游分支镜像保留在 external/robot-fighting/, 随时再合
```

## 仍未做的 (后续计划)

| 项 | 状态 | 备注 |
|------|------|------|
| 实机验证 | **未做** | 需要烧录后实测，本对话仅完成代码层面合并 |
| `.ioc` 同步 | **跳过** | 仓库未追踪此文件，由用户决定 |
| Keil 工程新增 jy62.c | **用户手工** | 见上文 A |
| FreeRTOS 任务架构启用 | **未在范围** | CLAUDE.md 说尚未实现，本次不动 |
| MPU-6050 旧引用清理 | **已被上游清理** | 上游版本 `i2c.c` 不再设置 I2C2 |
