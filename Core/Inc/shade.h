/*
 * @Author: Xiang xin wang wxinxiang8@gmail.com
 * @Date: 2026-01-22 14:05:37
 * @LastEditors: Xiang xin wang wxinxiang8@gmail.com
 * @LastEditTime: 2026-01-22 18:24:17
 * @FilePath: \MDK-ARMd:\robot fighting\robot\Core\Inc\shade.h
 * @Description: 这是默认设置,请设置`customMade`, 打开koroFileHeader查看配置 进行设置: https://github.com/OBKoro1/koro1FileHeader/wiki/%E9%85%8D%E7%BD%AE
 */
#ifndef SHADE_H
#define SHADE_H

#include "main.h"

extern uint16_t shade[2];//adc value
extern float voltage[2];//voltage value
extern float voltage_filtered[2];//filtered voltage (sliding avg)

/* 掉台检测统一阈值 (shared across fight/roaming) */
#define SHADE_DOWN_THRESHOLD  2.85f  /* 滤波值阈值 */
#define SHADE_DOWN_CONFIRM    5      /* 连续确认次数 (50ms@10ms loop) */
#define SHADE_RAW_EMERGENCY   3.10f  /* 原始值紧急阈值: 跳过滤波直接确认 */

void Shade_Sensor_Init(void);
void site_detect_shade(void);

#endif // SHADE_H