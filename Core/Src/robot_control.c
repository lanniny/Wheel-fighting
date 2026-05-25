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
    Vision_SendCmd('N');
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
            /* P3: 视觉引导攻击 — IR没触发但视觉看到敌方/中立目标且足够近 */
            else if (!Vision_IsTimeout() && vision_target.valid &&
                     (vision_target.type == 'E' || vision_target.type == 'N') &&
                     vision_target.area > FIGHT_VISION_ATTACK_MIN_AREA)
            {
                EnemyDir vdir;
                if (vision_target.dir < FIGHT_VISION_DIR_LEFT_THRESH)
                    vdir = DIR_FRONT_LEFT;
                else if (vision_target.dir > FIGHT_VISION_DIR_RIGHT_THRESH)
                    vdir = DIR_FRONT_RIGHT;
                else
                    vdir = DIR_FRONT;
                Fight_InitWithDir(vdir);
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
                Robot_Control_EnterRoaming();
            }
            break;
    }
}
