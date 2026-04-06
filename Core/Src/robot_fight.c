#include "robot_fight.h"
#include "robot_roaming.h"
#include "motor.h"
#include "obstacle.h"
#include "shade.h"
#include "vision_parser.h"
#include "usart.h"

static FightState Fight_State = FIGHT_ENGAGE;
static uint32_t Fight_StartTime = 0;
static bool Fight_DoneFlag = false;
static bool Fight_DownFlag = false;
static uint32_t Fight_EngageLost = 0;

/* 方向消抖: 连续2次相同方向才确认有效 */
static EnemyDir Fight_PrevRawDir = DIR_NONE;
static EnemyDir Fight_StableDir  = DIR_NONE;

/* 灰度掉台消抖计数 (阈值定义在 shade.h) */
static uint8_t Fight_ShadeDownCount = 0;

/* 视觉类型消抖 (远程新增) */
static char Fight_PrevVisionType = 'X';
static char Fight_StableVisionType = 'X';
static uint8_t Fight_VisionTypeCount = 0;

/* 边缘连续计数 */
static uint8_t Fight_EdgeCount = 0;

/* 后方光电忽略窗口 (F/B掉头后避免立即重新检测) */
static uint32_t Fight_IgnoreRearUntil = 0;

/* 动作原因追踪 (区分边缘触发 vs F/B触发的不同后续行为) */
typedef enum {
    FIGHT_ACTION_NONE = 0,
    FIGHT_ACTION_EDGE,
    FIGHT_ACTION_FB
} FightActionReason;
static FightActionReason Fight_ActionReason = FIGHT_ACTION_NONE;

/* 视觉追踪PD控制 v2: 死区+转向承诺 */
#define VISION_DEADZONE       8
#define VISION_TURN_COMMIT_MS 150
#define VISION_KP             20
#define VISION_KD             12

static int8_t prev_vision_dir = 0;
static int16_t committed_turn = 0;
static uint32_t turn_commit_time = 0;

/**
 * @description: 视觉精准追踪 v2 (死区+转向承诺+PD增益优化)
 */
static void Fight_VisionChase(void)
{
    int8_t d = vision_target.dir;  // [-100, +100]
    int16_t base = SPEED_MEDIUM;
    uint32_t now = HAL_GetTick();

    /* 距离自适应基础速度 (在死区和PD路径之前统一计算) */
    if(vision_target.area > 15000) base = SPEED_LOW;
    else if(vision_target.area < 3000) base = SPEED_HIGH;

    /* 死区: 目标基本居中, 直走 */
    if(d > -VISION_DEADZONE && d < VISION_DEADZONE)
    {
        /* 转向承诺有效期内继续上次转向, 避免抖动 */
        if(now - turn_commit_time < VISION_TURN_COMMIT_MS && committed_turn != 0)
        {
            int16_t left  = base + committed_turn;
            int16_t right = base - committed_turn;
            if(left > 1000) left = 1000; if(left < -1000) left = -1000;
            if(right > 1000) right = 1000; if(right < -1000) right = -1000;
            drive_user_defined(left, right);
        }
        else
        {
            /* 直走 (base已根据距离自适应) */
            drive_user_defined(base, base);
        }
        prev_vision_dir = d;
        return;
    }

    /* PD控制: Kp=2.0, Kd=1.2 */
    int16_t p_term = (int16_t)((int16_t)d * VISION_KP / 10);
    int16_t d_term = (int16_t)(((int16_t)d - (int16_t)prev_vision_dir) * VISION_KD / 10);
    prev_vision_dir = d;
    int16_t turn = p_term + d_term;

    /* 转向承诺: 防止方向频繁翻转 */
    if((turn > 0) != (committed_turn > 0) && committed_turn != 0)
    {
        if(now - turn_commit_time < VISION_TURN_COMMIT_MS)
        {
            turn = committed_turn;
        }
        else
        {
            committed_turn = turn;
            turn_commit_time = now;
        }
    }
    else
    {
        committed_turn = turn;
        turn_commit_time = now;
    }

    /* base已在函数顶部根据距离自适应 */

    int16_t left  = base + turn;
    int16_t right = base - turn;
    if(left > 1000) left = 1000; if(left < -1000) left = -1000;
    if(right > 1000) right = 1000; if(right < -1000) right = -1000;
    drive_user_defined(left, right);
}

/*======传感器读取======*/

/**
 * @description: 获取敌人方向 (含后方光电忽略窗口)
 */
EnemyDir Fight_GetEnemyDir(void)
{
    uint32_t now = HAL_GetTick();
    uint8_t ignore_rear = (Fight_IgnoreRearUntil != 0 && now < Fight_IgnoreRearUntil);

    /*读取八路光电传感器*/
    uint8_t nw    = (HAL_GPIO_ReadPin(FIGHT_IR_NW_PORT,    FIGHT_IR_NW_PIN)    == FIGHT_IR_TRIGGERED);
    uint8_t ne    = (HAL_GPIO_ReadPin(FIGHT_IR_NE_PORT,    FIGHT_IR_NE_PIN)    == FIGHT_IR_TRIGGERED);
    uint8_t l     = (HAL_GPIO_ReadPin(FIGHT_IR_L_PORT,     FIGHT_IR_L_PIN)     != FIGHT_IR_TRIGGERED);
    uint8_t r     = (HAL_GPIO_ReadPin(FIGHT_IR_R_PORT,     FIGHT_IR_R_PIN)     != FIGHT_IR_TRIGGERED);
    uint8_t sw    = (HAL_GPIO_ReadPin(FIGHT_IR_SW_PORT,    FIGHT_IR_SW_PIN)    == FIGHT_IR_TRIGGERED);
    uint8_t se    = (HAL_GPIO_ReadPin(FIGHT_IR_SE_PORT,    FIGHT_IR_SE_PIN)    == FIGHT_IR_TRIGGERED);
    uint8_t front = (HAL_GPIO_ReadPin(FIGHT_IR_FRONT_PORT, FIGHT_IR_FRONT_PIN) == FIGHT_IR_TRIGGERED);
    uint8_t back  = (HAL_GPIO_ReadPin(FIGHT_IR_BACK_PORT,  FIGHT_IR_BACK_PIN)  == FIGHT_IR_TRIGGERED);

    /* F/B掉头后忽略后方光电, 避免立即重新检测到刚离开的物体 */
    if(ignore_rear)
    {
        back = 0;
    }

    /*判断敌人方向*/
    if(front) return DIR_FRONT;
    if(nw || (nw && front)) return DIR_FRONT_LEFT;
    if(ne || (ne && front)) return DIR_FRONT_RIGHT;
    if(l)  return DIR_LEFT;
    if(r)  return DIR_RIGHT;
    if(sw) return DIR_BACK_LEFT;
    if(se) return DIR_BACK_RIGHT;
    if(back) return DIR_BACK;
    return DIR_NONE;
}

/**
 * @description: 光电边缘检测(一票否决)
 */
static bool Fight_EdgeDetected(void)
{
    Edge_Sensor_Detect();
    return (Obs_Data.IR1 == SET || Obs_Data.IR2 == SET);
}

/**
 * @description: 视觉类型消抖 — 连续N帧相同类型才确认有效
 *   防止视觉闪烁导致误判 (如E→F→E快速跳变)
 */
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

/**
 * @description: 掉台检测(灰度传感器 滤波+消抖 + 原始值紧急快速通道)
 *   正常路径: voltage_filtered > 2.85V 连续5次确认 (50ms)
 *   紧急路径: voltage raw > 3.10V 双传感器, 跳过滤波直接确认 (0ms)
 */
static bool Fight_DetectShade(void)
{
    site_detect_shade();

    /* 紧急快速通道: 原始值极高 = 确定掉台, 跳过滤波延迟 */
    if(voltage[0] > SHADE_RAW_EMERGENCY
       && voltage[1] > SHADE_RAW_EMERGENCY)
    {
        return true;
    }

    /* 正常路径: 滤波值 + 连续确认 */
    if(voltage_filtered[0] > SHADE_DOWN_THRESHOLD
       && voltage_filtered[1] > SHADE_DOWN_THRESHOLD)
    {
        Fight_ShadeDownCount++;
        if(Fight_ShadeDownCount >= SHADE_DOWN_CONFIRM)
            return true;
    }
    else
    {
        Fight_ShadeDownCount = 0;
    }
    return false;
}

/*======状态机======*/

void Fight_Init(void)
{
    Fight_State = FIGHT_ENGAGE;
    Fight_DoneFlag = false;
    Fight_DownFlag = false;
    Fight_StartTime = HAL_GetTick();
    Fight_EngageLost = 0;
    Fight_PrevRawDir = DIR_NONE;
    Fight_StableDir  = DIR_NONE;
    Fight_ShadeDownCount = 0;
    Fight_PrevVisionType = 'X';
    Fight_StableVisionType = 'X';
    Fight_VisionTypeCount = 0;
    Fight_EdgeCount = 0;
    Fight_IgnoreRearUntil = 0;
    Fight_ActionReason = FIGHT_ACTION_NONE;
    prev_vision_dir = 0;
    committed_turn = 0;
    turn_commit_time = 0;
}

void Fight_Update(void)
{
    uint32_t now = HAL_GetTick();
    uint32_t elapsed = now - Fight_StartTime;

    /*======掉台安全: 灰度传感器 滤波+消抖======*/
    if(Fight_DetectShade())
    {
        Fight_DownFlag = true;
        MOTOR_BrakeAll();
        return;
    }

    /* 方向消抖 + 视觉类型消抖 */
    EnemyDir raw_dir = Fight_GetEnemyDir();
    if(raw_dir == Fight_PrevRawDir)
    {
        Fight_StableDir = raw_dir;
    }
    Fight_PrevRawDir = raw_dir;
    EnemyDir dir = Fight_StableDir;
    char vision_type = Fight_GetStableVisionType();

    /*======边缘安全: 非RETREAT状态下制动并计数======*/
    if(Fight_EdgeDetected())
    {
        MOTOR_BrakeAll();
        Fight_ShadeDownCount = 0;  /* 边缘期间清掉灰度累计 */
        Fight_EdgeCount++;
        if(Fight_State != FIGHT_RETREAT)
        {
            if(Fight_EdgeCount >= 2)
            {
                Fight_State = FIGHT_RETREAT;
                Fight_ActionReason = FIGHT_ACTION_EDGE;
                Fight_StartTime = now;
            }
            return;
        }
    }
    else
    {
        Fight_EdgeCount = 0;
    }

    switch(Fight_State)
    {
        /*======交战状态======*/
        case FIGHT_ENGAGE:
            if(dir != DIR_NONE)
            {
                /* 有目标, 清除丢失计时 */
                Fight_EngageLost = 0;

                /* 正面接触后重置交战时间 */
                if(dir == DIR_FRONT || dir == DIR_FRONT_LEFT || dir == DIR_FRONT_RIGHT)
                {
                    Fight_StartTime = now;
                }

                /* 视觉类型消抖后判断: F(友方)/B(炸弹) → 制动+掉头回避 */
                if(vision_type == 'F' || vision_type == 'B')
                {
                    MOTOR_BrakeAll();
                    Fight_ActionReason = FIGHT_ACTION_FB;
                    Fight_State = FIGHT_TURN;
                    Fight_StartTime = now;
                    break;
                }

                /* 视觉检测到敌方/中立 → PD追踪 */
                if(vision_type == 'E' || vision_type == 'N')
                {
                    Fight_VisionChase();
                    break;
                }

                /* 视觉无目标但光电有目标 → 按方向驱动 */
                switch(dir)
                {
                    case DIR_FRONT:       drive_For_M();   break;
                    case DIR_FRONT_LEFT:  drive_Left_M();  break;
                    case DIR_FRONT_RIGHT: drive_Right_M(); break;
                    case DIR_LEFT:        drive_Left_M();  break;
                    case DIR_RIGHT:       drive_Right_M(); break;
                    case DIR_BACK_LEFT:   drive_Left_S();  break;
                    case DIR_BACK_RIGHT:  drive_Right_S(); break;
                    case DIR_BACK:        drive_Right_S(); break;
                    default: break;
                }
            }
            else
            {
                /* 丢失目标, 开始/继续丢失倒计时 */
                if(Fight_EngageLost == 0)
                {
                    Fight_EngageLost = now;
                }
                if(now - Fight_EngageLost >= FIGHT_ENGAGE_LOST)
                {
                    MOTOR_StopAll();
                    Fight_State = FIGHT_DONE;
                    Fight_DoneFlag = true;
                }
            }

            /* 交战超时 */
            if(elapsed >= FIGHT_ENGAGE_TIMEOUT)
            {
                MOTOR_StopAll();
                Fight_State = FIGHT_DONE;
                Fight_DoneFlag = true;
            }
            break;

        /*======撤退状态======*/
        case FIGHT_RETREAT:
            drive_Back_M();
            if(elapsed >= FIGHT_RETREAT_TIME)
            {
                Fight_ActionReason = FIGHT_ACTION_EDGE;
                Fight_State = FIGHT_TURN;
                Fight_StartTime = now;
            }
            break;

        /*======掉头状态======*/
        case FIGHT_TURN:
            drive_Left_S();
            if(elapsed >= FIGHT_TURN_TIME)
            {
                if(Fight_ActionReason == FIGHT_ACTION_FB)
                {
                    /* F/B回避: 掉头后短前进, 并忽略后方光电 */
                    Fight_IgnoreRearUntil = now + FIGHT_FB_REAR_IGNORE_TIME;
                    Fight_State = FIGHT_FORWARD;
                    Fight_StartTime = now;
                }
                else
                {
                    /* 边缘回避: 掉头后结束 */
                    MOTOR_StopAll();
                    Fight_State = FIGHT_DONE;
                    Fight_DoneFlag = true;
                    Fight_ActionReason = FIGHT_ACTION_NONE;
                }
            }
            break;

        /*======F/B回避后短前进======*/
        case FIGHT_FORWARD:
            drive_For_M();
            if(elapsed >= FIGHT_FB_FORWARD_TIME)
            {
                MOTOR_StopAll();
                Fight_IgnoreRearUntil = 0;
                Fight_State = FIGHT_DONE;
                Fight_DoneFlag = true;
                Fight_ActionReason = FIGHT_ACTION_NONE;
            }
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
