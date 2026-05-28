#include "robot_control.h"
#include "robot_up.h"
#include "robot_roaming.h"
#include "robot_fight.h"
#include "robot_backup.h"
#include "motor.h"
#include "vision_parser.h"

#define ROBOT_ATTACK_DIR_CONFIRM_COUNT 2U

static RobotState robot_state;
static EnemyDir robot_attack_candidate_dir = DIR_NONE;
static uint8_t robot_attack_candidate_count = 0;

static void Robot_Control_ResetAttackConfirm(void)
{
    robot_attack_candidate_dir = DIR_NONE;
    robot_attack_candidate_count = 0;
}

static void Robot_Control_EnterRoaming(void)
{
    Roaming_Init();
    Robot_Control_ResetAttackConfirm();
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
    Robot_Control_ResetAttackConfirm();
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
            if (enemy_dir == DIR_NONE)
            {
                Robot_Control_ResetAttackConfirm();
                break;
            }
            if (enemy_dir != robot_attack_candidate_dir)
            {
                robot_attack_candidate_dir = enemy_dir;
                robot_attack_candidate_count = 1;
                break;
            }
            if (robot_attack_candidate_count < ROBOT_ATTACK_DIR_CONFIRM_COUNT)
            {
                robot_attack_candidate_count++;
            }
            if (robot_attack_candidate_count >= ROBOT_ATTACK_DIR_CONFIRM_COUNT)
            {
                Fight_InitWithDir(enemy_dir);
                Robot_Control_ResetAttackConfirm();
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
