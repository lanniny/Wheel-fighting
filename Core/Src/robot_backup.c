/*
 * @file robot_backup.c
 * @brief 掉台后回台恢复状态机
 *
 * 整体思路：
 * 1. SPIN 原地左转，找到允许前冲修姿的朝向；
 * 2. RUSH_FORWARD 前冲修正姿态；
 * 3. 前冲结束后，若视觉稳定识别到 'G'，则直接进入 RUSH_BACK 后退冲台；
 * 4. 若视觉未满足，则进入 ESCAPE_BACK / ESCAPE_TURN 退让并换角度后回到 RUSH_FORWARD 重试；
 * 5. RUSH_BACK 过程中用双灰度连续确认是否已回到台上；
 * 6. FINISH_TURN 上台后再做一次收尾左转，随后交回漫游模块。
 */
#include "robot_backup.h"
#include "robot_roaming.h"
#include "robot_up.h"
#include "motor.h"
#include "obstacle.h"
#include "shade.h"
#include "vision_parser.h"


typedef enum {
    BACKUP_SPIN = 0,
    BACKUP_RUSH_FORWARD,
    BACKUP_RUSH_BACK,
    BACKUP_ESCAPE_BACK,
    BACKUP_ESCAPE_TURN,
    BACKUP_FINISH_TURN
} BackupStage_t;

static BackupStage_t Backup_Stage = BACKUP_SPIN;  // 当前回台状态机阶段
static uint32_t Backup_StartTime = 0;             // 当前阶段开始时刻，用于阶段超时判断
static bool Backup_Done = false;                  // 本轮回台是否完成
static uint8_t Backup_OnStageCount = 0;           // 已上台连续确认计数（灰度去抖）
static uint8_t Backup_FrontAlignCount = 0;        // 前向对正连续确认计数（IR去抖）
static char Backup_PrevVisionType = 'X';          // 上一帧视觉类型（用于 backup 视觉消抖）
static char Backup_StableVisionType = 'X';        // backup 使用的稳定视觉类型
static uint8_t Backup_VisionTypeCount = 0;        // 视觉类型连续命中计数

#define BACKUP_VISION_CONFIRM_COUNT 2        // 视觉 G 连续确认次数
#define BACKUP_FRONT_ALIGN_CONFIRM_COUNT 3   // IR 前向对正确认次数
#define BACKUP_FORWARD_LEFT_SPEED 200        // 前冲修姿时左轮速度
#define BACKUP_FORWARD_RIGHT_SPEED 400       // 前冲修姿时右轮速度

/**
 * @brief 进入回台收尾阶段
 * @param current_time 当前时刻
 */
static void Backup_FinishRecovery(uint32_t current_time)
{
    Backup_OnStageCount = 0;
    Backup_Stage = BACKUP_FINISH_TURN;
    Backup_StartTime = current_time;
}

/**
 * @brief 切换回台阶段并刷新阶段计时
 * @param next_stage   下一阶段
 * @param current_time 当前时刻
 */
static void Backup_SwitchStage(BackupStage_t next_stage, uint32_t current_time)
{
    Backup_Stage = next_stage;
    Backup_StartTime = current_time;
    Backup_FrontAlignCount = 0;
}

/**
 * @brief 判断是否已回到台上
 * @return 1=V0/V1 同时低于阈值，认为已在台上；0=仍在台下
 */
static int Backup_IsOnStage(void)
{
    return (voltage_v0 < 2.8f && voltage_v1 < 2.8f);
}

/**
 * @brief 判断车头是否已经对准可前冲修姿的方向
 * @return 1=连续多次确认通过；0=仍需继续原地左转搜索
 *
 * 当前使用 IR7/IR9/IR4 组合做前向对正：
 * - IR7/IR9 为 SET：表示两侧对齐参考满足
 * - IR4 为 RESET：表示正前检测到目标/围栏方向
 */
static int Backup_FrontAlignReady(void)
{
    if(Obs_Data.IR7 == SET &&
       Obs_Data.IR9 == SET &&
       Obs_Data.IR4 == RESET)
    {
        if(Backup_FrontAlignCount < BACKUP_FRONT_ALIGN_CONFIRM_COUNT)
        {
            Backup_FrontAlignCount++;
        }
    }
    else
    {
        Backup_FrontAlignCount = 0;
    }

    return (Backup_FrontAlignCount >= BACKUP_FRONT_ALIGN_CONFIRM_COUNT);
}

/**
 * @brief 获取 backup 使用的稳定视觉类型
 * @return 连续确认后的稳定视觉类型；超时/无效时返回 'X'
 */
static char Backup_GetStableVisionType(void)
{
    if (Vision_IsTimeout() || !vision_target.valid)
    {
        Backup_PrevVisionType = 'X';
        Backup_StableVisionType = 'X';
        Backup_VisionTypeCount = 0;
        return 'X';
    }

    if (vision_target.type != Backup_PrevVisionType)
    {
        Backup_PrevVisionType = vision_target.type;
        Backup_VisionTypeCount = 1;
    }
    else if (Backup_VisionTypeCount < BACKUP_VISION_CONFIRM_COUNT)
    {
        Backup_VisionTypeCount++;
    }

    if (Backup_VisionTypeCount >= BACKUP_VISION_CONFIRM_COUNT)
    {
        Backup_StableVisionType = Backup_PrevVisionType;
    }

    return Backup_StableVisionType;
}

/**
 * @brief 判断视觉是否稳定识别到可直接后退冲台的目标
 * @return 1=稳定识别到 'G'；0=视觉仍未满足
 */
static bool Backup_VisionWallReady(void)
{
    return (Backup_GetStableVisionType() == 'G');
}

/**
 * @brief 处理前冲超时后的分支
 * @param current_time 当前时刻
 *
 * 规则：
 * - 若视觉稳定识别到 'G'，说明当前姿态允许直接后退冲台，进入 RUSH_BACK；
 * - 否则先短后退脱离，再转向重试。
 */
static void Backup_HandleForwardTimeout(uint32_t current_time)
{
    if(Backup_VisionWallReady())
    {
        Backup_SwitchStage(BACKUP_RUSH_BACK, current_time);
    }
    else
    {
        drive_Back_L();
        Backup_SwitchStage(BACKUP_ESCAPE_BACK, current_time);
    }
}

/**
 * @brief 在允许的灰度确认阶段尝试判定是否已成功上台
 * @param current_time 当前时刻
 * @return 1=已判定上台并切入 FINISH_TURN；0=尚未上台
 */
static int Backup_TryFinishIfOnStage(uint32_t current_time)
{
    site_detect_shade();
    if(Backup_IsOnStage())
    {
        if(Backup_OnStageCount < 2)
        {
            Backup_OnStageCount++;
        }
    }
    else
    {
        Backup_OnStageCount = 0;
    }

    if(Backup_OnStageCount >= 2)
    {
        Backup_FinishRecovery(current_time);
        return 1;
    }

    return 0;
}

void Backup_Init(void)
{
    Backup_Stage = BACKUP_SPIN;
    Backup_StartTime = HAL_GetTick();
    Backup_Done = false;
    Backup_OnStageCount = 0;
    Backup_FrontAlignCount = 0;
    Backup_PrevVisionType = 'X';
    Backup_StableVisionType = 'X';
    Backup_VisionTypeCount = 0;
}

void Backup_Update(void)
{
    uint32_t current_time = HAL_GetTick();
    uint32_t elapsed_time = current_time - Backup_StartTime;

    if(Backup_Done)
    {
        return;
    }

    Obs_Sensor_ReadAll();

    switch (Backup_Stage)
    {
        case BACKUP_SPIN:
            /* 原地左转搜索可前冲修姿的朝向 */
            drive_Left_L();
            if(Backup_FrontAlignReady())
            {
                Backup_SwitchStage(BACKUP_RUSH_FORWARD, current_time);
            }
            break;

        case BACKUP_RUSH_FORWARD:
            /* 以前冲修正姿态，等待前冲结束后再判断是否可直接后冲上台 */
            drive_user_defined(BACKUP_FORWARD_LEFT_SPEED, BACKUP_FORWARD_RIGHT_SPEED);
            if(Backup_TryFinishIfOnStage(current_time))
            {
                return;
            }
            if(elapsed_time >= BACKUP_FORWARD_TIME_MS)
            {
                Backup_HandleForwardTimeout(current_time);
            }
            break;

        case BACKUP_ESCAPE_BACK:
            /* 当前姿态不允许直接后冲，先短后退拉开距离 */
            drive_Back_L();
            if(Backup_TryFinishIfOnStage(current_time))
            {
                return;
            }
            if(elapsed_time >= BACKUP_ESCAPE_BACK_TIME_MS)
            {
                Backup_SwitchStage(BACKUP_ESCAPE_TURN, current_time);
            }
            break;

        case BACKUP_ESCAPE_TURN:
            /* 短后退后快速左转，换一个入台角度后重新进入前冲修姿 */
            drive_Left_S();
            if(elapsed_time >= BACKUP_TURN_TIME_MS)
            {
                Backup_SwitchStage(BACKUP_RUSH_FORWARD, current_time);
            }
            break;

        case BACKUP_RUSH_BACK:
            /* 复用上台模块的三段梯度后退，逐步加大后冲力度 */
            if(elapsed_time < GOUP_RUSH_STAGE1_TIME)
            {
                drive_user_defined(-500, -500);
            }
            else if(elapsed_time < GOUP_RUSH_STAGE2_TIME)
            {
                drive_user_defined(-600, -600);
            }
            else
            {
                drive_user_defined(-700, -700);
            }
            if(Backup_TryFinishIfOnStage(current_time))
            {
                return;
            }
            if(elapsed_time < BACKUP_BACK_TIME_MS)
            {
                break;
            }
            /* 后冲总时长结束仍未上台，则回到 SPIN 重新开始一轮搜索 */
            Backup_OnStageCount = 0;
            Backup_SwitchStage(BACKUP_SPIN, current_time);
            break;

        case BACKUP_FINISH_TURN:
            /* 已确认回台后，再左转收尾，随后交回漫游模块 */
            drive_Left_S();
            if(elapsed_time >= BACKUP_TURN_TIME_MS)
            {
                MOTOR_StopAll();
                Backup_Done = true;
                Roaming_Init();
            }
            break;

        default:
            /* 异常分支统一回到 SPIN，避免状态机卡死 */
            Backup_SwitchStage(BACKUP_SPIN, current_time);
            break;
    }
}

bool Backup_IsDone(void)
{
    return Backup_Done;
}

uint8_t Backup_DebugGetStage(void)
{
    return (uint8_t)Backup_Stage;
}

char Backup_DebugGetStableVisionType(void)
{
    return Backup_StableVisionType;
}

uint8_t Backup_DebugGetVisionCount(void)
{
    return Backup_VisionTypeCount;
}
