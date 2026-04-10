/*
 * @Author: Xiang xin wang wxinxiang8@gmail.com
 * @Date: 2026-02-01 14:52:47
 * @LastEditors: Xiang xin wang wxinxiang8@gmail.com
 * @LastEditTime: 2026-02-03 14:23:36
 * @FilePath: \MDK-ARMd:\robot fighting\robot\Core\Src\robot_roaming.c
 * @Description: 机器人漫游控制，使用状态机实现非阻塞式控制
 */
#include "robot_roaming.h"
#include "shade.h"
#include "obstacle.h"
#include "motor.h"


typedef enum {
    ROAMING_TURN_DIR_NONE = 0,
    ROAMING_TURN_DIR_LEFT,
    ROAMING_TURN_DIR_RIGHT
} RoamingTurnDir;

static RoamingState Roaming_Stage = ROAMING_FORWARD;
static uint32_t Roaming_StartTime = 0;
static bool Roaming_Done = false;
static RoamingBackReason Roaming_BackReason = BACK_REASON_NONE;
static uint32_t Roaming_TurnDuration = ROAMING_TURN_LEFT_TIME;
static RoamingBackReason Roaming_PendingBackReason = BACK_REASON_NONE;
static uint8_t Roaming_EdgeCount = 0;
static uint8_t Roaming_ShadeCount = 0;
static RoamingTurnDir Roaming_LastTurnDir = ROAMING_TURN_DIR_NONE;
static RoamingTurnDir Roaming_BothTurnDir = ROAMING_TURN_DIR_RIGHT;

/**
 * @description: 检测是否掉落擂台（V0/V1 任一超阈值判定掉台）
 * @param void
 * @return int 1=掉落擂台, 0=在擂台上
 */
static int detect_shade(void)
{
    site_detect_shade();//read shade sensor data

    if(voltage_v0 > 2.8f || voltage_v1 > 2.8f)
    {
        if(Roaming_ShadeCount < ROAMING_SHADE_CONFIRM_COUNT)
        {
            Roaming_ShadeCount++;
        }
    }
    else
    {
        Roaming_ShadeCount = 0;
    }

    return (Roaming_ShadeCount >= ROAMING_SHADE_CONFIRM_COUNT);
}

/**
 * @description: 初始化漫游状态机
 * @param void
 * @return void
 */
void Roaming_Init(void)
{
    Shade_Sensor_Init();
    Roaming_Stage = ROAMING_FORWARD;
    Roaming_StartTime = HAL_GetTick();
    Roaming_Done = false;
    Roaming_BackReason = BACK_REASON_NONE;
    Roaming_PendingBackReason = BACK_REASON_NONE;
    Roaming_EdgeCount = 0;
    Roaming_ShadeCount = 0;
    Roaming_LastTurnDir = ROAMING_TURN_DIR_NONE;
    Roaming_BothTurnDir = ROAMING_TURN_DIR_RIGHT;
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

    // 非前进态下保持原有灰度掉台保护
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
            // 先读边缘传感器，优先处理悬崖
            Obs_Sensor_ReadAll();
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
                Roaming_EdgeCount = 0;

                // 无边缘预警时，再做灰度掉台判断
                if(detect_shade())
                {
                    Roaming_Stage = ROAMING_DONE;
                    Roaming_Done = true;
                    MOTOR_BrakeAll();
                    break;
                }

                // 若仍处于边缘预刹车阶段，则先不恢复前进，避免覆盖刹车输出
                if(MOTOR_IsBraking())
                {
                    break;
                }

                // 仅在无边缘预警且刹车已结束时继续前进
                drive_For_Roaming();
            }
            else
            {
                // 边缘预警期间清掉灰度累计，避免灰度抢先进入掉台
                Roaming_ShadeCount = 0;
                if(current_reason != Roaming_PendingBackReason)
                {
                    Roaming_PendingBackReason = current_reason;
                    Roaming_EdgeCount = 1;
                    MOTOR_BrakeAllRelease();
                }
                else
                {
                    Roaming_EdgeCount++;
                    if(Roaming_EdgeCount >= ROAMING_EDGE_CONFIRM_COUNT)
                    {
                        Roaming_BackReason = current_reason;
                        Roaming_Stage = ROAMING_EDGE_STOP;
                        Roaming_StartTime = current_time;
                        Roaming_PendingBackReason = BACK_REASON_NONE;
                        Roaming_EdgeCount = 0;
                    }
                }
            }
            break;

        case ROAMING_EDGE_STOP:
            if(!MOTOR_IsBraking() && elapsed_time >= ROAMING_EDGE_STOP_TIME)
            {
                Roaming_Stage = ROAMING_BACK;
                Roaming_StartTime = current_time;
            }
            break;

        case ROAMING_BACK:
            // 后退状态
            drive_user_defined(-450, -450);
            
            if(elapsed_time >= ROAMING_BACK_TIME)
            {
                // 后退完成，根据触发后退时锁存的原因决定转向
                if(Roaming_BackReason == BACK_REASON_BOTH)
                {
                    Roaming_Stage = ROAMING_TURN_BOTH;
                    Roaming_TurnDuration = ROAMING_BACKAND_TURN_TIME;
                    if(Roaming_LastTurnDir == ROAMING_TURN_DIR_RIGHT)
                    {
                        Roaming_BothTurnDir = ROAMING_TURN_DIR_LEFT;
                    }
                    else
                    {
                        Roaming_BothTurnDir = ROAMING_TURN_DIR_RIGHT;
                    }
                    Roaming_LastTurnDir = Roaming_BothTurnDir;
                }
                else if(Roaming_BackReason == BACK_REASON_LEFT)
                {
                    Roaming_Stage = ROAMING_TURN_LEFT;
                    Roaming_TurnDuration = ROAMING_TURN_LEFT_TIME;
                    Roaming_LastTurnDir = ROAMING_TURN_DIR_LEFT;
                }
                else if(Roaming_BackReason == BACK_REASON_RIGHT)
                {
                    Roaming_Stage = ROAMING_TURN_RIGHT;
                    Roaming_TurnDuration = ROAMING_TURN_RIGHT_TIME;
                    Roaming_LastTurnDir = ROAMING_TURN_DIR_RIGHT;
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
            // 左转状态
            drive_user_defined(-400, 400);
            
            if(elapsed_time >= Roaming_TurnDuration)
            {
                // 转向完成，继续前进
                Roaming_Stage = ROAMING_FORWARD;
                Roaming_StartTime = current_time;
            }
            break;
            
        case ROAMING_TURN_RIGHT:
            // 右转状态
            drive_user_defined(400, -400);
            
            if(elapsed_time >= Roaming_TurnDuration)
            {
                // 转向完成，继续前进
                Roaming_Stage = ROAMING_FORWARD;
                Roaming_StartTime = current_time;
            }
            break;

        case ROAMING_TURN_BOTH:
            if(Roaming_BothTurnDir == ROAMING_TURN_DIR_LEFT)
            {
                drive_user_defined(-400, 400);
            }
            else
            {
                drive_user_defined(400, -400);
            }

            if(elapsed_time >= Roaming_TurnDuration)
            {
                // 转向完成，继续前进
                Roaming_Stage = ROAMING_FORWARD;
                Roaming_StartTime = current_time;
            }
            break;
            
        case ROAMING_DONE:
            // 完成状态（掉落擂台）
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

