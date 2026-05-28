#include "robot_control.h"
#include "robot_up.h"
#include "robot_roaming.h"
#include "robot_fight.h"
#include "robot_backup.h"
#include "motor.h"

static RobotState robot_state;

static void Robot_Control_EnterRoaming(void)
{
    Roaming_Init();
    robot_state = ROBOT_ROAMING;
}

static void Robot_Control_EnterBackup(void)
{
    MOTOR_BrakeAll();
    Backup_Init();
    robot_state = ROBOT_BACKUP;
}

void Robot_Control_Init(void)
{
    GoUp_Init();
    robot_state = ROBOT_GO_UP;
}

RobotState Robot_Control_GetState(void)
{
    return robot_state;
}

void Robot_Control_Update(void)
{
    EnemyDir enemy_dir;

    switch (robot_state)
    {
        case ROBOT_GO_UP:
            GoUp_Update();
            if (GoUp_IsDone())
            {
                Robot_Control_EnterRoaming();
            }
            break;

        case ROBOT_ROAMING:
            Roaming_Update();
            if (Roaming_IsDone())
            {
                Robot_Control_EnterBackup();
                break;
            }
            enemy_dir = Fight_GetEnemyDir();
            if (enemy_dir != DIR_NONE)
            {
                Fight_InitWithDir(enemy_dir);
                robot_state = ROBOT_ATTACK;
            }
            break;

        case ROBOT_ATTACK:
            Fight_Update();
            if(Fight_IsDown())
            {
                Robot_Control_EnterBackup();
                break;
            }
            if (Fight_IsDone())
            {
                Robot_Control_EnterRoaming();
                break;
            }
            break;

        case ROBOT_BACKUP:
            Backup_Update();
            if (Backup_IsDone())
            {
                robot_state = ROBOT_ROAMING;
            }
            break;
    }
}
