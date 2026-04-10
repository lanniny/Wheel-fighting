/*
 * @Author: Xiang xin wang wxinxiang8@gmail.com
 * @Date: 2026-02-01 14:53:13
 * @LastEditors: Xiang xin wang wxinxiang8@gmail.com
 * @LastEditTime: 2026-03-24 21:04:36
 * @FilePath: \MDK-ARMd:\robot fighting\robot\Core\Src\obstacle.c
 * @Description: 这是默认设置,请设置`customMade`, 打开koroFileHeader查看配置 进行设置: https://github.com/OBKoro1/koro1FileHeader/wiki/%E9%85%8D%E7%BD%AE
 */
#include "obstacle.h"
#include "gpio.h"

// 定义全局变量实例
Obs_Sensors_t Obs_Data = {0};

static GPIO_PinState invert_state(GPIO_PinState state)
{
  return (state == GPIO_PIN_SET) ? GPIO_PIN_RESET : GPIO_PIN_SET;
}

/**
  * @brief  读取所有红外传感器状态
  *         建议在主循环或定时器中调用
  */
void Obs_Sensor_ReadAll(void)
{
  // 读取 GPIOE 上的 IR_1 到 IR_8，以及 GPIOA 上的 IR_9、IR_10
    
    Obs_Data.IR1 = invert_state(HAL_GPIO_ReadPin(IR_1_GPIO_Port, IR_1_Pin));
    Obs_Data.IR2 = HAL_GPIO_ReadPin(IR_2_GPIO_Port, IR_2_Pin);
    Obs_Data.IR3 = HAL_GPIO_ReadPin(IR_3_GPIO_Port, IR_3_Pin);
    Obs_Data.IR4 = HAL_GPIO_ReadPin(IR_4_GPIO_Port, IR_4_Pin);
    Obs_Data.IR5 = (HAL_GPIO_ReadPin(IR_5_GPIO_Port, IR_5_Pin));
    Obs_Data.IR6 = invert_state(HAL_GPIO_ReadPin(IR_6_GPIO_Port, IR_6_Pin));
    Obs_Data.IR7 = HAL_GPIO_ReadPin(IR_7_GPIO_Port, IR_7_Pin);
    Obs_Data.IR8 = HAL_GPIO_ReadPin(IR_8_GPIO_Port, IR_8_Pin);
    Obs_Data.IR9 = HAL_GPIO_ReadPin(IR_9_GPIO_Port, IR_9_Pin);
    Obs_Data.IR10 = invert_state(HAL_GPIO_ReadPin(IR_10_GPIO_Port, IR_10_Pin));
}
/**
  * @brief  读取边缘传感器状态
  *         建议在主循环或定时器中调用
  */
void Edge_Sensor_Detect(void)
{
    Obs_Data.IR1 = invert_state(HAL_GPIO_ReadPin(IR_1_GPIO_Port, IR_1_Pin));
    Obs_Data.IR2 = HAL_GPIO_ReadPin(IR_2_GPIO_Port, IR_2_Pin);
}
/**
  * @brief  读取周围红外传感器状态
  *         建议在主循环或定时器中调用
  */
void Enmy_Sensor_Detect(void)
{
    Obs_Data.IR3 = HAL_GPIO_ReadPin(IR_3_GPIO_Port, IR_3_Pin);
    Obs_Data.IR4 = HAL_GPIO_ReadPin(IR_4_GPIO_Port, IR_4_Pin);
    Obs_Data.IR5 = HAL_GPIO_ReadPin(IR_5_GPIO_Port, IR_5_Pin);
    Obs_Data.IR6 = invert_state(HAL_GPIO_ReadPin(IR_6_GPIO_Port, IR_6_Pin));
    Obs_Data.IR7 = HAL_GPIO_ReadPin(IR_7_GPIO_Port, IR_7_Pin);
    Obs_Data.IR8 = HAL_GPIO_ReadPin(IR_8_GPIO_Port, IR_8_Pin);
    Obs_Data.IR9 = HAL_GPIO_ReadPin(IR_9_GPIO_Port, IR_9_Pin);
    Obs_Data.IR10 = invert_state(HAL_GPIO_ReadPin(IR_10_GPIO_Port, IR_10_Pin));
}
