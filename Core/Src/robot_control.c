#include "robot_control.h"
#include "robot_up.h"
#include "robot_roaming.h"
#include "robot_fight.h"
#include "robot_backup.h"
#include "motor.h"
#include "vision_parser.h"

static RobotState robot_state;

static void Robot_Control_EnterRoaming(void)
{
    Roaming_Init();
    robot_state = ROBOT_ROAMING;
}

static void Robot_Control_EnterBackup(void)
{
    MOTOR_BrakeAll();
    Vision_SendCmd('D');
    Backup_Init();
    robot_state = ROBOT_BACKUP;
}

void Robot_Control_Init(void)
{
    GoUp_Init();
    robot_state = ROBOT_GO_UP;
}

void Robot_Control_Update(void)
{
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
            if (Fight_GetEnemyDir() != DIR_NONE)
            {
                Fight_Init();
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
                Vision_SendCmd('S');
                robot_state = ROBOT_ROAMING;
            }
            break;
    }
}
