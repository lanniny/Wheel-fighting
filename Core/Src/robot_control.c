#include "robot_control.h"
#include "robot_up.h"
#include "robot_roaming.h"
#include "robot_fight.h"
#include "robot_backup.h"
#include "motor.h"
#include "obstacle.h"
#include "vision_parser.h"

static RobotState robot_state;
static uint8_t enemy_confirm_count = 0;

void Robot_Control_Init(void)
{
    GoUp_Init();
    robot_state = ROBOT_GO_UP;
    enemy_confirm_count = 0;
}

void Robot_Control_Update(void)
{
    switch (robot_state)
    {
        case ROBOT_GO_UP:
            GoUp_Update();
            if (GoUp_IsDone())
            {
                Roaming_Init();
                robot_state = ROBOT_ROAMING;
                enemy_confirm_count = 0;
            }
            break;

        case ROBOT_ROAMING:
        {
            Roaming_Update();

            /* 视觉引导: 仅前进状态下视觉检测到目标才切入格斗, 防止边缘回避被打断 */
            uint8_t vision_ok = (!Vision_IsTimeout() && vision_target.valid);
            if (Roaming_IsForward() && vision_ok
                && (vision_target.type == 'N' || vision_target.type == 'E'))
            {
                MOTOR_BrakeAll();
                Fight_Init();
                robot_state = ROBOT_ATTACK;
                enemy_confirm_count = 0;
                break;
            }

            /* 红外检测敌人, 连续2次确认后进入格斗 */
            EnemyDir dir = Fight_GetEnemyDir();
            if (dir != DIR_NONE)
            {
                enemy_confirm_count++;
                if (enemy_confirm_count >= 2)
                {
                    MOTOR_BrakeAll();
                    Fight_Init();
                    robot_state = ROBOT_ATTACK;
                    enemy_confirm_count = 0;
                }
            }
            else
            {
                enemy_confirm_count = 0;
            }

            if (Roaming_IsDone())
            {
                MOTOR_BrakeAll();
                Backup_Init();
                robot_state = ROBOT_BACKUP;
                enemy_confirm_count = 0;
            }
            break;
        }

        case ROBOT_ATTACK:
            Fight_Update();
            if (Fight_IsDown())
            {
                MOTOR_BrakeAll();
                Backup_Init();
                robot_state = ROBOT_BACKUP;
                enemy_confirm_count = 0;
                break;
            }
            if (Fight_IsDone())
            {
                Roaming_Init();
                robot_state = ROBOT_ROAMING;
                enemy_confirm_count = 0;
            }
            break;

        case ROBOT_BACKUP:
            Backup_Update();
            if (Backup_IsDone())
            {
                Roaming_Init();
                robot_state = ROBOT_ROAMING;
                enemy_confirm_count = 0;
            }
            break;
    }
}
