# 上游 `wxinxiang8-ai/robot-fighting` ↔ 本地 Core/ Diff 报告

> Wave 1.1 产物。上游 HEAD = `34dbf7d` ("修改进攻里ir1和2都触发时的后退动作")。
> Clone 位置 `external/robot-fighting/`，diff 已去除 CRLF 干扰。

## 文件清单变化

### 仅上游有 (本地需新增)

| 文件 | 含义 |
|------|------|
| `Core/Inc/jy62.h` | JY62 9 轴 IMU 驱动头文件 |
| `Core/Src/jy62.c` | JY62 IMU 驱动实现 (USART3 + DMA + IDLE 中断) |

### 仅本地有 (保留, 上游不需要)

| 文件 | 含义 |
|------|------|
| `Core/CLAUDE.md` | 模块文档 (本地新增, 不上推) |

## 真实 diff 量级 (按真改动行数排序)

| 文件 | local | upstream | real_changed | 备注 |
|------|-------|----------|--------------|------|
| `Src/motor.c` | 328 | 479 | **355** | 上游加了 PID 闭环 + 速度环 ⭐ |
| `Src/main.c` | 244 | 553 | **317** | 上游加了 JY62/PID/Backup 测试模式 |
| `Src/robot_fight.c` | 373 | 600 | **308** | IR4/IR11 分类、IR1+IR2 后退、JY62 yaw 锁 |
| `Src/robot_backup.c` | 328 | 332 | **300** | 回台逻辑显著重写 |
| `Src/robot_roaming.c` | 272 | 358 | **186** | 巡台行为优化, 后方安全规则 |
| `Src/usart.c` | 162 | 282 | **120** | **+USART3 (115200) + DMA1_S1/S3 for JY62** |
| `Src/vision_parser.c` | 218 | 226 | **36** | USART3 RxEvent 分发到 JY62; `'N'` 文档 |
| `Src/obstacle.c` | 62 | 68 | **22** | IR 处理小调整 |
| `Src/robot_control.c` | 80 | 85 | **15** | 顶层调度 hook JY62 update |
| `Src/robot_up.c` | 177 | 177 | **2** | 微调参数 |
| `Src/encoder.c` | 115 | 115 | **2** | 微调 |
| `Src/shade.c` | 56 | 56 | **0** | 仅 CRLF, 无真改动 |
| `Src/pid.c` | 60 | 60 | **0** | 仅 CRLF, 无真改动 |

> 估计上游有约 **1700 行真实新代码**、**400 行重写**。是一次实质性升级。

## ⚠ 关键硬件配置冲突 (必须用户确认)

**上游把 IMU 从 I2C MPU-6050 切换为 UART JY62**，引脚复用冲突：

| 引脚 | 本地 (`CLAUDE.md` + `robot.ioc`) | 上游 (`usart.c` MspInit) |
|------|----------------------------------|-------------------------|
| **PB10** | I2C2 SCL → MPU-6050 IMU (SCL) | USART3_TX → JY62 IMU (TX), AF7 |
| **PB11** | I2C2 SDA → MPU-6050 IMU (SDA) | USART3_RX → JY62 IMU (RX), AF7 |

上游 `usart.c` MspInit 显式定义：
```c
GPIO_InitStruct.Pin = jy62_tx_Pin | jy62_rx_Pin;   // PB10/PB11
GPIO_InitStruct.Alternate = GPIO_AF7_USART3;
```

**这意味着**：
- 上游团队**实物板上换了 IMU**，从 I2C MPU-6050 改成了 UART JY62 模块 (常见的 6/9 轴 UART 输出 IMU)。
- 本地 `Core/CLAUDE.md` 中"I2C2 = MPU-6050 IMU"现在已**过时**。

### 你需要回答 (在我推进 lane A 合并之前)

1. **实物现在装的是哪种 IMU？** JY62 (UART) 还是 MPU-6050 (I2C)？
2. 如果是 JY62 → 完整接受上游 `usart.c` + `jy62.c/.h` + `vision_parser.c` USART3 分发 + `robot.ioc` 引脚改动。
3. 如果是 MPU-6050 → 必须**手动剥离** JY62 引入的 IMU 调用 (`JY62_Update()`、`jy62_data` 引用) 并用现有 MPU-6050 路径替代；JY62 文件保留但不参与编译。
4. 如果 **两套都装了 / 不确定** → 告诉我，按 JY62 优先合，MPU-6050 走 I2C2 路径并存 (前提是引脚不冲突，需要查实物)。

## 适配合并策略 (假设实物 = JY62)

### Phase A1：低风险增量 (可立即合)
| 改动 | 文件 | 说明 |
|------|------|------|
| 新增 JY62 驱动 | `Core/Inc/jy62.h`, `Core/Src/jy62.c` | 直接拷贝 |
| `vision_parser.c` USART3 分发 | `Core/Src/vision_parser.c:144-167` | 把上游 if-else 结构拷过来；XOR 校验/帧解析逻辑两边一致 |
| `Vision_SendCmd` 注释更新 | `vision_parser.c:200` | `'N'=新一轮开始/主控重启` 文档化 |
| `usart.c` 增加 USART3 init + MspInit | `Core/Src/usart.c` | 完整覆盖 (CubeMX 生成区) |
| `gpio.h/c`、`tim.h/c`、`dma.h/c` 等 | 多个 | CubeMX 重新生成区, 跟着 `robot.ioc` 一起替换 |

### Phase A2：业务逻辑合并 (需逐文件审视, 不能盲合)
| 文件 | 风险 | 合并策略 |
|------|------|----------|
| `motor.c` (+151 行) | 中 | 上游引入 PID 闭环 + 编码器反馈，本地仍是开环档位。**保留本地档位 API**，把 PID 调速作为新函数附加 (`MOTOR_SetSpeedClosed()`)，状态机暂不切换 |
| `main.c` (+309 行) | 中-高 | 上游加了一堆 `*_TEST_MODE` 宏 (`BACKUP_TEST`, `JY62_TEST`, `PID_DEBUG`, `IR_OLED_TEST`, `VISION_OLED_TEST`)。这些是开发期切换用的，**全部置 0** 合入即可，不影响生产路径 |
| `robot_fight.c` (+227 行) | **高** | IR4/IR11 角色分类 + IR1/2 同时触发后退 + JY62 yaw 锁可能改变了攻击行为。**必须人工对照** `Core/CLAUDE.md` 中的 Fight 状态机说明再合 |
| `robot_backup.c` | **高** | 大幅重写 (300 行 diff)。可能改变了掉台回台机制。**单独 diff 比对**，确认后 |
| `robot_roaming.c` | 中 | 漫游 + 后方安全规则。同样需对比 |
| `obstacle.c` (+6 行) | 低 | 小调整，可直接合 |
| `robot_control.c` (+5 行) | 低 | 在 update 循环里加了 `JY62_Update()` hook |

### Phase A3：硬件配置同步
- `robot.ioc` 必须从上游同步 (CubeMX 工程文件)
- CubeMX 重新生成代码：`gpio.c/h`, `tim.c/h`, `dma.c/h`, `usart.c/h`, `i2c.c/h`, `stm32f4xx_*` 等都跟着变
- **本地 USER CODE BEGIN/END 区块手工保留**

## 上游近期提交历史 (供决策参考)

```
34dbf7d 修改进攻里ir1和2都触发时的后退动作
d42c1b2 修改速度，后方都检测不到才会触发后边安全
f06bce6 进攻给ir4和ir11分类
6f94223 回归老版本
14eaef4 修正JY62测试模块
ab8efaa jy62
094b723 重新上台接入JY62
dc08556 PID加上滤波
4837ee5 加入PID和编码器
1912b3b jy62和光电
285f050 3.3
dc06bc7 3.2
28c5df4 3.1
c144469 3.0
```

可以看出顺序是：版本 3.x 基线 → 接入 JY62 + PID + 编码器 → IR 攻击逻辑细化 → 后方安全 → IR1/2 触发后退（最新）。

## Vision 协议侧无需变化

上游 `vision_parser.c` 的协议解析与本地完全相同 (CS XOR、5 字段、`type ∈ {E,N,F,X,B,G}`)，只是把 `HAL_UARTEx_RxEventCallback` 改成多 USART 实例分发。`vision_upload/comm.py` 不需要任何配套改动。

## 推荐下一步

1. **你回答 IMU 问题** → 我才能定 lane A 的具体合并粒度。
2. 在你回答前，lane A 暂挂；其它 lane (B/C/D/E + Wave 3 部分准备工作) 可先动。
3. 如果你今天没法回答，**默认按 JY62 路径**合并，MPU-6050 调用我会用 `#if 0` 注释保留以便回退。
