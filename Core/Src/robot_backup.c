/*
 * @file robot_backup.c
 * @brief 掉台后回台恢复状态机
 */
#include "robot_backup.h"
#include "robot_roaming.h"
#include "robot_up.h"
#include "motor.h"
#include "obstacle.h"
#include "shade.h"
#include "jy62.h"

#define BACKUP_WALL_BLOCK_CONFIRM_COUNT 3u

typedef enum {
    BACKUP_SPIN = 0,
    BACKUP_SPIN_FORWARD,
    BACKUP_PROBE_FORWARD,
    BACKUP_WALL_PRESS,
    BACKUP_ESCAPE_BACK,
    BACKUP_TURN_180,
    BACKUP_WALL_RUSH_FORWARD,
    BACKUP_RUSH_BACK,
    BACKUP_POST_RUSH_CHECK,
    BACKUP_FINISH_TURN
} BackupStage_t;

static BackupStage_t Backup_Stage = BACKUP_SPIN;
static uint32_t Backup_StartTime = 0;
static bool Backup_Done = false;
static uint8_t Backup_OnStageCount = 0;
static uint8_t Backup_WallBlockedCount = 0;
static float Backup_TurnTargetYaw = 0.0f;

static const float Backup_SearchYawTargets[] = {0.0f, 90.0f, 180.0f, -90.0f};

static float Backup_NormalizeYaw(float yaw)
{
    while(yaw > 180.0f)
    {
        yaw -= 360.0f;
    }

    while(yaw < -180.0f)
    {
        yaw += 360.0f;
    }

    return yaw;
}

static float Backup_YawError(float target, float current)
{
    return Backup_NormalizeYaw(target - current);
}

static float Backup_AbsFloat(float value)
{
    return (value < 0.0f) ? -value : value;
}

static void Backup_FinishRecovery(uint32_t current_time)
{
    Backup_OnStageCount = 0;
    Backup_Stage = BACKUP_FINISH_TURN;
    Backup_StartTime = current_time;
}

static void Backup_SwitchStage(BackupStage_t next_stage, uint32_t current_time)
{
    if(next_stage != BACKUP_PROBE_FORWARD)
    {
        Backup_WallBlockedCount = 0;
    }

    Backup_Stage = next_stage;
    Backup_StartTime = current_time;
}

static void Backup_StartTurn180(uint32_t current_time)
{
    Backup_TurnTargetYaw = Backup_NormalizeYaw(jy62_data.yaw_deg + 180.0f);
    Backup_SwitchStage(BACKUP_TURN_180, current_time);
}

static int Backup_IsOnStage(void)
{
    return (voltage_v0 < 2.8f && voltage_v1 < 2.8f);
}

static int Backup_IsFrontBoundaryBlocked(void)
{
    return (Obs_Data.IR4 == OBS_BLOCKED_STATE && Obs_Data.IR11 == OBS_BLOCKED_STATE);
}

static int Backup_IsWallBlocked(void)
{
    return (Obs_Data.IR3 == OBS_BLOCKED_STATE && Obs_Data.IR5 == OBS_BLOCKED_STATE);
}

static int Backup_IsWallBlockedConfirmed(void)
{
    if(Backup_IsWallBlocked())
    {
        if(Backup_WallBlockedCount < BACKUP_WALL_BLOCK_CONFIRM_COUNT)
        {
            Backup_WallBlockedCount++;
        }
    }
    else
    {
        Backup_WallBlockedCount = 0;
    }

    return (Backup_WallBlockedCount >= BACKUP_WALL_BLOCK_CONFIRM_COUNT);
}

static int Backup_IsSearchYawReady(void)
{
    uint8_t i;

    if(!JY62_IsOnline())
    {
        return 0;
    }

    for(i = 0u; i < (sizeof(Backup_SearchYawTargets) / sizeof(Backup_SearchYawTargets[0])); i++)
    {
        if(Backup_AbsFloat(Backup_YawError(Backup_SearchYawTargets[i], jy62_data.yaw_deg)) <= BACKUP_YAW_TOLERANCE_DEG)
        {
            return 1;
        }
    }

    return 0;
}

static int Backup_SearchReady(void)
{
    return (Backup_IsSearchYawReady() && Backup_IsFrontBoundaryBlocked());
}

static int Backup_Turn180Ready(void)
{
    if(!JY62_IsOnline())
    {
        return 0;
    }

    return (Backup_AbsFloat(Backup_YawError(Backup_TurnTargetYaw, jy62_data.yaw_deg)) <= BACKUP_YAW_TOLERANCE_DEG);
}

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
    Backup_WallBlockedCount = 0;
    Backup_TurnTargetYaw = 0.0f;
}

void Backup_Update(void)
{
    uint32_t current_time;
    uint32_t elapsed_time;

    JY62_Update();

    current_time = HAL_GetTick();
    elapsed_time = current_time - Backup_StartTime;

    if(Backup_Done)
    {
        return;
    }

    Obs_Sensor_ReadAll();

    switch (Backup_Stage)
    {
        case BACKUP_SPIN:
            drive_Left_L();
            if(Backup_SearchReady())
            {
                Backup_SwitchStage(BACKUP_PROBE_FORWARD, current_time);
                break;
            }
            if(elapsed_time >= BACKUP_SPIN_TIMEOUT_MS)
            {
                Backup_SwitchStage(BACKUP_SPIN_FORWARD, current_time);
            }
            break;

        case BACKUP_SPIN_FORWARD:
            drive_user_defined(BACKUP_FORWARD_SPEED, BACKUP_FORWARD_SPEED);
            if(Backup_TryFinishIfOnStage(current_time))
            {
                return;
            }
            if(elapsed_time >= BACKUP_SPIN_FORWARD_TIME_MS)
            {
                Backup_SwitchStage(BACKUP_SPIN, current_time);
            }
            break;

        case BACKUP_PROBE_FORWARD:
            if(elapsed_time < BACKUP_PROBE_FORWARD_TIME_MS)
            {
                drive_user_defined(BACKUP_FORWARD_SPEED, BACKUP_FORWARD_SPEED);
            }
            else
            {
                MOTOR_StopAll();
            }

            if(Backup_TryFinishIfOnStage(current_time))
            {
                return;
            }

            if(elapsed_time >= BACKUP_PROBE_FORWARD_TIME_MS)
            {
                if(Backup_IsWallBlockedConfirmed())
                {
                    Backup_SwitchStage(BACKUP_WALL_PRESS, current_time);
                    break;
                }

                if(Backup_WallBlockedCount == 0u)
                {
                    drive_user_defined(-BACKUP_ESCAPE_BACK_SPEED, -BACKUP_ESCAPE_BACK_SPEED);
                    Backup_SwitchStage(BACKUP_ESCAPE_BACK, current_time);
                }
            }
            break;

        case BACKUP_WALL_PRESS:
            drive_user_defined(BACKUP_FORWARD_SPEED, BACKUP_FORWARD_SPEED);
            if(elapsed_time >= BACKUP_WALL_PRESS_TIME_MS)
            {
                Backup_SwitchStage(BACKUP_RUSH_BACK, current_time);
            }
            break;

        case BACKUP_ESCAPE_BACK:
            drive_user_defined(-BACKUP_ESCAPE_BACK_SPEED, -BACKUP_ESCAPE_BACK_SPEED);
            if(Backup_TryFinishIfOnStage(current_time))
            {
                return;
            }
            if(elapsed_time >= BACKUP_ESCAPE_BACK_TIME_MS)
            {
                Backup_StartTurn180(current_time);
            }
            break;

        case BACKUP_TURN_180:
            drive_Left_M();
            if(Backup_Turn180Ready())
            {
                Backup_SwitchStage(BACKUP_WALL_RUSH_FORWARD, current_time);
                break;
            }
            if(elapsed_time >= BACKUP_TURN_180_TIMEOUT_MS)
            {
                Backup_SwitchStage(BACKUP_SPIN, current_time);
            }
            break;

        case BACKUP_WALL_RUSH_FORWARD:
            drive_user_defined(BACKUP_FORWARD_SPEED, BACKUP_FORWARD_SPEED);
            if(Backup_TryFinishIfOnStage(current_time))
            {
                return;
            }
            if(elapsed_time >= BACKUP_WALL_RUSH_TIME_MS)
            {
                Backup_SwitchStage(BACKUP_RUSH_BACK, current_time);
            }
            break;

        case BACKUP_RUSH_BACK:
            if(elapsed_time < GOUP_RUSH_STAGE1_TIME)
            {
                drive_user_defined(-500, -500);
            }
            else if(elapsed_time < GOUP_RUSH_STAGE2_TIME)
            {
                drive_user_defined(-550, -550);
            }
            else if(elapsed_time < BACKUP_BACK_TIME_MS)
            {
                drive_user_defined(-600, -600);
            }
            else
            {
                MOTOR_StopAll();
                Backup_OnStageCount = 0;
                Backup_SwitchStage(BACKUP_POST_RUSH_CHECK, current_time);
            }
            break;

        case BACKUP_POST_RUSH_CHECK:
            MOTOR_StopAll();
            if(Backup_TryFinishIfOnStage(current_time))
            {
                return;
            }
            if(elapsed_time >= BACKUP_POST_RUSH_CHECK_TIME_MS)
            {
                Backup_OnStageCount = 0;
                Backup_SwitchStage(BACKUP_SPIN, current_time);
            }
            break;

        case BACKUP_FINISH_TURN:
            drive_Left_S();
            if(elapsed_time >= BACKUP_TURN_TIME_MS)
            {
                MOTOR_StopAll();
                Backup_Done = true;
                Roaming_Init();
            }
            break;

        default:
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
    return 'X';
}
