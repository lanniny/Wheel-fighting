# STM32 端 UART 握手对接指南（给电控队友）

> 2026-05-30 | 视觉端已实现并验证，STM32 端待实现。
> 来源：匠(embedded-expert) 设计 + 烛(codex-reviewer) 两轮对抗复核 + Workflow 三视角文档审查(18条) + 主驾协议核实。
>
> ⚠️ **本文档用"代码锚点"而非绝对行号定位**（CubeMX 重生成/编辑会让行号漂移）。请按"在 XXX 这一行之后"找位置，不要数行号。

---

## 1. 背景与目的

掉台冲台流程中，STM32 与视觉(Radxa) 经 UART(USART2 115200, DMA-IDLE RX / 阻塞 TX) 通信：
- STM32 进 BACKUP → 发 `#D` 让视觉(可选)进掉台黑色检测
- 冲台上台(Backup_IsDone) → 发 `#S` 让视觉退掉台

**原问题**：`#D`/`#S` 各只发**一次**，115200 噪声/拆包丢这 3 字节 → 视觉收不到 → 撞己方/卡死。

**握手目的**：STM32 发 D/S 后**等视觉回确认，收到才停发，否则周期重发** → 保证 D/S 不丢。

---

## 2. 握手协议规约

| 方向 | 格式 | 内容 |
|------|------|------|
| STM32 → 视觉（命令） | `#<cmd>\n` | `D`=进掉台 / `S`=退掉台(大写) / `N` / `b`/`y` |
| 视觉 → STM32（确认） | `$<ack>*CS\n` | `d`=确认收到D / `s`=确认收到S（**小写**） |

**大小写天然隔离**：STM32 发大写 `#D`/`#S`，视觉回小写 `$d`/`$s`，与目标 type(`E/N/F/X/B/G` 全大写) 不冲突。

**✅ 校验和已验证对齐**：单字符 body 的 XOR = 该字符 ASCII。
- `$d` → CS=`64`（'d'=0x64）；`$s` → CS=`73`（'s'=0x73）
- 视觉实发：`$d*64\n` / `$s*73\n`（已核 comm.py `_checksum('d')` 产出 `'64'`）
- STM32 `calc_checksum(body,1)` → `strtol("64",16)=0x64` 匹配 ✓（**不会 cserr 丢帧**）

---

## 3. 视觉端（已实现，comm.py，队友无需改）

- 收 `#D` → 回 `$d*64\n` + 打印 `[HANDSHAKE] 收到 D → 回确认 $d`
- 收 `#S`(大写) → 回 `$s*73\n` + 打印 `[HANDSHAKE]`
- **开关 `HANDSHAKE_ENABLED`**(默认 0/关)：STM32 端接好前**不发 ack**(防 $d/$s 被当目标污染)。STM32 烧录握手固件后，板卡设 `VISION_HANDSHAKE=1` 开启。

> 🔑 **关键澄清（验证时必看）**：**回 `$d`/`$s` 只受 `VISION_HANDSHAKE` 门控，与是否进 DROP 无关**。
> - 只要 `VISION_HANDSHAKE=1`，视觉收 `#D` **必回 `$d`**（不论 `VISION_DROP_ENABLED` 取值）。
> - 是否"进 DROP 黑色检测/发 G"是**另一个独立开关** `VISION_DROP_ENABLED` 决定的。当前仓库策略可能是"视觉永远正常上台、不进 DROP"。
> - **所以调试握手不必开 DROP**，也**不要**把"是否发 G/掉台行为"当作握手成功的判据——只看 `$d/$s 是否回 + STM32 是否停发`。

---

## 4. STM32 端要做（三处改动 + 编译自检）

> 所有"整体替换"= **删除原函数/原 case 内全部代码，用下方整段覆盖**（不要在旧代码上追加，否则 `MOTOR_BrakeAll`/`Backup_Init` 会重复执行）。

### 4.1 `Core/Inc/vision_parser.h`
**位置**：加在文件末尾 `#endif` 之前（或 `Vision_GetSnapshot` 原型之后）。
```c
/* 视觉端握手 ACK 标志 (中断置位, 主循环读后清零) */
extern volatile uint8_t g_vision_d_ack;
extern volatile uint8_t g_vision_s_ack;
void Vision_ClearAck(void);   /* 进入新一轮握手前清, 关中断防撕裂 */
```

### 4.2 `Core/Src/vision_parser.c`

**(a) 标志定义**：加在 `volatile VisionTarget_t vision_target = ...;` 这一行之后。
```c
/* 握手 ACK 标志 (volatile: 中断写/主循环读) */
volatile uint8_t g_vision_d_ack = 0u;
volatile uint8_t g_vision_s_ack = 0u;
```

**(b) `parse_frame` 旁路**：插在 `char type_ch = body[0];` 这一行**之后**、任何 `vision_target.xxx = ...` 写入**之前**（即紧接 type_ch 赋值、在 `vision_target.valid = 0u;` 之前）。
```c
    char type_ch = body[0];

    /* ── 握手 ACK 旁路【致命#1: 必须在写 vision_target 之前】──
     * $d/$s 是视觉对 #D/#S 的确认, 不是目标。若不拦截会被当成
     * type='d'/'s'、valid=1 的目标, 污染 type 并刷新 timestamp
     * 让 Vision_IsTimeout 误判数据新鲜。
     * 旁路 return 后 vision_target 完全不被改动(不刷 timestamp/valid/type)
     * 是预期行为; 若同一 DMA 缓冲里还有真目标帧, parse_buffer 的
     * last-frame-wins 会正常处理, 队友无需在旁路里维护 vision_target。 */
    if (type_ch == 'd') { g_vision_d_ack = 1u; return; }
    if (type_ch == 's') { g_vision_s_ack = 1u; return; }

    /* （以下为原代码, 不动）写入全局变量: 先置 valid=0 ... */
    vision_target.valid = 0u;
    /* ... */
```
> 注：旁路里**不要** `vision_rx_success++`（ack 不是目标帧；计入会抬高串口诊断的 `ok:` 计数，干扰现场对账）。

**(c) `Vision_ClearAck` 实现**：插在 `Vision_GetSnapshot` 函数右大括号 `}` 之后、`HAL_UART_ErrorCallback` 之前。签名须与 .h 声明一致 `void(void)`。
```c
void Vision_ClearAck(void)
{
    uint32_t primask = __get_PRIMASK();
    __disable_irq();
    g_vision_d_ack = 0u;
    g_vision_s_ack = 0u;
    if (primask == 0u) __enable_irq();
}
```

### 4.3 `Core/Src/robot_control.c`

> 文件顶部已有 `#include "vision_parser.h"`，所以 `g_vision_*_ack`/`Vision_ClearAck`/`Vision_SendCmd` 可直接用，**无需新增 include**。

**(a) 握手状态变量**：插在静态变量区末尾（`static uint8_t robot_attack_candidate_count = 0;` 这一行之后、第一个 `static` 函数定义之前的空行处）。
```c
/* ── 掉台握手: D/S 重发-等-ack ── */
#define HS_RESEND_PERIOD_MS   250u   /* 重发周期 */
#define HS_D_TIMEOUT_MS       2000u  /* D 兜底超时(视觉永不回也继续回台) */
#define HS_S_TIMEOUT_MS       2000u  /* S 兜底超时(强制进 ROAMING 防卡死) */
typedef enum { HS_NONE = 0, HS_WAIT_D, HS_WAIT_S } HandshakeStage_t;
static HandshakeStage_t robot_hs_stage = HS_NONE;
static uint32_t robot_hs_last_send = 0u;
static uint32_t robot_hs_start     = 0u;
```

**(b) `Robot_Control_EnterBackup` 整体替换：**
```c
static void Robot_Control_EnterBackup(void)
{
    uint32_t now = HAL_GetTick();
    MOTOR_BrakeAll();
    Vision_ClearAck();            /* 清上一轮残留 ack */
    Vision_SendCmd('D');          /* 首发 */
    robot_hs_stage     = HS_WAIT_D;
    robot_hs_last_send = now;
    robot_hs_start     = now;
    Backup_Init();
    robot_state = ROBOT_BACKUP;
}
```

**(c) `ROBOT_BACKUP` 分支整体替换**（保留末尾 `break;`）：
```c
        case ROBOT_BACKUP:
        {
            uint32_t now = HAL_GetTick();

            /* 阶段 D: 回台动作进行中, 周期重发 #D 直到收到 $d */
            if (robot_hs_stage == HS_WAIT_D)
            {
                if (g_vision_d_ack)
                    robot_hs_stage = HS_NONE;                       /* 成功停发 */
                else if ((now - robot_hs_start) >= HS_D_TIMEOUT_MS)
                    robot_hs_stage = HS_NONE;                       /* 兜底放弃(继续回台) */
                else if ((now - robot_hs_last_send) >= HS_RESEND_PERIOD_MS)
                { Vision_SendCmd('D'); robot_hs_last_send = now; }  /* 重发 */
            }

            Backup_Update();   /* 回台动作始终推进, 不被握手阻塞 */

            /* 阶段 S: 回台完成发 #S 并等 $s 才进 ROAMING */
            if (Backup_IsDone())
            {
                if (robot_hs_stage != HS_WAIT_S)
                {
                    /* 刚进 S 握手: 首发 #S 起计时【ClearAck 只在这一拍调用!】 */
                    Vision_ClearAck();
                    Vision_SendCmd('S');
                    robot_hs_stage     = HS_WAIT_S;
                    robot_hs_last_send = now;
                    robot_hs_start     = now;
                }
                else if (g_vision_s_ack)
                {
                    robot_hs_stage = HS_NONE;
                    Robot_Control_EnterRoaming();                  /* 成功进漫游(★见下方注意) */
                }
                else if ((now - robot_hs_start) >= HS_S_TIMEOUT_MS)
                {
                    robot_hs_stage = HS_NONE;
                    Robot_Control_EnterRoaming();                  /* 兜底超时强制流转 */
                }
                else if ((now - robot_hs_last_send) >= HS_RESEND_PERIOD_MS)
                { Vision_SendCmd('S'); robot_hs_last_send = now; } /* 重发 */
            }
            break;
        }
```
> ★ **【高危·必核】进 ROAMING 的方式要和原代码一致**：原 `ROBOT_BACKUP` 分支 `Backup_IsDone()` 后是怎么进 ROAMING 的？
> - 若原来就是 `Robot_Control_EnterRoaming()` → 保持上面写法即可。
> - 若原来是 `robot_state = ROBOT_ROAMING;`（不调 EnterRoaming）→ **请把上面两处 `Robot_Control_EnterRoaming()` 改回 `robot_state = ROBOT_ROAMING;`**。
> - **风险**：`robot_backup.c` 的 `BACKUP_FINISH_TURN` 分支在置 `Backup_Done=true` 那一拍**可能已调过 `Roaming_Init()`**。若 `EnterRoaming` 内部又调一次 `Roaming_Init`，会**二次初始化**。确认 `Roaming_Init` 幂等可重入，或直接用 `robot_state=ROBOT_ROAMING` 保持原语义。**这是改之前必须核对的一点。**

### 4.4 `Core/Src/robot_backup.c` — **不用改** ✓
因为 d/s 在 `parse_frame` 里命中后**立即 return、从不写入 `vision_target`**，所以 `Backup_GetVisionType` 读到的快照里永远不会出现 d/s，robot_backup.c 无需任何改动。

### 4.5 ✅ 改完编译自检（三件套）
1. **`vision_parser.h`**：加了 2 个 `extern` + `Vision_ClearAck` 声明 ✓
2. **`vision_parser.c`**：加了 2 个 `volatile` 定义 + `parse_frame` 旁路 + `Vision_ClearAck` 实现 ✓
3. **`robot_control.c`**：用到 `g_vision_*_ack`/`Vision_ClearAck`/`Vision_SendCmd`——文件顶部**已有** `#include "vision_parser.h"`(无需新增) ✓
- 已确认全工程无 `g_vision_d_ack`/`g_vision_s_ack` 同名符号，不冲突。
- **State_Debug_GetName / OLED 无需改动**：握手用 `robot_hs_stage` 子状态(robot_control.c 内部 static)，不新增顶层 `RobotState` 枚举，所以调试显示完全不受影响。
- 编译应零 warning。

---

## 5. ⚠️ 致命注意事项（改之前必读）

| # | 注意事项 | 后果（不遵守） |
|---|---------|--------------|
| **致命1** | **d/s 旁路必须在写 `vision_target` 之前**（紧接 `char type_ch=body[0];` 之后拦截 return） | $d/$s 被当目标，污染 type、刷新 timestamp 让 Vision_IsTimeout 误判 |
| **致命2** | **`Vision_ClearAck()` 只在"刚进 S 阶段"那一拍调用一次**（`robot_hs_stage != HS_WAIT_S` 守卫） | 每拍都清 → `$s` 永远收不到 → **卡死** |
| **致命3** | **2s 兜底超时不可省**（HS_S_TIMEOUT 到了强制 EnterRoaming） | 视觉永不回 ack → 机器人停在 BACKUP **瘫痪** |
| **致命4** | **绝不用阻塞 `while` 等 ack**，用非阻塞轮询（主循环每拍查一次 `g_vision_*_ack`） | 阻塞会卡死主循环、饿死灰度掉台/IR 安全检测 |
| **高危5** | **进 ROAMING 方式与原代码一致**（见 4.3c ★），警惕 `Roaming_Init` 二次初始化 | 二次 init 可能重置漫游状态/计数 |
| 重要6 | 主循环 10ms 周期；250ms 重发=每 25 拍发一次。若启用 `STATE_UART_DEBUG_MODE`，debug 串每 500ms 阻塞 TX ~120B(约10ms)，与重发共用 USART2 阻塞通道(单线程串行不抢 gState，但单拍最坏阻塞 ~10ms)，掉台期间灰度/IR 安全检测在同一主循环——实测掉台少见，可接受 | — |

---

## 6. 验证方法（烧录后）

1. `/keil` 编译 + `/jlink` 烧录握手固件
2. 视觉端设 `VISION_HANDSHAKE=1`（`systemctl edit vision.service` 加 `Environment=VISION_HANDSHAKE=1` 或临时 export，重启 vision）
3. **serial 抓视觉实发**：应见 `$d*64\n`（校验位 `64`）/ `$s*73\n`
4. **触发掉台，只看握手判据**（不看是否发 G）：
   - 视觉 `journalctl -u vision.service` 见 `[HANDSHAKE] 收到 D → 回确认 $d`
   - **STM32 侧：发 #D 后收到 $d → 停发 D**（不再重发）；退掉台同理 #S → $s → 进 ROAMING
   - ⚠️ **不要把"是否发 G/掉台动作"当握手判据**（那受 `VISION_DROP_ENABLED` 控制，与握手无关）
5. **丢包测试**：故意干扰/拔插，确认重发生效（D 丢 → STM32 继续发 → 视觉再回 $d → 最终收到停发）

---

## 7. 启用 / 回退开关

| 开关 | 默认 | 作用 |
|------|------|------|
| `VISION_HANDSHAKE` | `0`(关) | 视觉是否发 $d/$s 确认。**STM32 烧录握手固件后才设 1** |
| `VISION_DROP_ENABLED` | `1`(开, 以 config.py 为准) | 视觉掉台冲台检测(收 #D 进 DROP 发 G)。设 0=纯光电上台。**与 HANDSHAKE 是两个独立开关，默认方向相反** |

**回退握手**：`VISION_HANDSHAKE=0` → 视觉不发 ack（STM32 退化成单发 D/S，=握手前行为）。

---

## 8. 风险表（匠）

| # | 风险 | 概率 | 缓解 |
|---|------|------|------|
| R1 | 重发阻塞 TX 累计(+debug串) | 中 | 250ms/次×0.26ms ≪ 主循环；与 debug 串单线程串行不抢 gState；最坏单拍 ~10ms |
| R2 | 握手卡死(视觉永不回) | 中 | **2s 兜底强制流转(必须项)** |
| R3 | ClearAck 每拍误清 | 高(若实现错) | **`stage != HS_WAIT_S` 守卫，只首拍清** |
| R4 | D 兜底放弃后视觉没切模式 | 低 | 没 G 也能靠 IMU+IR+灰度回台，可接受降级 |
| R5 | ack 跨轮残留 | 低 | EnterBackup 里 ClearAck 已清 |
| R6 | 进 ROAMING 二次 Roaming_Init | 中 | 见 4.3c ★，核对原代码 + 确认幂等 |

---

## 9. 复审建议

STM32 改完，建议召唤 **烛(codex-reviewer embedded)** 复审 `robot_control.c` 状态机，重点：
1. **进 ROAMING 方式 + `Roaming_Init` 二次初始化**（4.3c ★，本方案最大不确定点）
2. `Vision_ClearAck` 守卫（只首拍清）
3. 多轮掉台时 `robot_hs_stage` 复位（再次 EnterBackup 会重置 ✓）
4. 2s 兜底边界

烧录后用 **serial skill 抓一帧** 确认视觉 `$d` 校验位是 `64`。

---

*视觉端代码已在本仓库：`vision_upload/comm.py`(`_send_ack`/`_parse_cmd_byte`) + `vision_upload/config.py`(`HANDSHAKE_ENABLED`)。本文档经 Workflow 三视角审查(18条)修订。*
