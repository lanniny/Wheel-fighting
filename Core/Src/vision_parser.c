/**
 * @file    vision_parser.c
 * @brief   视觉系统UART解析模块
 *
 * 协议格式: $type,cx,cy,area,dir*CS\n
 *   type: E=敌方 N=中立 F=友方 X=无目标 B=炸弹
 *   CS:   body字段的异或校验和(十六进制, 2位)
 *   示例: $E,320,240,5000,+25*4A\n
 *
 * 实现方案: USART2 + DMA循环接收 + IDLE行中断
 *   - HAL_UARTEx_ReceiveToIdle_DMA 自动利用IDLE中断触发回调
 *   - 每帧回调后立即重启DMA, 保证连续接收
 */
#include "vision_parser.h"
#include "usart.h"
#include <string.h>
#include <stdlib.h>
#include <stdio.h>

/* ---- 内部常量 ---- */
#define DMA_RX_BUF_SIZE   256u   /* DMA接收缓冲区大小 */
#define VISION_TIMEOUT_MS 200u   /* 数据超时阈值(ms) */

/* ---- 全局目标数据 ---- */
volatile VisionTarget_t vision_target = { .type = 'X', .valid = 0u };

/* ---- 接收统计 ---- */
static uint32_t vision_rx_total   = 0u;  /* DMA回调触发次数 */
static uint32_t vision_rx_success = 0u;  /* 成功解析帧数 */
static uint32_t vision_rx_cserr   = 0u;  /* 校验和错误次数 */

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
/* 内部函数: 解析单帧  $type,cx,cy,area,dir*CS\n                       */
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

    /* 解析字段: type,cx,cy,area,dir */
    char body_copy[48];
    memcpy(body_copy, body, (size_t)body_len);
    body_copy[body_len] = '\0';

    char type_ch = 'X';
    int  cx = 0, cy = 0, area = 0, dir = 0;
    int  n = sscanf(body_copy, "%c,%d,%d,%d,%d",
                    &type_ch, &cx, &cy, &area, &dir);
    if (n != 5) return;

    /* 写入全局变量: 先置 valid=0 屏蔽旧数据, 写完字段后再置 valid
     * 防止主循环读到 "旧cx + 新area" 的不一致状态 */
    vision_target.valid     = 0u;
    vision_target.type      = type_ch;
    vision_target.cx        = (int16_t)cx;
    vision_target.cy        = (int16_t)cy;
    vision_target.area      = (int32_t)area;
    vision_target.dir       = (int8_t)((dir < -100) ? -100 : ((dir > 100) ? 100 : dir));
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
    if (huart->Instance != USART2) return;

    vision_rx_total++;

    /* 多帧解析: 处理缓冲区中所有完整帧 */
    if (Size > 0u) {
        parse_buffer(dma_rx_buf, Size);
    }

    /* 立即重启DMA接收, 准备下一帧 */
    HAL_UARTEx_ReceiveToIdle_DMA(&huart2, dma_rx_buf, DMA_RX_BUF_SIZE);

    /* 禁用半传输中断, 防止在缓冲区半满时误触发回调 */
    __HAL_DMA_DISABLE_IT(huart2.hdmarx, DMA_IT_HT);
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
    __HAL_DMA_DISABLE_IT(huart2.hdmarx, DMA_IT_HT);
}

/**
 * @brief 向视觉系统发送己方颜色
 * @param color 'b'=蓝方  'y'=黄方
 */
void Vision_SendColor(char color)
{
    uint8_t ch = (uint8_t)color;
    HAL_UART_Transmit(&huart2, &ch, 1u, 10u);
}

/**
 * @brief 向视觉系统发送单字节指令
 * @param cmd 指令字符: 'D'=掉台回复模式  'S'=恢复正常检测
 */
void Vision_SendCmd(char cmd)
{
    uint8_t ch = (uint8_t)cmd;
    HAL_UART_Transmit(&huart2, &ch, 1u, 10u);
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
