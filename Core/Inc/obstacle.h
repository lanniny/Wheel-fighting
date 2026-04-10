#ifndef OBSTACLE_H
#define OBSTACLE_H

#include "main.h"

// 定义传感器数据结构体
typedef struct {
    GPIO_PinState IR1; // 传感器1状态 (0或1)
    GPIO_PinState IR2; // 传感器2状态
    GPIO_PinState IR3; // 传感器3状态
    GPIO_PinState IR4; // 传感器4状态
    GPIO_PinState IR5; // 传感器5状态
    GPIO_PinState IR6; // 传感器6状态
    GPIO_PinState IR7; // 传感器7状态
    GPIO_PinState IR8; // 传感器8状态
    GPIO_PinState IR9; // 传感器9状态
    GPIO_PinState IR10; // 传感器10状态
    
    // 如果需要，可以添加组合状态，例如:
    // uint8_t AllStatus; // 8位位掩码，每一位代表一个传感器
}Obs_Sensors_t;

// 声明全局变量，供其他文件访问
extern Obs_Sensors_t Obs_Data;

// 函数声明
void Obs_Sensor_ReadAll(void);
void Edge_Sensor_Detect(void);
void Enmy_Sensor_Detect(void);


#endif // OBSTACLE_H
