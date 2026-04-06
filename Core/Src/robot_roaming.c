/*
 * @Description: 机器人漫游控制 — 状态机 + 灰度滤波消抖 + 边缘互斥 + 左右转分别计时
 */
#include "robot_roaming.h"
#include "shade.h"
#include "obstacle.h"
#include "motor.h"

static RoamingState Roaming_Stage = ROAMING_FORWARD;
static uint32_t Roaming_StartTime = 0;
static bool Roaming_Done = false;
static RoamingBackReason Roaming_BackReason = BACK_REASON_NONE;
static uint32_t Roaming_TurnTimeL = ROAMING_TURN_LEFT_TIME;
static uint32_t Roaming_TurnTimeR = ROAMING_TURN_RIGHT_TIME;
static uint32_t Roaming_TurnTimeB = ROAMING_BACKAND_TURN_TIME;
static RoamingBackReason Roaming_PendingBackReason = BACK_REASON_NONE;
static uint32_t Roaming_BackDebounceStart = 0;

/* 灰度掉台消抖 (阈值定义在 shade.h) */
static uint8_t Roaming_ShadeDownCount = 0;

/**
 * @description: 检测是否掉落擂台
 *   正常路径: 滤波+消抖
 *   紧急路径: 原始值极高, 零延迟确认
 */
static int detect_shade(void)
{
    site_detect_shade();

    /* 紧急快速通道: 原始值极高 = 确定掉台 */
    if(voltage[0] > SHADE_RAW_EMERGENCY
       && voltage[1] > SHADE_RAW_EMERGENCY)
    {
        return 1;
    }

    /* 正常路径: 滤波值 + 连续确认 */
    if(voltage_filtered[0] > SHADE_DOWN_THRESHOLD
       && voltage_filtered[1] > SHADE_DOWN_THRESHOLD)
    {
        Roaming_ShadeDownCount++;
        return (Roaming_ShadeDownCount >= SHADE_DOWN_CONFIRM) ? 1 : 0;
    }
    else
    {
        Roaming_ShadeDownCount = 0;
        return 0;
    }
}

void Roaming_Init(void)
{
    Shade_Sensor_Init();
    Roaming_Stage = ROAMING_FORWARD;
    Roaming_StartTime = HAL_GetTick();
    Roaming_Done = false;
    Roaming_BackReason = BACK_REASON_NONE;
    Roaming_PendingBackReason = BACK_REASON_NONE;
    Roaming_BackDebounceStart = 0;
    Roaming_ShadeDownCount = 0;
}

/**
 * @description: 更新漫游状态机，根据传感器和时间执行相应动作
 * @param void
 * @return void
 */
void Roaming_Update(void)
{
    uint32_t current_time = HAL_GetTick();
    uint32_t elapsed_time = current_time - Roaming_StartTime;

    /* 非前进态下保持灰度掉台保护 */
    if(Roaming_Stage != ROAMING_FORWARD && detect_shade())
    {
        Roaming_Stage = ROAMING_DONE;
        Roaming_Done = true;
        MOTOR_BrakeAll();
        return;
    }

    RoamingBackReason current_reason = BACK_REASON_NONE;

    switch(Roaming_Stage)
    {
        case ROAMING_FORWARD:
            drive_For_L();

            /* 先读边缘传感器, 优先处理悬崖 */
            Obs_Sensor_ReadAll();

            current_reason = BACK_REASON_NONE;
            if(Obs_Data.IR1 == SET && Obs_Data.IR2 == SET)
            {
                current_reason = BACK_REASON_BOTH;
            }
            else if(Obs_Data.IR1 == SET && Obs_Data.IR2 == RESET)
            {
                current_reason = BACK_REASON_RIGHT;
            }
            else if(Obs_Data.IR1 == RESET && Obs_Data.IR2 == SET)
            {
                current_reason = BACK_REASON_LEFT;
            }

            if(current_reason == BACK_REASON_NONE)
            {
                Roaming_PendingBackReason = BACK_REASON_NONE;
                Roaming_BackDebounceStart = 0;

                /* 无边缘预警时, 再做灰度掉台判断 */
                if(detect_shade())
                {
                    Roaming_Stage = ROAMING_DONE;
                    Roaming_Done = true;
                    MOTOR_BrakeAll();
                    break;
                }
            }
            else
            {
                /* 边缘预警期间清掉灰度累计, 避免灰度抢先进入掉台 */
                Roaming_ShadeDownCount = 0;

                if(current_reason != Roaming_PendingBackReason)
                {
                    Roaming_PendingBackReason = current_reason;
                    Roaming_BackDebounceStart = current_time;
                }
                else if((current_time - Roaming_BackDebounceStart) >= ROAMING_EDGE_DEBOUNCE_MS)
                {
                    MOTOR_BrakeAll();
                    Roaming_BackReason = current_reason;
                    Roaming_Stage = ROAMING_BACK;
                    Roaming_StartTime = current_time;
                    Roaming_PendingBackReason = BACK_REASON_NONE;
                    Roaming_BackDebounceStart = 0;
                }
            }
            break;

        case ROAMING_BACK:
            drive_Back_L();

            if(elapsed_time >= ROAMING_BACK_TIME)
            {
                /* 后退完成, 根据锁存原因决定转向 */
                if(Roaming_BackReason == BACK_REASON_BOTH)
                {
                    Roaming_Stage = ROAMING_TURN_BOTH;
                    Roaming_TurnTimeB = ROAMING_BACKAND_TURN_TIME;
                }
                else if(Roaming_BackReason == BACK_REASON_LEFT)
                {
                    Roaming_Stage = ROAMING_TURN_LEFT;
                    Roaming_TurnTimeL = ROAMING_TURN_LEFT_TIME;
                }
                else if(Roaming_BackReason == BACK_REASON_RIGHT)
                {
                    Roaming_Stage = ROAMING_TURN_RIGHT;
                    Roaming_TurnTimeR = ROAMING_TURN_RIGHT_TIME;
                }
                else
                {
                    Roaming_Stage = ROAMING_FORWARD;
                }
                Roaming_BackReason = BACK_REASON_NONE;
                Roaming_StartTime = current_time;
            }
            break;

        case ROAMING_TURN_LEFT:
            drive_Left_M();
            if(elapsed_time >= Roaming_TurnTimeL)
            {
                Roaming_Stage = ROAMING_FORWARD;
                Roaming_StartTime = current_time;
            }
            break;

        case ROAMING_TURN_RIGHT:
            drive_Right_M();
            if(elapsed_time >= Roaming_TurnTimeR)
            {
                Roaming_Stage = ROAMING_FORWARD;
                Roaming_StartTime = current_time;
            }
            break;

        case ROAMING_TURN_BOTH:
            drive_Right_M();
            if(elapsed_time >= Roaming_TurnTimeB)
            {
                Roaming_Stage = ROAMING_FORWARD;
                Roaming_StartTime = current_time;
            }
            break;

        case ROAMING_DONE:
            MOTOR_StopAll();
            Roaming_Done = true;
            break;
    }
}

/**
 * @description: 返回漫游任务是否完成（掉落擂台）
 * @param void
 * @return bool
 */
bool Roaming_IsDone(void)
{
    return Roaming_Done;
}

/**
 * @description: 当前是否处于前进状态 (仅前进时允许切入格斗, 避免边缘回避被打断)
 */
bool Roaming_IsForward(void)
{
    return (Roaming_Stage == ROAMING_FORWARD);
}
