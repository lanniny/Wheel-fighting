#include "robot_fight.h"
#include "motor.h"
#include "obstacle.h"
#include "shade.h"
#include "vision_parser.h"

static FightState Fight_State = FIGHT_ENGAGE;        // 当前进攻状态机状态
static uint32_t Fight_StartTime = 0;                 // 当前状态起始时间戳
static bool Fight_DoneFlag = false;                  // 进攻阶段完成标志（通知总控回漫游）
static uint32_t Fight_EngageLost = 0;                // 交战态中丢失目标的起始时间
static EnemyDir Fight_PrevRawDir = DIR_NONE;         // 上一拍原始敌人方向（用于方向消抖）
static EnemyDir Fight_StableDir  = DIR_NONE;         // 消抖后的稳定敌人方向
static uint8_t Fight_EdgeCount = 0;                  // 边缘检测确认计数
static char Fight_PrevVisionType = 'X';              // 上一拍视觉类型（用于视觉类型消抖）
static char Fight_StableVisionType = 'X';            // 消抖后的稳定视觉类型
static uint8_t Fight_VisionTypeCount = 0;            // 视觉类型连续命中计数
static uint8_t Fight_ShadeCount = 0;                 // 灰度掉台确认计数
static bool Fight_DownFlag = false;                  // 掉台标志（通知总控切BACKUP）
static EnemyDir Fight_TrackDir = DIR_NONE;           // 侧后追踪当前方向

/*======传感器读取======*/

/**
 * @description: 获取敌人方向
 * @param void
 * @return EnemyDir
 */
EnemyDir Fight_GetEnemyDir(void)
{
    /*读取八路光电传感器*/
    uint8_t nw    = (HAL_GPIO_ReadPin(FIGHT_IR_NW_PORT,    FIGHT_IR_NW_PIN)    == FIGHT_IR_TRIGGERED);
    uint8_t ne    = (HAL_GPIO_ReadPin(FIGHT_IR_NE_PORT,    FIGHT_IR_NE_PIN)    == FIGHT_IR_TRIGGERED);
    uint8_t l     = (HAL_GPIO_ReadPin(FIGHT_IR_L_PORT,     FIGHT_IR_L_PIN)     != FIGHT_IR_TRIGGERED);
    uint8_t r     = (HAL_GPIO_ReadPin(FIGHT_IR_R_PORT,     FIGHT_IR_R_PIN)     != FIGHT_IR_TRIGGERED);
    uint8_t sw    = (HAL_GPIO_ReadPin(FIGHT_IR_SW_PORT,    FIGHT_IR_SW_PIN)    == FIGHT_IR_TRIGGERED);
    uint8_t se    = (HAL_GPIO_ReadPin(FIGHT_IR_SE_PORT,    FIGHT_IR_SE_PIN)    == FIGHT_IR_TRIGGERED);
    uint8_t front = (HAL_GPIO_ReadPin(FIGHT_IR_FRONT_PORT, FIGHT_IR_FRONT_PIN) == FIGHT_IR_TRIGGERED);
    uint8_t back  = (HAL_GPIO_ReadPin(FIGHT_IR_BACK_PORT,  FIGHT_IR_BACK_PIN)  == FIGHT_IR_TRIGGERED);

    /*判断敌人方向*/
    if(front)return DIR_FRONT;
    if(nw) return DIR_FRONT_LEFT;
    if(ne) return DIR_FRONT_RIGHT;
    if(l)  return DIR_LEFT;
    if(r)  return DIR_RIGHT;
    if(sw) return DIR_BACK_LEFT;
    if(se) return DIR_BACK_RIGHT;
    if(back) return DIR_BACK;
    return DIR_NONE;
}

/**
 * @description: 光电边缘检测(一票否决)
 * @param void
 * @return bool
 */
static bool Fight_EdgeDetected(void)
{
    Edge_Sensor_Detect();
    return (Obs_Data.IR1 == SET || Obs_Data.IR2 == SET);
}

/**
 * @description: 检测是否掉台（V0/V1 任一超阈值，连续确认后触发）
 * @param void
 * @return bool
 */
static bool detect_shade(void)
{
    site_detect_shade();

    if(voltage_v0 > 2.8f || voltage_v1 > 2.8f)
    {
        if(Fight_ShadeCount < FIGHT_SHADE_CONFIRM_COUNT)
        {
            Fight_ShadeCount++;
        }
    }
    else
    {
        Fight_ShadeCount = 0;
    }

    return (Fight_ShadeCount >= FIGHT_SHADE_CONFIRM_COUNT);
}

static char Fight_GetStableVisionType(void)
{
    if (Vision_IsTimeout() || !vision_target.valid)
    {
        Fight_PrevVisionType = 'X';
        Fight_StableVisionType = 'X';
        Fight_VisionTypeCount = 0;
        return 'X';
    }

    if (vision_target.type != Fight_PrevVisionType)
    {
        Fight_PrevVisionType = vision_target.type;
        Fight_VisionTypeCount = 1;
    }
    else if (Fight_VisionTypeCount < FIGHT_VISION_CONFIRM_COUNT)
    {
        Fight_VisionTypeCount++;
    }

    if (Fight_VisionTypeCount >= FIGHT_VISION_CONFIRM_COUNT)
    {
        Fight_StableVisionType = Fight_PrevVisionType;
    }

    return Fight_StableVisionType;
}

/*======状态机======*/

void Fight_Init(void)
{
    Motor_Ramp_SyncFromCurrent();
    Fight_State = FIGHT_ENGAGE;
    Fight_DoneFlag = false;
    Fight_StartTime = HAL_GetTick();
    Fight_EngageLost = 0;
    Fight_PrevRawDir = DIR_NONE;
    Fight_StableDir = DIR_NONE;
    Fight_EdgeCount = 0;
    Fight_PrevVisionType = 'X';
    Fight_StableVisionType = 'X';
    Fight_VisionTypeCount = 0;
    Fight_ShadeCount = 0;
    Fight_DownFlag = false;
    Fight_TrackDir = DIR_NONE;
}

void Fight_Update(void)
{
    uint32_t now = HAL_GetTick();
    uint32_t elapsed = now - Fight_StartTime;
    EnemyDir raw_dir = Fight_GetEnemyDir();
    if(raw_dir == Fight_PrevRawDir)
    {
        Fight_StableDir = raw_dir;
    }
    Fight_PrevRawDir = raw_dir;
    EnemyDir dir = Fight_StableDir;
    char vision_type = Fight_GetStableVisionType();

    /*======掉台安全======*/
    if(detect_shade())
    {
        Motor_Ramp_ForceStop();
        Fight_DownFlag = true;
        MOTOR_BrakeAll();
        return;
    }

    /*======边缘安全：交战/侧后追踪中都做确认，确认后先停顿再撤退======*/
    if(Fight_State == FIGHT_ENGAGE || Fight_State == FIGHT_TRACK_SPIN ||
       Fight_State == FIGHT_FORWARD)
    {
        if(Fight_EdgeDetected())
        {
            Fight_EdgeCount++;
            if(Fight_EdgeCount >= 2)
            {
                Motor_Ramp_ForceStop();
                MOTOR_BrakeAllRelease();
                Fight_ShadeCount = 0;
                Fight_State = FIGHT_EDGE_STOP;
                Fight_StartTime = now;
                Fight_EdgeCount = 0;
                return;
            }
        }
        else
        {
            Fight_EdgeCount = 0;
        }
    }

    switch(Fight_State)
    {
        /*======交战状态======*/
        case FIGHT_ENGAGE:
            if(dir != DIR_NONE)
            {
                /*有目标，清除交战丢失时间*/
                Fight_EngageLost = 0;

                /*正面接触后重置交战时间*/
                if(dir == DIR_FRONT || dir == DIR_FRONT_LEFT || dir == DIR_FRONT_RIGHT)
                {
                    Fight_StartTime = now;
                }

                /*视觉类型消抖后再判断*/
                if (vision_type == 'F' || vision_type == 'B') {
                    Motor_Ramp_ForceStop();
                    MOTOR_BrakeAll();
                    Fight_State = FIGHT_FB_TURN;
                    Fight_StartTime = now;
                    break;
                }

                /*IR方向追踪：前扇区走Ramp，侧后方直接进原地旋转*/
                switch(dir)
                {
                    case DIR_FRONT:
                        Motor_Ramp_SetTarget(FIGHT_TRACK_PUSH_SPEED, FIGHT_TRACK_PUSH_SPEED);
                        Motor_Ramp_Update();
                        break;
                    case DIR_FRONT_LEFT:
                        Motor_Ramp_SetTarget(FIGHT_TRACK_FRONT_INNER_SPEED,
                                             FIGHT_TRACK_FRONT_OUTER_SPEED);
                        Motor_Ramp_Update();
                        break;
                    case DIR_FRONT_RIGHT:
                        Motor_Ramp_SetTarget(FIGHT_TRACK_FRONT_OUTER_SPEED,
                                             FIGHT_TRACK_FRONT_INNER_SPEED);
                        Motor_Ramp_Update();
                        break;
                    case DIR_LEFT:
                    case DIR_BACK_LEFT:
                        Motor_Ramp_SyncFromCurrent();
                        Fight_State = FIGHT_TRACK_SPIN;
                        Fight_TrackDir = DIR_LEFT;
                        Fight_EngageLost = 0;
                        Fight_StartTime = now;
                        break;
                    case DIR_RIGHT:
                    case DIR_BACK_RIGHT:
                    case DIR_BACK:
                        Motor_Ramp_SyncFromCurrent();
                        Fight_State = FIGHT_TRACK_SPIN;
                        Fight_TrackDir = DIR_RIGHT;
                        Fight_EngageLost = 0;
                        Fight_StartTime = now;
                        break;
                    default:
                        break;
                }
            }
            else{
                /*丢失目标：保持当前速度继续跑，不立刻减速*/
                Motor_Ramp_Update();
                if(Fight_EngageLost == 0)
                {
                    Fight_EngageLost = now;
                }
                /*丢失超时，直接回漫游*/
                if(now - Fight_EngageLost >= FIGHT_ENGAGE_LOST)
                {
                    Fight_State = FIGHT_DONE;
                }
            }

            /*交战超时,结束：直接交给 DONE 统一停机收尾*/
            if((now - Fight_StartTime) >= FIGHT_ENGAGE_TIMEOUT)
            {
                Fight_State = FIGHT_DONE;
            }
            break;

        /*======边缘确认后短暂停顿======*/
        case FIGHT_EDGE_STOP:
            if(!MOTOR_IsBraking() && elapsed >= FIGHT_EDGE_STOP_TIME)
            {
                Fight_State = FIGHT_RETREAT;
                Fight_StartTime = now;
            }
            break;

        /*======撤退状态======*/
        case FIGHT_RETREAT:
            drive_user_defined(-600, -600);
            if(elapsed >= FIGHT_RETREAT_TIME)
            {
                Fight_State = FIGHT_TURN;
                Fight_StartTime = now;
            }
            break;

        /*======边缘恢复掉头状态======*/
        case FIGHT_TURN:
            drive_Left_S();
            if(elapsed >= FIGHT_TURN_TIME)
            {
                Fight_State = FIGHT_DONE;
            }
            break;

        /*======F/B回避掉头状态======*/
        case FIGHT_FB_TURN:
            drive_Left_S();
            if(elapsed >= FIGHT_TURN_TIME)
            {
                Fight_State = FIGHT_FORWARD;
                Fight_StartTime = now;
            }
            break;

        /*======F/B回避后短前进======*/
        case FIGHT_FORWARD:
            drive_For_M();
            if(elapsed >= FIGHT_FB_FORWARD_TIME)
            {
                Fight_State = FIGHT_DONE;
            }
            break;

        /*======侧后向追踪：直接原地补角，直到前扇区命中======*/
        case FIGHT_TRACK_SPIN:
            if(dir == DIR_FRONT || dir == DIR_FRONT_LEFT || dir == DIR_FRONT_RIGHT)
            {
                Fight_TrackDir = DIR_NONE;
                Fight_EngageLost = 0;
                Motor_Ramp_SyncFromCurrent();
                Fight_State = FIGHT_ENGAGE;
                Fight_StartTime = now;
                break;
            }

            if(dir == DIR_LEFT || dir == DIR_BACK_LEFT)
            {
                Fight_TrackDir = DIR_LEFT;
                Fight_EngageLost = 0;
            }
            else if(dir == DIR_RIGHT || dir == DIR_BACK_RIGHT || dir == DIR_BACK)
            {
                Fight_TrackDir = DIR_RIGHT;
                Fight_EngageLost = 0;
            }
            else if(dir == DIR_NONE)
            {
                if(Fight_EngageLost == 0)
                {
                    Fight_EngageLost = now;
                }
                else if((now - Fight_EngageLost) >= FIGHT_ENGAGE_LOST)
                {
                    Fight_State = FIGHT_DONE;
                    break;
                }
            }

            if(Fight_TrackDir == DIR_LEFT)
            {
                Motor_Ramp_SetTarget(-FIGHT_TRACK_SPIN_SPEED, FIGHT_TRACK_SPIN_SPEED);
            }
            else
            {
                Motor_Ramp_SetTarget(FIGHT_TRACK_SPIN_SPEED, -FIGHT_TRACK_SPIN_SPEED);
            }
            Motor_Ramp_Update();
            break;

        /*======完成状态======*/
        case FIGHT_DONE:
            MOTOR_StopAll();
            Fight_DoneFlag = true;
            break;
    }

}

bool Fight_IsDone(void)
{
    return Fight_DoneFlag;
}

bool Fight_IsDown(void)
{
    return Fight_DownFlag;
}