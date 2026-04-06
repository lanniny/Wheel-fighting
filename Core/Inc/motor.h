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
#define SPEED_ROAMING       350         // 漫游专用速度(35%)

// 转向速度
#define SPEED_TURN_L        300         // 慢转向速度(30%)
#define SPEED_TURN_M        400         // 中等转向速度(40%)
#define SPEED_TURN_R        400         // 中转向(兼容远程命名)
#define SPEED_TURN_S        600         // 快速转向速度(60%)

// 弧线转向速度
#define SPEED_ARC_INNER_M   300         // 中速弧线内侧(30%)
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

void drive_For_L(void);
void drive_For_M(void);
void drive_For_H(void);
void drive_For_Roaming(void);

void drive_Back_L(void);
void drive_Back_M(void);
void drive_Back_H(void);

void drive_Left_L(void);     // 慢左转
void drive_Right_L(void);    // 慢右转
void drive_Left_M(void);     // 中左转
void drive_Right_M(void);    // 中右转
void drive_Left_S(void);     // 快左转
void drive_Right_S(void);    // 快右转

void drive_Left_Roaming(void);   // 漫游左转
void drive_Right_Roaming(void);  // 漫游右转

void drive_Retreat_L(void);
void drive_Retreat_R(void);

void drive_user_defined(int16_t left_speed, int16_t right_speed);

void drive_ArcLeft_M(void);
void drive_ArcRight_M(void);

void MOTOR_StopAll(void);
void MOTOR_Brake(MOTOR_ID motor_id);
void MOTOR_BrakeAll(void);

#endif