/**
 * @file    vision_parser.c
 * @brief   视觉系统UART解析模块
 *
 * 协议格式 (简化 2026-05-28): $<type>*CS\n
 *   type: E=敌方 N=中立 F=友方 X=无目标 B=炸弹 G=冲台(掉台回复)
 *   CS:   type 单字符的异或校验和(十六进制, 2位; 单字符即其 ASCII 值)
 *   示例: $E*45\n
 *   注:   cx/cy/area/dir 已废弃 (下位机实战只消费 type), 上位机不再发送。
 *
 * 实现方案: USART2 + DMA(NORMAL) + IDLE行中断
 *   - HAL_UARTEx_ReceiveToIdle_DMA 自动利用IDLE中断触发回调
 *   - 每帧回调后立即重启DMA, 保证连续接收
 *   - ORE 等错误由 HAL_UART_ErrorCallback 兜底清标志+重启 (防接收永久死亡)
 */
#include "vision_parser.h"
#include "usart.h"
#include "jy62.h"
#include <string.h>
#include <stdlib.h>
#include <stdio.h>

/* ---- 内部常量 ---- */
#define DMA_RX_BUF_SIZE   256u   /* DMA接收缓冲区大小 */
#define VISION_TIMEOUT_MS 200u   /* 数据超时阈值(ms) */

/* ---- 全局目标数据 ---- */
volatile VisionTarget_t vision_target = { .type = 'X', .valid = 0u };

/* ---- 接收统计 ---- */
static uint32_t         vision_rx_total   = 0u;  /* DMA回调触发次数 */
static uint32_t         vision_rx_success = 0u;  /* 成功解析帧数 */
static uint32_t         vision_rx_cserr   = 0u;  /* 校验和错误次数 */
static uint32_t         vision_rx_restart_fail = 0u;  /* DMA重启失败次数(应为0; 非0=ORE/拆包高发) */

/* ---- 内部缓冲区 ---- */
static uint8_t dma_rx_buf[DMA_RX_BUF_SIZE];

/* ------------------------------------------------------------------ */
/* 内部函数: XOR校验                                                   */
/* ------------------------------------------------------------------ */
static uint8_t calc_checksum(const char *data, int len)
{
    uint8_t cs = 0;
    for (int i = 0; i < len; i++) {
        cs ^= (uint8_t)data[i];
    }
    return cs;
}

/* ------------------------------------------------------------------ */
/* 内部函数: 解析单帧  $<type>*CS\n                                    */
/* ------------------------------------------------------------------ */
static void parse_frame(const uint8_t *buf, uint16_t size)
{
    /* 在缓冲区内找 '$' */
    const char *dollar = NULL;
    for (uint16_t i = 0; i < size; i++) {
        if (buf[i] == '$') {
            dollar = (const char *)(buf + i);
            break;
        }
    }
    if (dollar == NULL) return;

    /* 在 '$' 之后找 '*' */
    uint16_t remaining = (uint16_t)(size - (uint16_t)(dollar - (const char *)buf));
    const char *star = NULL;
    for (uint16_t i = 1; i < remaining; i++) {
        if (dollar[i] == '*') {
            star = dollar + i;
            break;
        }
    }
    if (star == NULL) return;

    /* 确保 '*' 后至少还有2个校验和字符 */
    uint16_t tail_len = (uint16_t)((const char *)(buf + size) - star);
    if (tail_len < 3u) return;

    /* 计算body的XOR校验 */
    const char *body    = dollar + 1;
    int         body_len = (int)(star - body);
    if (body_len <= 0 || body_len >= 48) return;

    uint8_t expected_cs = calc_checksum(body, body_len);

    /* 解析十六进制校验和 */
    char cs_str[3] = { star[1], star[2], '\0' };
    uint8_t received_cs = (uint8_t)strtol(cs_str, NULL, 16);

    if (expected_cs != received_cs) {
        vision_rx_cserr++;
        return;  /* 校验失败, 丢弃 */
    }

    /* 协议简化: body 只含单个 type 字符 (校验和已保证 body 完整性) */
    char type_ch = body[0];

    /* 写入全局变量: 先置 valid=0 屏蔽旧数据, 写完字段后再置 valid。
     * 多字段更新对主循环的原子性由 Vision_GetSnapshot() 读侧关中断保证
     * (防 fight/backup 跨 USART2 中断撕裂读 timestamp/type) */
    vision_target.valid     = 0u;
    vision_target.type      = type_ch;
    vision_target.cx        = 0;
    vision_target.cy        = 0;
    vision_target.area      = 0;
    vision_target.dir       = 0;
    vision_target.timestamp = HAL_GetTick();
    vision_target.valid     = (type_ch != 'X') ? 1u : 0u;
    vision_rx_success++;
}

/* ------------------------------------------------------------------ */
/* 内部函数: 多帧解析 — 遍历缓冲区解析所有完整帧                       */
/* ------------------------------------------------------------------ */
static void parse_buffer(const uint8_t *buf, uint16_t size)
{
    uint16_t pos = 0;
    while (pos < size)
    {
        /* find '$' */
        while (pos < size && buf[pos] != '$') pos++;
        if (pos >= size) break;

        /* find '\n' */
        uint16_t start = pos;
        uint16_t end = pos + 1;
        while (end < size && buf[end] != '\n') end++;
        if (end >= size) break;

        /* parse this frame (last valid frame wins) */
        parse_frame(buf + start, end - start + 1);
        pos = end + 1;
    }
}

/* ------------------------------------------------------------------ */
/* HAL回调: DMA接收完成 或 IDLE线触发                                  */
/* ------------------------------------------------------------------ */
void HAL_UARTEx_RxEventCallback(UART_HandleTypeDef *huart, uint16_t Size)
{
    if (huart->Instance == USART2)
    {
        vision_rx_total++;

        /* 多帧解析: 处理缓冲区中所有完整帧 */
        if (Size > 0u) {
            parse_buffer(dma_rx_buf, Size);
        }

        /* 立即重启DMA接收, 准备下一帧。
         * 不关 HT 半传输中断: 256B 缓冲 + 6B 短帧, HT 几乎不触发;
         * NORMAL 下 HT 回调内重启会返 HAL_BUSY 被吞, 但 DMA 未 abort 自然续跑,
         * 后续 IDLE 走正常 abort+重启路径, 故无需关
         * (烛 2026-05-28 reflection2 读 HAL 源码实证, 修正原匠 F1 解释)。 */
        if (HAL_UARTEx_ReceiveToIdle_DMA(&huart2, dma_rx_buf, DMA_RX_BUF_SIZE) != HAL_OK)
        {
            vision_rx_restart_fail++;
        }
        return;
    }

    if (huart->Instance == USART3)
    {
        JY62_UART_RxEventCallback(huart, Size);
    }
}

/* ------------------------------------------------------------------ */
/* 公开接口                                                            */
/* ------------------------------------------------------------------ */

/**
 * @brief 启动视觉UART接收
 *        须在 MX_USART2_UART_Init() 和 MX_DMA_Init() 之后调用
 */
void Vision_Init(void)
{
    memset((void *)&vision_target, 0, sizeof(vision_target));
    vision_target.type  = 'X';
    vision_target.valid = 0u;

    HAL_UARTEx_ReceiveToIdle_DMA(&huart2, dma_rx_buf, DMA_RX_BUF_SIZE);
}

/**
 * @brief 向视觉系统发送己方颜色
 * @param color 'b'=蓝方  'y'=黄方
 */
void Vision_SendColor(char color)
{
    /* 命令定界 (2026-05-28): #<color>\n 格式; 上位机只认带定界的命令,
     * 阻塞发送与 debug 统一走阻塞通道, 不抢 DMA gState */
    uint8_t buf[3] = { (uint8_t)'#', (uint8_t)color, (uint8_t)'\n' };
    HAL_UART_Transmit(&huart2, buf, 3u, 10u);
}

/**
 * @brief 向视觉系统发送单字节指令 (以 #<cmd>\n 定界帧发送)
 * @param cmd 指令字符: 'D'=掉台回复模式  'S'=恢复正常检测  'N'=新一轮开始/主控重启
 */
void Vision_SendCmd(char cmd)
{
    uint8_t buf[3] = { (uint8_t)'#', (uint8_t)cmd, (uint8_t)'\n' };
    HAL_UART_Transmit(&huart2, buf, 3u, 10u);
}

/**
 * @brief 检查视觉数据是否超时
 * @return 1=超时或无数据, 0=数据新鲜
 */
uint8_t Vision_IsTimeout(void)
{
    if (vision_target.timestamp == 0u) return 1u;
    return ((HAL_GetTick() - vision_target.timestamp) > VISION_TIMEOUT_MS) ? 1u : 0u;
}

/**
 * @brief 获取接收统计 (供调试输出)
 */
void Vision_GetStats(uint32_t *total, uint32_t *success, uint32_t *cserr)
{
    if (total)   *total   = vision_rx_total;
    if (success) *success = vision_rx_success;
    if (cserr)   *cserr   = vision_rx_cserr;
}

/**
 * @brief 获取DMA重启失败计数 (赛前现场观察; 正常应为0, 非0提示ORE/拆包高发)
 */
uint32_t Vision_GetRestartFails(void)
{
    return vision_rx_restart_fail;
}

/**
 * @brief 原子读取视觉目标快照 (关中断拷贝, 防中断写/主循环读撕裂)
 * @param out 输出快照; NULL 则忽略
 * @note  fight/backup 等读侧应取一致快照, 不要直接多次读 vision_target
 *        (否则 timestamp 与 type 可能跨 USART2 中断撕裂, 烛 2026-05-28 诊断 M3)
 */
void Vision_GetSnapshot(VisionTarget_t *out)
{
    uint32_t primask;

    if (out == NULL) return;

    primask = __get_PRIMASK();
    __disable_irq();
    out->type      = vision_target.type;
    out->cx        = vision_target.cx;
    out->cy        = vision_target.cy;
    out->area      = vision_target.area;
    out->dir       = vision_target.dir;
    out->valid     = vision_target.valid;
    out->timestamp = vision_target.timestamp;
    if (primask == 0u) __enable_irq();
}

/**
 * @brief UART 错误回调: 清错误标志并重启接收, 防 ORE 后接收永久死亡
 * @note  NORMAL 模式回调重启窗口若遇背靠背字节会触发 ORE(overrun);
 *        F407 老款 USART 的 ORE 会阻塞 RXNE/DMA 请求, 必须清标志+重启。
 *        (匠 2026-05-28 诊断 M2: 原先全工程无此回调 = "视觉突然死"头号嫌疑)
 */
void HAL_UART_ErrorCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART2)
    {
        __HAL_UART_CLEAR_OREFLAG(huart);
        __HAL_UART_CLEAR_NEFLAG(huart);
        __HAL_UART_CLEAR_FEFLAG(huart);
        if (HAL_UARTEx_ReceiveToIdle_DMA(&huart2, dma_rx_buf, DMA_RX_BUF_SIZE) != HAL_OK)
        {
            vision_rx_restart_fail++;
        }
    }
    else if (huart->Instance == USART3)
    {
        __HAL_UART_CLEAR_OREFLAG(huart);
        __HAL_UART_CLEAR_NEFLAG(huart);
        __HAL_UART_CLEAR_FEFLAG(huart);
        JY62_RestartRx();
    }
}
