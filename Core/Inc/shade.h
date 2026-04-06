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

void Shade_Sensor_Init(void);
void site_detect_shade(void);

#endif // SHADE_H