# 视觉代码审查报告 (Wave 1.2)

> 审查范围：本地 `vision_upload/{config,comm,detector,main}.py` (v6) + STM32 端 `Core/Src/vision_parser.c`。
> 评分约定：**[阻塞]** 必修；**[优化]** 应修；**[可选]** 锦上添花；**[亮点]** 已经做得好的部分。

## 总评

整体代码质量 **高**：结构清晰、错误处理完整、有 echo 确认通道、有掉台迟滞状态机、有 watchdog、有标定 JSON 自动加载。绝大多数问题都是**协议演进 / 部署同步 / NPU 集成**层面的、而不是单文件 bug。

`vision_upload/` v6 已经具备 production 部署所需的健壮性。下面的清单按优先级排序。

---

## 阻塞级问题 (必须在 Wave 2 解决)

### B1. 部署版本漂移：本地 v6 vs 板端 v5
- **现象**：本地 `vision_upload/main.py:277, 912` 标注 `v6`；板端 `/home/radxa/vision_upload/main.py` 第 3 行标注 `v5`。
- **影响**：v6 的回声确认 (echo)、掉台迟滞、性能分析器、SIGTERM handler、watchdog 都没在板上跑。当前 systemd `vision.service` (active 3 weeks 1 day) 是旧版。
- **动作**：Wave 2 lane B 完成补丁后，**整目录 rsync** 到 `/home/radxa/vision_upload/` 并 `sudo systemctl restart vision`。先备份板端那 6 个本地没有的调试脚本 (`cam_test.py`, `diag_hsv.py`, `uart_capture.py`, `uart_debug.py`, `uart_test.py`, `vision.service.new`)。

### B2. STM32 协议解析器是"5 字段硬编码"
- **位置**：`Core/Src/vision_parser.c:100`
  ```c
  int n = sscanf(body_copy, "%c,%d,%d,%d,%d", &type_ch, &cx, &cy, &area, &dir);
  if (n != 5) return;
  ```
- **影响**：只要协议升级到 6+ 字段（v2 想加 timestamp / 置信度 / target_id），STM32 直接丢帧。两端会"视觉端发送成功 + STM32 默默丢弃"，最难调试的故障类别。
- **协议向后兼容方案**：让上位机在 body 里**追加在 `*` 之前**，并且使用**任意可选字段**：
  ```
  v1 (现行): $E,320,240,5000,+25*4A\n
  v2:        $E,320,240,5000,+25,1715,87,3*5C\n   ← 多了 ts,conf,tid
  ```
  STM32 端改成"取前 5 个字段，忽略剩余"——`sscanf("%c,%d,%d,%d,%d%n", ..., &n_consumed)` 配合 `n_consumed` 判断是否消费到 `*`。这样：
  - 旧 v1 STM32 固件 + 新 v2 视觉 → 兼容（v2 帧多余字段被忽略）
  - 新 v2 STM32 固件 + 旧 v1 视觉 → 兼容（v2 字段缺省按 0 处理）
- **动作**：Wave 2 lane E 实现。

### B3. `read_command()` 只返回最后一个命令字符
- **位置**：`vision_upload/comm.py:282-319`
- **现象**：`while pos < len(buf)` 中每次 `cmd = c` 直接覆盖；如果 STM32 同时发了 `'D'`(进入掉台) + `'b'`(切换颜色)，主循环只看到最后一个。
- **影响**：状态机切换可能丢失中间状态，`_handle_command(cmd)` 只跑一次。掉台模式可能不被触发或颜色切换被跳过。
- **动作**：改成累积 `commands = []`，主循环 `for cmd in commands: self._handle_command(cmd)`。`vision_upload/main.py:325` 同步改循环。

---

## 优化级问题 (Wave 2 处理)

### O1. STM32 端 vs 上位机端 echo 时间戳口径不一致
- `comm.py:233-234` 计算 `_echo_latency = (now - self._last_tx_ts) * 1000` —— 这是**上位机本地时间**的回环延迟，包含板端排队 + DMA + 主循环延迟。但 STM32 端的 echo 是在视觉解析完成后立即发出的，**没有显式 timestamp 字段**。
- **影响**：当前 echo latency 显示的是 RTT，无法判断"是 STM32 处理慢还是 UART 回程拥塞"。
- **动作**：v2 协议加 `ts` 字段后，echo 帧带回 STM32 收到时的 `HAL_GetTick()`，可分离上下行延迟。

### O2. `force_send_now()` 半边重置
- **位置**：`comm.py:367-369`
  ```python
  def force_send_now(self):
      self._last_send = 0.0
  ```
- **影响**：跳过频率限制后，下次 `send_target()` 会更新 `_last_tx_ts = now`，但旧的 `_last_tx_ts` 在 `force_send_now()` 调用瞬间没归零；对 echo 健康判定可能短暂出现"echo timeout"误报。
- **动作**：`force_send_now()` 里也 `self._last_tx_ts = 0.0`，让 echo 计时跟着重置。

### O3. STM32 端 vision_parser 缺少 RX 错误回调
- **位置**：`vision_parser.c` 全文无 `HAL_UART_ErrorCallback` 实现。
- **现象**：DMA 出现 OVR / FE / NE / PE 等错误时 HAL 会触发错误回调，但这里没人接管，DMA 不会自动重启。
- **影响**：长时间运行后偶发 USART 错误会导致 vision_target 被永久卡住（直到下次烧录）。
- **动作**：实现：
  ```c
  void HAL_UART_ErrorCallback(UART_HandleTypeDef *huart) {
      if (huart->Instance == USART2) {
          __HAL_UART_CLEAR_OREFLAG(huart);
          HAL_UARTEx_ReceiveToIdle_DMA(&huart2, dma_rx_buf, DMA_RX_BUF_SIZE);
          __HAL_DMA_DISABLE_IT(huart2.hdmarx, DMA_IT_HT);
      }
  }
  ```

### O4. STM32 协议 body 缓冲只有 48 字节
- **位置**：`vision_parser.c:80, 94`
  ```c
  if (body_len <= 0 || body_len >= 48) return;
  char body_copy[48];
  ```
- **影响**：v2 协议加字段后可能 > 48 字节直接被丢。
- **动作**：v2 同步把 `body_copy` 扩到 96 或 128 字节，并把 48 改成宏。

### O5. `Tracker.update()` 新目标确认逻辑可能重入
- **位置**：`detector.py:156-163`
- **现象**：循环内又遍历 `targets` 用 30px 容差找匹配，时间复杂度 O(n²)，且如果 targets 已被 sort 后输入会找错对象。
- **影响**：目标多 (>5) 时 CPU 上升、可能给出错误确认目标。
- **动作**：在第一次匹配阶段就把"刚确认"的目标记入 `result`，省掉二次扫描。重写：
  ```python
  if best_id is not None:
      tr = self._tracks[best_id]
      ... # 现有逻辑
      tr['confirmed'] = (tr.get('confirm_count', 1) >= self.confirm_frames)
      if tr['confirmed']:
          result.append(...)  # 在这里就 append
  ```

### O6. `Vision_SendCmd` 的 `'D'` 与 `comm.py` 的 `'D'` 处理时间窗
- 板端 `comm.py:185` 收到 `'D'` 立即 `self.drop_recovery = True`；STM32 端 `vision_parser.c` 仅有一个 single-byte send，没有等待 ACK。如果 USART 在 STM32→上位机 方向丢字节（罕见但发生过），掉台模式会失同步。
- **动作**：v2 协议引入 single-byte command echo（视觉收到 'D' 后用 `$ack,D*..\n` 显式回执）；STM32 端如果 100ms 内没看到 ack 则重发 'D'。

### O7. main.py 文档/版本号不一致
- 第 3 行 `"""轮式格斗机器人 - 视觉主程序 v5"""` 与第 277 行 `print('Vision system v6 running')` 矛盾。
- **动作**：统一为 v6（`vision_upload/CLAUDE.md` 已明确说 v6）。

---

## 可选 / 风格类

### V1. `_parse_cmd_byte('S')` 返回 `'s'` 而非 `'S'`
- `comm.py:177-180`，调用方现在不区分大小写，**目前不是 bug**，但读起来困惑。
- 建议返回原字节，让 main 端做大小写归一。

### V2. `frame_idx % 3 == 0` 流频率硬编码
- `main.py:689, 436` —— 30 FPS / 3 = 10 FPS 流媒体。如果想提高画质或降到 5 FPS 节省带宽，需改两处。
- 建议配 `config.STREAM_EVERY_N_FRAMES = 3`。

### V3. `find_camera()` 依赖 `v4l2-ctl`
- `config.py:17` —— 板端没装 `v4l2-utils`（Cubie 环境快照证实）。当前已 fallback 到 `/dev/video0/1/2` 枚举，OK。
- 建议直接默认 fallback，删掉 v4l2-ctl 调用，省去 subprocess 开销。

### V4. 日志中的 percentile / blob 字段太长
- `main.py:404-418` `DROP_DBG` 一行 200+ 字符，肉眼对齐困难。
- 建议改成多行 `det_logger.info('\n  v_mean=%.0f\n  v_std=%.1f...')`。

---

## 亮点 (无需改动)

- ✅ `comm.py` 的 USB 热拔插检测 + 设备消失主动 reconnect (line 141-153)。这在系统级是"金本位"做法。
- ✅ `comm.py` 的指数退避重连 2→4→8→8s (line 124-139) 避免疯狂打开 /dev/。
- ✅ `comm.py` echo 健康通道 + 状态属性 (`echo_confirmed`, `echo_healthy`, `echo_age`) — STM32 → 视觉 反向心跳是少有的正确实现。
- ✅ `detector.py` 的 CLAHE 仅在 V 通道（默认）+ 自适应形态学核（基于上帧最大面积选 small/medium/large kernel）— 对光照变化和远近目标都很 robust。
- ✅ `detector.py:530-540` 在掉台检测里**手算 V/S 通道而非 cvtColor()**，省掉一次完整 HSV 转换 — 实测每帧 ~0.5ms 提升。
- ✅ `detector.py` 黄色二阶段验证 (S 均值 + H std + 圆度 + 底部排除) — 暖光环境下蓝色冒充黄色这种典型坑都覆盖了。
- ✅ `detector.py` `detect_tags_in_rois` ROI Tag 检测比全帧 ~15× — 很对的优化。
- ✅ `config.py` 全字段都通过 `os.environ.get()` 暴露 — SSH 调试时可临时注入参数无需重启服务。
- ✅ `config.py` `_load_calibration()` 自动加载 `hsv_calibration.json` — 标定→生产不用改代码。
- ✅ STM32 `vision_parser.c` "先 valid=0 再写字段最后 valid=1" 模式 (line 104-114) 防止主循环读到撕裂状态 — 真正会写并发的代码。

---

## 优先级路线图 (Wave 2 排期)

| 优先级 | 项 | 改动量 | 落地 lane |
|--------|----|--------|-----------|
| 🔴 必修 | B1 重新部署到 Cubie | 5 min (rsync) | lane B 末尾 |
| 🔴 必修 | B2 协议 v2 (5+N 兼容) | 中 | lane E |
| 🔴 必修 | B3 read_command 累积命令 | 小 | lane B |
| 🟡 应修 | O1 echo 加 timestamp | 小 (协议 v2 自带) | lane E |
| 🟡 应修 | O2 force_send_now 全重置 | 1 行 | lane B |
| 🟡 应修 | O3 STM32 ErrorCallback | 中 | lane A 或 E |
| 🟡 应修 | O4 body 缓冲扩展 | 小 | lane E |
| 🟡 应修 | O5 Tracker 重构 | 中 | lane B |
| 🟡 应修 | O6 cmd echo ack | 中 | lane E（可推迟） |
| 🟢 可选 | O7/V1-V4 | 小 | lane B 顺手做 |
