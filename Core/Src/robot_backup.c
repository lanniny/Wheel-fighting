#include "robot_backup.h"
#include "robot_roaming.h"
#include "motor.h"
#include "obstacle.h"
#include "shade.h"
#include "vision_parser.h"

extern float voltage[2];

typedef enum {
    BACKUP_SPIN = 0,
    BACKUP_RUSH_FORWARD,
    BACKUP_RUSH_BACK
} BackupStage_t;

static BackupStage_t Backup_Stage = BACKUP_SPIN;
static uint32_t Backup_StartTime = 0;
static bool Backup_Done = false;

static void Backup_FinishRecovery(uint32_t current_time)
{
    Vision_SendCmd('S');  /* 通知视觉: 回台成功, 恢复正常检测 */
    drive_Left_M();
    HAL_Delay(200);
    MOTOR_StopAll();
    Backup_Done = true;
    Backup_Stage = BACKUP_SPIN;
    Backup_StartTime = current_time;
    Roaming_Init();
}

static void Backup_SwitchStage(BackupStage_t next_stage, uint32_t current_time)
{
    Backup_Stage = next_stage;
    Backup_StartTime = current_time;
}

static int Backup_IsOnStage(void)
{
    return (voltage[0] < 2.7f && voltage[1] < 2.7f);
}

static int Backup_FrontAlignReady(void)
{
    return (Obs_Data.IR2 == RESET && Obs_Data.IR5 == SET &&
            Obs_Data.IR7 == SET);
}

static int Backup_ShouldRushBack(void)
{
    return (Obs_Data.IR1 == RESET && Obs_Data.IR3 == RESET);
}

void Backup_Init(void)
{
    Backup_Stage = BACKUP_SPIN;
    Backup_StartTime = HAL_GetTick();
    Backup_Done = false;
    Vision_SendCmd('D');  /* 通知视觉: 进入掉台回复, 切换黑色检测 */
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
            drive_Left_L();
            /* 视觉辅助对齐: 'G'=台面在正前方, 与光电条件取或 */
            if(Backup_FrontAlignReady()
               || (!Vision_IsTimeout() && vision_target.type == 'G'))
            {
                Backup_SwitchStage(BACKUP_RUSH_FORWARD, current_time);
            }
            break;

        case BACKUP_RUSH_FORWARD:
            drive_For_L();
            if(Backup_ShouldRushBack())
            {
                Backup_SwitchStage(BACKUP_RUSH_BACK, current_time);
            }
            else if(elapsed_time >= BACKUP_FORWARD_TIME_MS)
            {
                Backup_SwitchStage(BACKUP_RUSH_BACK, current_time);
            }
            break;

        case BACKUP_RUSH_BACK:
            drive_Back_H();
            if(elapsed_time < BACKUP_BACK_TIME_MS)
            {
                break;
            }
            site_detect_shade();
            if(Backup_IsOnStage())
            {
                Backup_FinishRecovery(current_time);
            }
            else
            {
                Backup_SwitchStage(BACKUP_SPIN, current_time);
            }
            break;

        default:
            Backup_Stage = BACKUP_SPIN;
            Backup_StartTime = current_time;
            break;
    }
}

bool Backup_IsDone(void)
{
    return Backup_Done;
}
