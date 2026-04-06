/*
 * @Author: Xiang xin wang wxinxiang8@gmail.com
 * @Date: 2026-01-22 15:56:26
 * @LastEditors: Xiang xin wang wxinxiang8@gmail.com
 * @LastEditTime: 2026-01-29 15:29:12
 * @FilePath: \MDK-ARMd:\robot fighting\robot\Core\Src\shade.c
 * @Description: 这是默认设置,请设置`customMade`, 打开koroFileHeader查看配置 进行设置: https://github.com/OBKoro1/koro1FileHeader/wiki/%E9%85%8D%E7%BD%AE
 */
#include "shade.h"
#include "adc.h"
#include "dma.h"

// MX_ADC2_Init();
// MX_DMA_Init(); // Do not call init functions at global scope. They are called in main.c

uint16_t shade[2];//adc value
float voltage[2];//voltage value

#define SHADE_FILTER_SIZE 8

float voltage_filtered[2] = {0.0f};
static float voltage_history[2][SHADE_FILTER_SIZE] = {{0}};
static uint8_t shade_history_idx = 0;

void Shade_Sensor_Init(void)
{
    HAL_ADC_Start_DMA(&hadc2,(uint32_t*)shade,2);//start adc2 dma

    /* initialize filter history to safe values (on-platform) */
    for(int i = 0; i < 2; i++)
        for(int j = 0; j < SHADE_FILTER_SIZE; j++)
            voltage_history[i][j] = 0.0f;
    shade_history_idx = 0;
    voltage_filtered[0] = 0.0f;
    voltage_filtered[1] = 0.0f;
}

void site_detect_shade()
{
    for(int i = 0; i < 2; i++)
    {
        float raw = (float)(shade[i] * 3.3f) / 4095.0f;
        voltage[i] = raw;

        /* sliding-window average filter */
        voltage_history[i][shade_history_idx] = raw;
        float sum = 0.0f;
        for(int j = 0; j < SHADE_FILTER_SIZE; j++)
            sum += voltage_history[i][j];
        voltage_filtered[i] = sum / SHADE_FILTER_SIZE;
    }
    shade_history_idx = (shade_history_idx + 1) % SHADE_FILTER_SIZE;
}