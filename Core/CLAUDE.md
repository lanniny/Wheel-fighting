[根目录](../CLAUDE.md) > **Core (嵌入式控制固件)**

# Core -- STM32F407 嵌入式控制固件

## 变更记录 (Changelog)

| 时间 | 变更 |
|------|------|
| 2026-04-08 | 初始生成模块文档 |

## 模块职责

STM32F407VET6 下位机实时控制系统。负责电机驱动、传感器融合、比赛状态机决策、边缘/掉台安全保护、以及与上位机视觉系统的 UART 通信。运行于 FreeRTOS 环境（当前实际使用主循环轮询，FreeRTOS 任务架构为目标设计）。

## 入口与启动

- **入口文件**: `Core/Src/main.c`
- **启动流程**:
  1. `HAL_Init()` + `SystemClock_Config()` (168MHz HSI PLL)
  2. 外设初始化: GPIO, DMA, ADC2, TIM1/2/3/4/5/8/9, I2C1/2, USART2
  3. 用户初始化: `Obs_Sensor_Init()` -> `MOTOR_Init()` -> `Backup_Init()` -> `MOTOR_StopAll()`
  4. **阻塞等待启动触发**: `Startup_WaitForTrigger()` -- 非接触式红外传感器判定队伍颜色（左遮挡=黄方, 右遮挡=蓝方），通过 UART 通知上位机
  5. `Vision_Init()` -> `Robot_Control_Init()` -- 启动视觉 DMA 接收和顶层状态机
  6. 主循环: `Robot_Control_Update()` + `HAL_Delay(10)` (10ms 周期)

## 对外接口

### UART 通信协议 (USART2, 115200bps, DMA+IDLE 中断)

**上位机 -> STM32** (视觉数据):
```
$type,cx,cy,area,dir*CS\n
  type: E=敌方 N=中立 F=友方 X=无目标 B=炸弹
  cx,cy: 目标中心像素坐标 [0-640, 0-480]
  area: 目标面积(像素)
  dir: 方向偏移 [-100,+100], 负=左 正=右
  CS: body字段逐字节XOR校验和(十六进制2位)
  例: $E,320,240,5000,+25*4A\n
```

**STM32 -> 上位机** (颜色指令):
```
单字节: 'b'=蓝方, 'y'=黄方
```

### 关键全局变量

| 变量 | 文件 | 说明 |
|------|------|------|
| `vision_target` | `vision_parser.h` | 最新视觉目标 (volatile, 中断更新) |
| `Current_Team` | `robot_up.h` | 当前队伍颜色 (TEAM_YELLOW/TEAM_BLUE) |
| `Obs_Data` | `obstacle.h` | 10路红外传感器状态 |
| `voltage[]` / `voltage_filtered[]` | `shade.h` | 灰度传感器原始/滤波电压 |
| `ENCODER[]` | `encoder.h` | 4路编码器数据 (位置/速度/方向) |

## 关键依赖与配置

### 构建工具链

| 项目 | 说明 |
|------|------|
| IDE | Keil MDK-ARM V5.32 (ARM Compiler) |
| 工程文件 | `MDK-ARM/robot.uvprojx` |
| CubeMX 配置 | `robot.ioc` (引脚/时钟/外设定义源) |
| 构建输出 | `MDK-ARM/build/robot.hex` |

### 硬件资源映射

| 外设 | 用途 | 引脚/配置 |
|------|------|-----------|
| TIM4 CH1-CH4 | 电机 PWM (~20kHz) | PD12-PD15, ARR=4199, PSC=0 |
| TIM1/3/5/8 | 编码器模式 (4路) | PE9/11, PA6/7, PA0/1, PC6/7 |
| ADC2 + DMA2_S2 | 灰度传感器x2 (边缘检测) | PC0, PC1 |
| USART2 + DMA1 | 上位机通信 | PD5(TX), PD6(RX), 115200bps |
| I2C1 | OLED 显示 | PB6(SCL), PB7(SDA) |
| I2C2 | MPU-6050 IMU | PB10(SCL), PB11(SDA) |
| GPIO (PE0-4, PE12-14, PA4-5) | 红外避障x10 | 数字输入 |
| TIM7 | FreeRTOS/HAL 系统节拍 | 1ms tick |

## 状态机架构

### 顶层状态机 (robot_control.c)

```
ROBOT_GO_UP  ──(上台完成)──>  ROBOT_ROAMING  ──(视觉/红外检测到目标)──>  ROBOT_ATTACK
     ^                             ^                                        |
     |                             |                              (交战完成/目标丢失)
     |                             +────────────────────────────────────────+
     |                             |
     |                        (掉台检测)
     |                             v
     +──────────────────────  ROBOT_BACKUP  ──(回台完成)──>  ROBOT_ROAMING
```

### 子状态机详情

| 状态机 | 文件 | 子状态 | 说明 |
|--------|------|--------|------|
| GoUp | `robot_up.c` | RUSH -> TURN -> DONE | 倒退冲台(1s) -> 原地掉头(0.5s) |
| Roaming | `robot_roaming.c` | FORWARD -> BACK -> TURN_L/R/BOTH -> DONE | 巡台寻敌, 边缘检测消抖, 灰度掉台保护 |
| Fight | `robot_fight.c` | ENGAGE -> RETREAT -> TURN -> FORWARD -> DONE | 视觉PD追踪, 红外方向定位, F/B回避, 边缘安全 |
| Backup | `robot_backup.c` | SPIN -> RUSH_FORWARD -> RUSH_BACK | 掉台后旋转找台 -> 前冲 -> 后退上台 |

### 安全机制

1. **灰度掉台检测** (双通道):
   - 正常路径: 滤波值 > 2.85V 连续确认 5 次 (50ms)
   - 紧急通道: 原始值 > 3.10V 双传感器同时触发, 零延迟确认
2. **红外边缘检测**: IR1/IR2 下视红外, 触发后退+转向
3. **视觉类型消抖**: 连续 2 帧相同类型才确认有效 (防止 E/F 闪烁)
4. **方向消抖**: 连续 2 次相同方向才确认 (防止光电抖动)

## 数据模型

### 电机控制

- **4 轮差速驱动**: MOTOR_1/2 = 左侧, MOTOR_3/4 = 右侧
- **PWM 映射**: 速度输入 [0-1000] -> PWM [0-4199]
- **预设档位**: Low=300, Medium=600, High=800, Roaming=350
- **转向**: TURN_L=300, TURN_M=400, TURN_S=600
- **弧线转向**: 内侧=300, 外侧=500

### PID 控制器

```c
typedef struct {
    float Kp, Ki, Kd;       // PID 参数
    float setpoint;          // 目标值
    float integral;          // 积分累计 (含限幅)
    float last_error;        // 上次误差
    float output_max/min;    // 输出限幅
    float integral_max;      // 积分限幅
} PID_TypeDef;
```

**注**: PID 控制器已实现但当前固件直接使用开环速度档位控制，PID 闭环为后续优化方向。

## 测试与质量

- **无自动化测试**: 嵌入式 C 代码, 通过实际硬件调试验证
- **调试方式**: OLED 显示 + UART 串口打印 + Keil 仿真器
- **关键调试信息**: `Vision_GetStats()` 提供 DMA 回调/解析成功/校验错误计数

## 常见问题 (FAQ)

**Q: 为什么 dis_sensor.c 不在文件清单中?**
A: 当前代码中红外测距 ADC1 采集模块已被移除或未启用, 仅保留 ADC2 灰度传感器。

**Q: FreeRTOS 在用吗?**
A: FreeRTOS 内核已编译链接, TIM7 作为 HAL 节拍源。但实际控制逻辑运行在主循环 `while(1)` 中, 10ms 轮询周期。FreeRTOS 多任务架构为目标设计。

**Q: 编码器数据用在哪里?**
A: `encoder.c` 已实现完整的 4 路编码器读取和速度计算, 但当前状态机使用开环速度控制。编码器 + PID 闭环控制为后续优化方向。

**Q: 视觉数据超时怎么处理?**
A: `Vision_IsTimeout()` 检测 200ms 无新数据则视为超时。漫游模式下视觉超时时仅依赖红外传感器; 格斗模式下视觉超时导致目标丢失计时。

## 相关文件清单

### 核心源码 (用户代码)

| 文件 | 行数 | 职责 |
|------|------|------|
| `Src/main.c` | 244 | 入口, 初始化, 主循环 |
| `Src/robot_control.c` | 107 | 顶层状态机调度 |
| `Src/robot_fight.c` | 431 | 格斗策略 (视觉PD追踪 + 红外 + 安全) |
| `Src/robot_roaming.c` | 224 | 巡台漫游 (边缘检测 + 灰度保护) |
| `Src/robot_up.c` | 138 | 上台策略 (倒退冲台 + 掉头) |
| `Src/robot_backup.c` | 121 | 掉台回台策略 |
| `Src/motor.c` | 243 | 电机 PWM + 预设动作函数 |
| `Src/vision_parser.c` | 209 | UART DMA 接收 + 协议解析 + 校验 |
| `Src/shade.c` | 53 | 灰度 ADC 采集 + 滑动窗口滤波 |
| `Src/obstacle.c` | 72 | 10 路红外传感器读取 |
| `Src/encoder.c` | 116 | 编码器读取 + 速度计算 |
| `Src/pid.c` | 61 | PID 闭环控制器 |
| `Src/oled.c` | -- | OLED I2C 显示驱动 |

### 头文件

| 文件 | 定义内容 |
|------|----------|
| `Inc/robot_control.h` | RobotState 枚举, 控制接口 |
| `Inc/robot_fight.h` | FightState/EnemyDir 枚举, 引脚配置, 时间参数 |
| `Inc/robot_roaming.h` | RoamingState 枚举, 时间参数 |
| `Inc/robot_up.h` | GoUpState/TeamColor 枚举, 启动引脚配置 |
| `Inc/robot_backup.h` | 回台时间参数 |
| `Inc/motor.h` | 速度档位宏, MOTOR_ID 枚举, 驱动函数声明 |
| `Inc/vision_parser.h` | VisionTarget_t 结构体, 视觉接口声明 |
| `Inc/shade.h` | 掉台检测阈值宏 (SHADE_DOWN_THRESHOLD 等) |
| `Inc/obstacle.h` | Obs_Sensors_t 结构体, 传感器接口 |
| `Inc/encoder.h` | Encoder_TypeDef 结构体, 编码器接口 |
| `Inc/pid.h` | PID_TypeDef 结构体, PID 接口 |

### CubeMX 生成代码 (勿手动修改)

`Src/adc.c`, `Src/dma.c`, `Src/gpio.c`, `Src/i2c.c`, `Src/tim.c`, `Src/usart.c`, `Src/stm32f4xx_hal_msp.c`, `Src/stm32f4xx_it.c`, `Src/system_stm32f4xx.c`, `Src/stm32f4xx_hal_timebase_tim.c`
