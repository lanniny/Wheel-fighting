#ifndef __MOTOR_H__
#define __MOTOR_H__
#include <stdint.h>
#include <stdbool.h>

// PWM配置
#define PWM_MAX_VALUE       4199        // PWM最大值(对应定时器ARR)
#define SPEED_MAX_INPUT     1000        // 速度输入最大值

// 制动参数
#define BRAKE_PULSE_SPEED   500         // 一级反向制动速度
#define BRAKE_PULSE_MS      20          // 一级反向制动时间(ms)
#define BRAKE_HOLD_SPEED    250         // 二级反向拖刹速度
#define BRAKE_HOLD_MS       10          // 二级反向拖刹时间(ms)

// 预定义速度等级
#define SPEED_LOW           300         // 低速(30%)
#define SPEED_MEDIUM        400         // 中速(40%)
#define SPEED_HIGH          800         // 高速(80%)
#define SPEED_ROAMING       400         // 漫游速度(40%)

// 斜坡平滑参数
#define RAMP_STEP           250         // 每10ms最大PWM变化量

// 转向速度
#define SPEED_TURN_L        200         // 慢转向速度(20%)
#define SPEED_TURN_M        400         // 中等转向速度(40%)
#define SPEED_TURN_S        600         // 快速转向速度(60%)
#define SPEED_TURN_R        400         // 漫游转向速度(40%)


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
void drive_For_Roaming(void);

void drive_Back_L(void);//后退(低中高)
void drive_Back_M(void);
void drive_Back_H(void);

void drive_Left_L(void);//慢左右转
void drive_Right_L(void);

void drive_Left_M(void);//微左右转
void drive_Right_M(void);

void drive_Left_S(void);//超级左右转
void drive_Right_S(void);

void drive_Retreat_L(void);//后退左转
void drive_Retreat_R(void);//后退右转

void drive_user_defined(int16_t left_speed, int16_t right_speed);//用户自定义速度控制

void MOTOR_StopAll(void);
void MOTOR_BrakeAll(void);          // 主动电磁制动(安全场景用，非阻塞，结束后停机)
void MOTOR_BrakeAllRelease(void);   // 主动电磁制动(边缘场景用，非阻塞，结束后不主动停机)
bool MOTOR_IsBraking(void);         // 查询当前是否仍在制动阶段
void MOTOR_Service(void);           // 电机后台服务（处理非阻塞制动）

void Motor_Ramp_SetTarget(int16_t left, int16_t right);
void Motor_Ramp_Update(void);
void Motor_Ramp_ForceStop(void);
void Motor_Ramp_SyncFromCurrent(void);

#endif