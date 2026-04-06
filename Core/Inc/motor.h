/*
 * @Author: Xiang xin wang wxinxiang8@gmail.com
 * @Date: 2026-03-14 14:24:43
 * @LastEditors: Xiang xin wang wxinxiang8@gmail.com
 * @LastEditTime: 2026-03-15 16:14:08
 * @FilePath: \MDK-ARMd:\robot fighting\robot\Core\Inc\motor.h
 * @Description: 这是默认设置,请设置`customMade`, 打开koroFileHeader查看配置 进行设置: https://github.com/OBKoro1/koro1FileHeader/wiki/%E9%85%8D%E7%BD%AE
 */
#ifndef __MOTOR_H__
#define __MOTOR_H__
#include <stdint.h>

// PWM配置
#define PWM_MAX_VALUE       4199        // PWM最大值(对应定时器ARR)
#define SPEED_MAX_INPUT     1000        // 速度输入最大值

// 预定义速度等级
#define SPEED_LOW           300         // 低速(30%)
#define SPEED_MEDIUM        600         // 中速(60%)
#define SPEED_HIGH          800         // 高速(90%)

// 转向速度
#define SPEED_TURN_M        400         // 中等转向速度(40%)
#define SPEED_TURN_S        600         // 快速转向速度(60%)

// 弧线转向速度
#define SPEED_ARC_INNER_M   300         // 中速弧线内侧(20%)
#define SPEED_ARC_OUTER_M   500         // 中速弧线外侧(50%)

typedef enum{
    //left_f
    MOTOR_1=1,//PD12,PD13
    //left_b
    MOTOR_2=2,//PD12,PD13
    //right_f
    MOTOR_3=3,//PD14,PD15
    //right_b
    MOTOR_4=4 //PD14,PD15
}MOTOR_ID;

void MOTOR_Init(void);
void MOTOR_SetSpeed(MOTOR_ID motor_id, int16_t speed);

void drive_For_L(void);//前进(低中高)
void drive_For_M(void);
void drive_For_H(void);

void drive_Back_L(void);//后退(低中高)
void drive_Back_M(void);
void drive_Back_H(void);

void drive_Left_M(void);//微左右转
void drive_Right_M(void);

void drive_Left_S(void);//超级左右转
void drive_Right_S(void);

void drive_Retreat_L(void);//后退左转
void drive_Retreat_R(void);//后退右转

void drive_user_defined(int16_t left_speed, int16_t right_speed);//用户自定义速度控制

void drive_ArcLeft_M(void);    // 中速弧线左 (ENGAGE前侧追踪用)
void drive_ArcRight_M(void);   // 中速弧线右

void MOTOR_StopAll(void);
void MOTOR_Brake(MOTOR_ID motor_id);
void MOTOR_BrakeAll(void);

#endif