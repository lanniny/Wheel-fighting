#ifndef __VISION_PARSER_H
#define __VISION_PARSER_H

#include "stm32f4xx_hal.h"
#include <stdint.h>

/* 视觉系统发给STM32的目标数据 */
typedef struct {
    char     type;        /* 目标类型: 'E'敌方 'N'中立 'F'友方 'X'无目标 'B'炸弹 'G'冲台 */
    int16_t  cx;          /* 目标中心x [0-640] */
    int16_t  cy;          /* 目标中心y [0-480] */
    int32_t  area;        /* 目标面积 (像素) */
    int8_t   dir;         /* 方向偏移 [-100,+100], 负=左 正=右 */
    uint8_t  valid;       /* 1=数据有效且未超时 */
    uint32_t timestamp;   /* HAL_GetTick() 时间戳 */
} VisionTarget_t;

/* 最新视觉目标 (全局共享, 中断更新) */
extern volatile VisionTarget_t vision_target;

/**
 * @brief 初始化视觉串口接收 (DMA + IDLE中断)
 *        在所有外设初始化完成后调用
 */
void Vision_Init(void);

/**
 * @brief 发送己方颜色给视觉系统
 * @param color 'b'=蓝方  'y'=黄方
 */
void Vision_SendColor(char color);

/**
 * @brief 发送单字节指令给视觉系统
 * @param cmd 指令字符: 'D'=掉台回复模式  'S'=恢复正常检测  等
 */
void Vision_SendCmd(char cmd);

/**
 * @brief 检查视觉数据是否超时 (200ms无新数据视为超时)
 * @return 1=超时/无数据, 0=数据有效
 */
uint8_t Vision_IsTimeout(void);

/**
 * @brief 获取接收统计 (供调试输出, 参数为NULL则跳过)
 * @param total   DMA回调触发次数
 * @param success 成功解析帧数
 * @param cserr   校验和错误次数
 */
void Vision_GetStats(uint32_t *total, uint32_t *success, uint32_t *cserr);

#endif /* __VISION_PARSER_H */
