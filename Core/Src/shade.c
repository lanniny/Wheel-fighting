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

static uint16_t shade_buf[2];
static uint8_t shade_test_inject_enable = 0;
static uint16_t shade_test_v0 = 0;
static uint16_t shade_test_v1 = 0;
uint16_t shade_v0;//adc value
uint16_t shade_v1;//adc value
float voltage_v0;//voltage value
float voltage_v1;//voltage value

void Shade_Sensor_Init(void)
{
    HAL_ADC_Start_DMA(&hadc2,(uint32_t*)shade_buf,2);//start adc2 dma
}

void site_detect_shade()
{
    if (shade_test_inject_enable)
    {
        shade_v0 = shade_test_v0;
        shade_v1 = shade_test_v1;
    }
    else
    {
        shade_v0 = shade_buf[0];
        shade_v1 = shade_buf[1];
    }

    voltage_v0 = (float)(shade_v0 * 3.3f) / 4095.0f;//convert adc value to voltage value
    voltage_v1 = (float)(shade_v1 * 3.3f) / 4095.0f;//convert adc value to voltage value
}

void Shade_TestInject_Enable(uint16_t v0_adc, uint16_t v1_adc)
{
    shade_test_inject_enable = 1;
    shade_test_v0 = v0_adc;
    shade_test_v1 = v1_adc;
}

void Shade_TestInject_Disable(void)
{
    shade_test_inject_enable = 0;
}