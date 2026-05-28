#include "robot_fight.h"
#include "motor.h"
#include "obstacle.h"
#include "shade.h"
#include "vision_parser.h"

typedef enum {
    FIGHT_EDGE_SIDE_NONE = 0,                         // 未锁存前边缘方向
    FIGHT_EDGE_SIDE_LEFT,                             // 左前边缘触发，用于选择向右小弧度后退
    FIGHT_EDGE_SIDE_RIGHT,                            // 右前边缘触发，用于选择向左小弧度后退
    FIGHT_EDGE_SIDE_BOTH                              // 双前边缘触发，优先结合最近弧追方向逃逸
} FightEdgeSide;

static FightState Fight_State = FIGHT_ENGAGE;        // 当前进攻状态机状态
static uint32_t Fight_StartTime = 0;                 // 当前状态起始时间戳，用于状态内超时判断
static bool Fight_DoneFlag = false;                  // 进攻阶段完成标志（通知总控回漫游）
static uint32_t Fight_EngageLost = 0;                // 丢失目标的起始时间，超过 FIGHT_ENGAGE_LOST 后退出进攻
static EnemyDir Fight_PrevRawDir = DIR_NONE;         // 上一拍原始敌人方向，用于方向兜底
static EnemyDir Fight_StableDir  = DIR_NONE;         // 当前用于动作分支的敌人方向，一拍即用
static uint8_t Fight_RearEdgeCount = 0;              // 后方安全传感器连续触发计数
static char Fight_PrevVisionType = 'X';              // 上一拍视觉类型（用于视觉类型消抖）
static char Fight_StableVisionType = 'X';            // 消抖后的稳定视觉类型
static uint8_t Fight_VisionTypeCount = 0;            // 视觉类型连续命中计数
static uint32_t Fight_LastVisionTypeTime = 0;        // 上一次参与类型消抖的视觉帧时间戳
static uint32_t Fight_FBAvoidVisionTime = 0;         // 上一次参与F/B回避确认的视觉帧时间戳
static uint8_t Fight_FriendAvoidCount = 0;           // 友方回避连续确认计数
static uint8_t Fight_BombAvoidCount = 0;             // 炸弹回避连续确认计数
static uint32_t Fight_ShadeStartTime = 0;            // 灰度掉台原始条件开始成立的时间
static bool Fight_DownFlag = false;                  // 掉台标志（通知总控切 BACKUP）
static EnemyDir Fight_TrackDir = DIR_NONE;           // 侧边/后侧边甩头追踪时锁定的方向
static EnemyDir Fight_LastTrackSide = DIR_RIGHT;     // 正后方只知道在后面时，沿用最近一次左右甩头侧
static EnemyDir Fight_LastFrontArcDir = DIR_NONE;    // 最近一次前侧方弧追方向，用于双前边缘逃逸参考
static uint32_t Fight_LastFrontArcTime = 0;          // 最近一次前侧方弧追时间戳
static EnemyDir Fight_EdgeEscapeArcDir = DIR_NONE;   // 触发前边缘时锁存的逃逸参考方向
static FightEdgeSide Fight_EdgeSide = FIGHT_EDGE_SIDE_NONE; // 触发前边缘时锁存的边缘侧别
static bool Fight_FrontHoldConsumed = false;         // 双前遮挡正前保持只进入一次，避免 1000ms 保持反复重启

static EnemyDir Fight_GetLockedTrackDir(void)
{
    // 左右侧同时触发时方向会变模糊，优先沿用正在甩头的锁定方向。
    if(Fight_TrackDir == DIR_LEFT || Fight_TrackDir == DIR_RIGHT ||
       Fight_TrackDir == DIR_BACK_LEFT || Fight_TrackDir == DIR_BACK_RIGHT ||
       Fight_TrackDir == DIR_BACK)
    {
        return Fight_TrackDir;
    }
    if(Fight_StableDir == DIR_LEFT || Fight_StableDir == DIR_BACK_LEFT)
    {
        return DIR_LEFT;
    }
    if(Fight_StableDir == DIR_RIGHT || Fight_StableDir == DIR_BACK_RIGHT)
    {
        return DIR_RIGHT;
    }
    if(Fight_StableDir == DIR_BACK)
    {
        return Fight_LastTrackSide;
    }
    if(Fight_PrevRawDir == DIR_LEFT || Fight_PrevRawDir == DIR_BACK_LEFT)
    {
        return DIR_LEFT;
    }
    if(Fight_PrevRawDir == DIR_RIGHT || Fight_PrevRawDir == DIR_BACK_RIGHT)
    {
        return DIR_RIGHT;
    }
    if(Fight_PrevRawDir == DIR_BACK)
    {
        return Fight_LastTrackSide;
    }
    return Fight_LastTrackSide;
}

static bool Fight_IsFrontArcDir(EnemyDir dir)
{
    // IR3/IR5 属于前侧方大角度弧追，用来给双前边缘逃逸提供方向记忆。
    return (dir == DIR_FRONT_LEFT || dir == DIR_FRONT_RIGHT);
}

static bool Fight_IsFrontHoldInterruptDir(EnemyDir dir)
{
    return (dir == DIR_FRONT_LEFT || dir == DIR_FRONT_RIGHT ||
            dir == DIR_LEFT || dir == DIR_RIGHT ||
            dir == DIR_BACK_LEFT || dir == DIR_BACK_RIGHT || dir == DIR_BACK);
}

static void Fight_UpdateLastFrontArcDir(uint32_t now, EnemyDir dir)
{
    if(Fight_IsFrontArcDir(dir))
    {
        // 记住最近一次前侧方追踪方向，前边缘双触发时按这个方向反向脱离。
        Fight_LastFrontArcDir = dir;
        Fight_LastTrackSide = (dir == DIR_FRONT_LEFT) ? DIR_LEFT : DIR_RIGHT;
        Fight_LastFrontArcTime = now;
    }
}

static EnemyDir Fight_GetEdgeEscapeArcDir(uint32_t now)
{
    if(Fight_LastFrontArcDir != DIR_NONE &&
       (now - Fight_LastFrontArcTime) <= FIGHT_EDGE_ARC_MEMORY_MS)
    {
        // 只使用 800ms 内的新鲜弧追记忆，太旧的方向不再参与逃逸决策。
        return Fight_LastFrontArcDir;
    }
    return DIR_NONE;
}

static FightEdgeSide Fight_GetEdgeSide(void)
{
    if(Obs_Data.IR1 == OBS_EDGE_TRIGGERED_STATE && Obs_Data.IR2 == OBS_EDGE_TRIGGERED_STATE)
    {
        return FIGHT_EDGE_SIDE_BOTH;
    }
    if(Obs_Data.IR1 == OBS_EDGE_TRIGGERED_STATE)
    {
        return FIGHT_EDGE_SIDE_LEFT;
    }
    if(Obs_Data.IR2 == OBS_EDGE_TRIGGERED_STATE)
    {
        return FIGHT_EDGE_SIDE_RIGHT;
    }
    return FIGHT_EDGE_SIDE_NONE;
}

static void Fight_DriveEdgeRetreat(uint32_t elapsed)
{
    if(elapsed < FIGHT_RETREAT_SOFT_TIME)
    {
        drive_user_defined(-FIGHT_RETREAT_SOFT_SPEED, -FIGHT_RETREAT_SOFT_SPEED);
        return;
    }

    if(Fight_EdgeSide == FIGHT_EDGE_SIDE_LEFT)
    {
        // 左前边缘触发时左轮后退更快，向右后方带一点弧度退开。
        drive_user_defined(-FIGHT_RETREAT_SPEED, -(FIGHT_RETREAT_SPEED - FIGHT_EDGE_RETREAT_DIFF));
    }
    else if(Fight_EdgeSide == FIGHT_EDGE_SIDE_RIGHT)
    {
        // 右前边缘触发时右轮后退更快，向左后方带一点弧度退开。
        drive_user_defined(-(FIGHT_RETREAT_SPEED - FIGHT_EDGE_RETREAT_DIFF), -FIGHT_RETREAT_SPEED);
    }
    else
    {
        // 双前边缘或未知侧别时不偏转，直接直退保证先离开边缘。
        drive_user_defined(-FIGHT_RETREAT_SPEED, -FIGHT_RETREAT_SPEED);
    }
}

static bool Fight_ShouldTurnRight(void)
{
    // 左前边缘或最近左前弧追后触边时，右转更容易把车头带回台内。
    return (Fight_EdgeSide == FIGHT_EDGE_SIDE_LEFT ||
            (Fight_EdgeSide == FIGHT_EDGE_SIDE_BOTH && Fight_EdgeEscapeArcDir == DIR_FRONT_LEFT) ||
            (Fight_EdgeSide == FIGHT_EDGE_SIDE_NONE && Fight_EdgeEscapeArcDir == DIR_FRONT_LEFT));
}

/*======传感器读取======*/

/**
 * @description: 获取敌人方向
 * @param void
 * @return EnemyDir
 */
EnemyDir Fight_GetEnemyDir(void)
{
    uint8_t nw;
    uint8_t ne;
    uint8_t l;
    uint8_t r;
    uint8_t sw;
    uint8_t se;
    uint8_t front_left;
    uint8_t front_right;
    uint8_t back;

    Enmy_Sensor_Detect();

    nw          = (Obs_Data.IR3  == OBS_BLOCKED_STATE);
    ne          = (Obs_Data.IR5  == OBS_BLOCKED_STATE);
    l           = (Obs_Data.IR10 == OBS_BLOCKED_STATE);
    r           = (Obs_Data.IR6  == OBS_BLOCKED_STATE);
    sw          = (Obs_Data.IR9  == OBS_BLOCKED_STATE);
    se          = (Obs_Data.IR7  == OBS_BLOCKED_STATE);
    front_left  = (Obs_Data.IR4  == OBS_BLOCKED_STATE);
    front_right = (Obs_Data.IR11 == OBS_BLOCKED_STATE);
    back        = (Obs_Data.IR8  == OBS_BLOCKED_STATE);

    /*判断敌人方向*/
    // IR4+IR11 优先判正前，单独触发时判小偏，用小弧度修正车头。
    if(front_left && front_right) return DIR_FRONT;
    if(front_left) return DIR_FRONT_SLIGHT_LEFT;
    if(front_right) return DIR_FRONT_SLIGHT_RIGHT;

    // IR3/IR5 是前侧方大角度追踪，两路同时触发也视为正前。
    if(nw && ne) return DIR_FRONT;
    if(nw) return DIR_FRONT_LEFT;
    if(ne) return DIR_FRONT_RIGHT;

    // 正左右同时触发时不直接判正前，沿用锁定甩头方向避免左右来回抖。
    if(l && r) return Fight_GetLockedTrackDir();
    if(l)  return DIR_LEFT;
    if(r)  return DIR_RIGHT;

    // 后侧方和正后方进入 FIGHT_TRACK_SPIN，由甩头追踪转回前扇区。
    if(sw && se) return DIR_BACK;
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
    // 前方朝下光电是一票否决安全条件，触发后立即制动并进入边缘逃逸链路。
    return (Obs_Data.IR1 == OBS_EDGE_TRIGGERED_STATE || Obs_Data.IR2 == OBS_EDGE_TRIGGERED_STATE);
}

static bool Fight_RearEdgeDetected(void)
{
    // 后方两路都未遮挡时认为车尾已经接近边缘，需要前进脱离。
    return (Obs_Data.IR12 != OBS_BLOCKED_STATE && Obs_Data.IR13 != OBS_BLOCKED_STATE);
}

static bool detect_shade(uint32_t now)
{
    site_detect_shade();

    if((voltage_v0 > 2.8f && voltage_v0 < 3.0f) &&
       (voltage_v1 > 2.8f && voltage_v1 < 3.0f))
    {
        if(Fight_ShadeStartTime == 0)
        {
            Fight_ShadeStartTime = now;
        }
        return ((now - Fight_ShadeStartTime) >= FIGHT_SHADE_CONFIRM_TIME);
    }

    Fight_ShadeStartTime = 0;
    return false;
}

static char Fight_GetStableVisionType(void)
{
    if (Vision_IsTimeout() || !vision_target.valid)
    {
        // 视觉超时或帧无效时清空稳定类型，避免旧 F/B 继续触发回避。
        Fight_PrevVisionType = 'X';
        Fight_StableVisionType = 'X';
        Fight_VisionTypeCount = 0;
        Fight_LastVisionTypeTime = 0;
        return 'X';
    }

    if(vision_target.timestamp == Fight_LastVisionTypeTime)
    {
        return Fight_StableVisionType;
    }
    Fight_LastVisionTypeTime = vision_target.timestamp;

    if (vision_target.type != Fight_PrevVisionType)
    {
        // 类型变化时重新开始计数，连续命中后才承认新视觉类型。
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

static bool Fight_IsFriendVisionFront(int8_t vision_dir)
{
    return (vision_dir >= -FIGHT_FRIEND_DIR_GATE && vision_dir <= FIGHT_FRIEND_DIR_GATE);
}

static void Fight_ResetFBAvoidConfirm(void)
{
    Fight_FriendAvoidCount = 0;
    Fight_BombAvoidCount = 0;
}

static bool Fight_ShouldAvoidVisionTarget(char vision_type)
{
    uint32_t vision_time;
    char raw_type;
    int32_t area;
    int8_t vision_dir;

    if(Vision_IsTimeout() || !vision_target.valid)
    {
        Fight_ResetFBAvoidConfirm();
        Fight_FBAvoidVisionTime = 0;
        return false;
    }

    vision_time = vision_target.timestamp;
    if(vision_time == Fight_FBAvoidVisionTime)
    {
        return false;
    }
    Fight_FBAvoidVisionTime = vision_time;

    raw_type = vision_target.type;
    area = vision_target.area;
    vision_dir = vision_target.dir;

    if(vision_type == 'F' && raw_type == 'F')
    {
        Fight_BombAvoidCount = 0;
        if(area < FIGHT_FRIEND_AREA_OFF || !Fight_IsFriendVisionFront(vision_dir))
        {
            Fight_FriendAvoidCount = 0;
        }
        else if(area >= FIGHT_FRIEND_AREA_ON && Fight_FriendAvoidCount < FIGHT_FB_CONFIRM_COUNT)
        {
            Fight_FriendAvoidCount++;
        }
        return (Fight_FriendAvoidCount >= FIGHT_FB_CONFIRM_COUNT);
    }

    if(vision_type == 'B' && raw_type == 'B')
    {
        Fight_FriendAvoidCount = 0;
        if(area < FIGHT_BOMB_AREA_OFF)
        {
            Fight_BombAvoidCount = 0;
        }
        else if(area >= FIGHT_BOMB_AREA_ON && Fight_BombAvoidCount < FIGHT_FB_CONFIRM_COUNT)
        {
            Fight_BombAvoidCount++;
        }
        return (Fight_BombAvoidCount >= FIGHT_FB_CONFIRM_COUNT);
    }

    Fight_ResetFBAvoidConfirm();
    return false;
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
    Fight_RearEdgeCount = 0;
    Fight_PrevVisionType = 'X';
    Fight_StableVisionType = 'X';
    Fight_VisionTypeCount = 0;
    Fight_LastVisionTypeTime = 0;
    Fight_FBAvoidVisionTime = 0;
    Fight_ResetFBAvoidConfirm();
    Fight_ShadeStartTime = 0;
    Fight_DownFlag = false;
    Fight_TrackDir = DIR_NONE;
    Fight_LastTrackSide = DIR_RIGHT;
    Fight_LastFrontArcDir = DIR_NONE;
    Fight_LastFrontArcTime = 0;
    Fight_EdgeEscapeArcDir = DIR_NONE;
    Fight_EdgeSide = FIGHT_EDGE_SIDE_NONE;
    Fight_FrontHoldConsumed = false;
}

void Fight_InitWithDir(EnemyDir dir)
{
    Fight_Init();
    Fight_PrevRawDir = dir;
    Fight_StableDir = dir;
    if(Fight_IsFrontArcDir(dir))
    {
        Fight_LastFrontArcDir = dir;
        Fight_LastFrontArcTime = HAL_GetTick();
    }
}

void Fight_Update(void)
{
    uint32_t now = HAL_GetTick();
    uint32_t elapsed = now - Fight_StartTime;
    EnemyDir raw_dir;
    EnemyDir dir;
    char vision_type;
    bool front_hold_trigger;

    Edge_Sensor_Detect();
    // 前边缘优先级最高，除正在执行边缘逃逸链路外，任何进攻动作都要立刻打断。
    if(Fight_State != FIGHT_EDGE_STOP &&
       Fight_State != FIGHT_RETREAT &&
       Fight_State != FIGHT_TURN &&
       Fight_State != FIGHT_DONE &&
       Fight_EdgeDetected())
    {
        MOTOR_BrakeAll();
        Fight_ShadeStartTime = 0;
        Fight_RearEdgeCount = 0;
        Fight_EdgeEscapeArcDir = Fight_GetEdgeEscapeArcDir(now);
        Fight_EdgeSide = Fight_GetEdgeSide();
        Fight_State = FIGHT_EDGE_STOP;
        Fight_StartTime = now;
        return;
    }

    raw_dir = Fight_GetEnemyDir();
    front_hold_trigger = (Obs_Data.IR4 == OBS_BLOCKED_STATE && Obs_Data.IR11 == OBS_BLOCKED_STATE);
    Fight_StableDir = raw_dir;
    Fight_PrevRawDir = raw_dir;
    dir = Fight_StableDir;
    if(dir != DIR_FRONT && Fight_State != FIGHT_FRONT_HOLD)
    {
        // 离开正前后允许下一次双前遮挡重新进入 1000ms 正前保持。
        Fight_FrontHoldConsumed = false;
    }
    vision_type = Fight_GetStableVisionType();
    if(Fight_State == FIGHT_ENGAGE)
    {
        Fight_UpdateLastFrontArcDir(now, dir);
    }

    /*======掉台安全======*/
    if(detect_shade(now))
    {
        Fight_DownFlag = true;
        MOTOR_BrakeAll();
        return;
    }

    if(Fight_State != FIGHT_EDGE_STOP &&
       Fight_State != FIGHT_REAR_EDGE_STOP &&
       Fight_State != FIGHT_REAR_ESCAPE &&
       Fight_State != FIGHT_DONE)
    {
        if(!Fight_EdgeDetected() && Fight_RearEdgeDetected())
        {
            if(Fight_RearEdgeCount < FIGHT_REAR_EDGE_CONFIRM_COUNT)
            {
                Fight_RearEdgeCount++;
            }
            if(Fight_RearEdgeCount >= FIGHT_REAR_EDGE_CONFIRM_COUNT)
            {
                MOTOR_BrakeAll();
                Fight_ShadeStartTime = 0;
                Fight_RearEdgeCount = 0;
                Fight_State = FIGHT_REAR_EDGE_STOP;
                Fight_StartTime = now;
                return;
            }
        }
        else
        {
            Fight_RearEdgeCount = 0;
        }
    }

    // F/B回避只用新视觉帧连续确认，避免单帧面积抖动导致忽远忽近。
    if(Fight_ShouldAvoidVisionTarget(vision_type) &&
       Fight_State != FIGHT_EDGE_STOP &&
       Fight_State != FIGHT_RETREAT &&
       Fight_State != FIGHT_TURN &&
       Fight_State != FIGHT_REAR_EDGE_STOP &&
       Fight_State != FIGHT_REAR_ESCAPE &&
       Fight_State != FIGHT_FB_TURN &&
       Fight_State != FIGHT_FORWARD &&
       Fight_State != FIGHT_FB_ADVANCE &&
       Fight_State != FIGHT_DONE)
    {
        MOTOR_BrakeAll();
        Fight_State = FIGHT_FB_TURN;
        Fight_StartTime = now;
        return;
    }

    if(Fight_State == FIGHT_ENGAGE && front_hold_trigger && !Fight_FrontHoldConsumed)
    {
        // IR4+IR11 原始双遮挡立即进入正前保持。
        Fight_FrontHoldConsumed = true;
        Motor_Ramp_SyncFromCurrent();
        Fight_State = FIGHT_FRONT_HOLD;
        Fight_StartTime = now;
        return;
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
                if(dir == DIR_FRONT || dir == DIR_FRONT_SLIGHT_LEFT || dir == DIR_FRONT_SLIGHT_RIGHT ||
                   dir == DIR_FRONT_LEFT || dir == DIR_FRONT_RIGHT)
                {
                    Fight_StartTime = now;
                }

                /*IR方向追踪：前扇区走Ramp，侧后方进入前冲甩头*/
                switch(dir)
                {
                    case DIR_FRONT:
                        // 原始 IR4+IR11 双遮挡已经在 switch 前触发正前保持；这里处理保持结束后的普通正前推进。
                        Motor_Ramp_SetTarget(FIGHT_TRACK_PUSH_SPEED, FIGHT_TRACK_PUSH_SPEED);
                        Motor_Ramp_Update();
                        break;
                    case DIR_FRONT_SLIGHT_LEFT:
                        // IR4 单独遮挡：左轮慢、右轮快，小弧度向左前方修正。
                        Motor_Ramp_SetTarget(FIGHT_TRACK_FRONT_SMALL_ARC_INNER_SPEED,
                                             FIGHT_TRACK_FRONT_SMALL_ARC_OUTER_SPEED);
                        Motor_Ramp_Update();
                        break;
                    case DIR_FRONT_SLIGHT_RIGHT:
                        // IR11 单独遮挡：右轮慢、左轮快，小弧度向右前方修正。
                        Motor_Ramp_SetTarget(FIGHT_TRACK_FRONT_SMALL_ARC_OUTER_SPEED,
                                             FIGHT_TRACK_FRONT_SMALL_ARC_INNER_SPEED);
                        Motor_Ramp_Update();
                        break;
                    case DIR_FRONT_LEFT:
                        // IR3 命中：大角度左前弧追，内侧轮接近停住，外侧轮把车头甩过去。
                        Motor_Ramp_SetTarget(FIGHT_TRACK_FRONT_ARC_INNER_SPEED,
                                             FIGHT_TRACK_FRONT_ARC_OUTER_SPEED);
                        Motor_Ramp_Update();
                        break;
                    case DIR_FRONT_RIGHT:
                        // IR5 命中：大角度右前弧追，动作与左前对称。
                        Motor_Ramp_SetTarget(FIGHT_TRACK_FRONT_ARC_OUTER_SPEED,
                                             FIGHT_TRACK_FRONT_ARC_INNER_SPEED);
                        Motor_Ramp_Update();
                        break;
                    case DIR_LEFT:
                    case DIR_BACK_LEFT:
                        // 左侧/左后侧看见目标时进入甩头追踪，直到目标进入前扇区再回 ENGAGE。
                        Motor_Ramp_SyncFromCurrent();
                        Fight_State = FIGHT_TRACK_SPIN;
                        Fight_TrackDir = dir;
                        Fight_LastTrackSide = DIR_LEFT;
                        Fight_EngageLost = 0;
                        Fight_StartTime = now;
                        break;
                    case DIR_RIGHT:
                    case DIR_BACK_RIGHT:
                        // 右侧/右后侧与左侧对称，锁定方向避免传感器边界来回抖动。
                        Motor_Ramp_SyncFromCurrent();
                        Fight_State = FIGHT_TRACK_SPIN;
                        Fight_TrackDir = dir;
                        Fight_LastTrackSide = DIR_RIGHT;
                        Fight_EngageLost = 0;
                        Fight_StartTime = now;
                        break;
                    case DIR_BACK:
                        // 正后方没有左右信息时，沿用最近一次左右侧甩头方向。
                        Motor_Ramp_SyncFromCurrent();
                        Fight_State = FIGHT_TRACK_SPIN;
                        Fight_TrackDir = (Fight_LastTrackSide == DIR_LEFT) ? DIR_BACK_LEFT : DIR_BACK_RIGHT;
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

        case FIGHT_FRONT_HOLD:
            // 正前保持期间忽略 IR4/IR11 的单侧变化，只允许其它敌人方向和安全链路打断。
            if(Fight_IsFrontHoldInterruptDir(dir))
            {
                Fight_FrontHoldConsumed = false;
                Fight_State = FIGHT_ENGAGE;
                Fight_StartTime = now;
                break;
            }
            Motor_Ramp_SetTarget(FIGHT_TRACK_PUSH_SPEED, FIGHT_TRACK_PUSH_SPEED);
            Motor_Ramp_Update();
            if(elapsed >= FIGHT_FRONT_HOLD_TIME)
            {
                Fight_State = FIGHT_ENGAGE;
                Fight_StartTime = now;
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

        case FIGHT_REAR_EDGE_STOP:
            // 后方边缘先等主动制动结束，再前进脱离，避免刹车和前进命令互相打架。
            if(!MOTOR_IsBraking() && elapsed >= FIGHT_EDGE_STOP_TIME)
            {
                Fight_State = FIGHT_REAR_ESCAPE;
                Fight_StartTime = now;
            }
            break;

        case FIGHT_REAR_ESCAPE:
            // 车尾接近边缘时向前冲出危险区，然后把控制权交回漫游。
            drive_user_defined(FIGHT_REAR_ESCAPE_SPEED, FIGHT_REAR_ESCAPE_SPEED);
            if(elapsed >= FIGHT_REAR_ESCAPE_TIME)
            {
                Fight_State = FIGHT_DONE;
            }
            break;

        /*======撤退状态======*/
        case FIGHT_RETREAT:
            Fight_DriveEdgeRetreat(elapsed);
            if(elapsed >= FIGHT_RETREAT_TIME)
            {
                Fight_State = FIGHT_TURN;
                Fight_StartTime = now;
            }
            break;

        /*======边缘恢复掉头状态======*/
        case FIGHT_TURN:
            if(Fight_ShouldTurnRight())
            {
                drive_user_defined(FIGHT_TURN_SPEED, -FIGHT_TURN_SPEED);
            }
            else
            {
                drive_user_defined(-FIGHT_TURN_SPEED, FIGHT_TURN_SPEED);
            }
            if(elapsed >= ((Fight_EdgeSide == FIGHT_EDGE_SIDE_NONE && Fight_EdgeEscapeArcDir == DIR_NONE) ? FIGHT_TURN_TIME : FIGHT_EDGE_TURN_TIME))
            {
                Fight_EdgeEscapeArcDir = DIR_NONE;
                Fight_EdgeSide = FIGHT_EDGE_SIDE_NONE;
                Fight_State = FIGHT_DONE;
            }
            break;

        /*======F/B回避先后退======*/
        case FIGHT_FB_TURN:
            drive_user_defined(-FIGHT_FB_RETREAT_SPEED, -FIGHT_FB_RETREAT_SPEED);
            if(elapsed >= FIGHT_FB_RETREAT_TIME)
            {
                Fight_State = FIGHT_FORWARD;
                Fight_StartTime = now;
            }
            break;

        /*======F/B回避后180°掉头======*/
        case FIGHT_FORWARD:
            drive_user_defined(-FIGHT_TURN_SPEED, FIGHT_TURN_SPEED);
            if(elapsed >= FIGHT_TURN_TIME)
            {
                Fight_State = FIGHT_FB_ADVANCE;
                Fight_StartTime = now;
            }
            break;

        /*======F/B回避后短前进======*/
        case FIGHT_FB_ADVANCE:
            drive_user_defined(FIGHT_FB_ADVANCE_SPEED, FIGHT_FB_ADVANCE_SPEED);
            if(elapsed >= FIGHT_FB_FORWARD_TIME)
            {
                Fight_State = FIGHT_DONE;
            }
            break;

        /*======侧后向追踪：侧后方前冲甩头，直到前扇区命中======*/
        case FIGHT_TRACK_SPIN:
            if(dir == DIR_FRONT || dir == DIR_FRONT_SLIGHT_LEFT || dir == DIR_FRONT_SLIGHT_RIGHT ||
                   dir == DIR_FRONT_LEFT || dir == DIR_FRONT_RIGHT)
            {
                // 甩头追踪一旦把目标带进前扇区，就回到 ENGAGE 让前方追踪逻辑接管。
                Fight_TrackDir = DIR_NONE;
                Fight_EngageLost = 0;
                Motor_Ramp_SyncFromCurrent();
                Fight_State = FIGHT_ENGAGE;
                Fight_StartTime = now;
                break;
            }

            if(dir == DIR_LEFT || dir == DIR_BACK_LEFT)
            {
                // 甩头过程中持续刷新同侧目标，避免短暂从侧边变后侧边时丢方向。
                Fight_TrackDir = dir;
                Fight_LastTrackSide = DIR_LEFT;
                Fight_EngageLost = 0;
            }
            else if(dir == DIR_RIGHT || dir == DIR_BACK_RIGHT)
            {
                // 右侧链路与左侧对称，保持最后一次有效侧向用于正后方兜底。
                Fight_TrackDir = dir;
                Fight_LastTrackSide = DIR_RIGHT;
                Fight_EngageLost = 0;
            }
            else if(dir == DIR_BACK)
            {
                // 正后方只有后向信息，按最近左右侧继续甩，直到重新看见前扇区。
                Fight_TrackDir = (Fight_LastTrackSide == DIR_LEFT) ? DIR_BACK_LEFT : DIR_BACK_RIGHT;
                Fight_EngageLost = 0;
            }
            else if(dir == DIR_NONE)
            {
                // 甩头期间短暂丢目标时继续沿锁定方向转，超过丢失时间才退出进攻。
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

            if(Fight_TrackDir == DIR_BACK_LEFT)
            {
                // 左后侧：左轮反转、右轮前进，车头向左后目标方向快速甩过去。
                Motor_Ramp_SetTarget(-FIGHT_TRACK_REAR_ARC_BACK_SPEED, FIGHT_TRACK_REAR_ARC_FORWARD_SPEED);
            }
            else if(Fight_TrackDir == DIR_BACK_RIGHT)
            {
                // 右后侧：右轮反转、左轮前进，和左后侧动作对称。
                Motor_Ramp_SetTarget(FIGHT_TRACK_REAR_ARC_FORWARD_SPEED, -FIGHT_TRACK_REAR_ARC_BACK_SPEED);
            }
            else if(Fight_TrackDir == DIR_LEFT)
            {
                // 正左侧：左轮反转、右轮前进，用原地甩头把目标带到前方。
                Motor_Ramp_SetTarget(-FIGHT_TRACK_SIDE_ARC_BACK_SPEED, FIGHT_TRACK_SIDE_ARC_FORWARD_SPEED);
            }
            else
            {
                // 默认按右侧处理，覆盖 DIR_RIGHT 以及异常未锁定但仍在 TRACK_SPIN 的情况。
                Motor_Ramp_SetTarget(FIGHT_TRACK_SIDE_ARC_FORWARD_SPEED, -FIGHT_TRACK_SIDE_ARC_BACK_SPEED);
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
