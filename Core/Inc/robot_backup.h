/*
 * @file robot_backup.h
 * @brief 掉台后回台恢复模块接口与时间参数
 */
#ifndef __ROBOT_BACKUP_H
#define __ROBOT_BACKUP_H

#include "main.h"
#include <stdbool.h>

#define BACKUP_YAW_TOLERANCE_DEG        6.0f
#define BACKUP_SPIN_FORWARD_TIME_MS     300
#define BACKUP_SPIN_TIMEOUT_MS          2000
#define BACKUP_PROBE_FORWARD_TIME_MS    300
#define BACKUP_WALL_PRESS_TIME_MS       200
#define BACKUP_WALL_RUSH_TIME_MS        1000
#define BACKUP_TURN_180_TIMEOUT_MS      2000
#define BACKUP_BACK_TIME_MS             1500
#define BACKUP_POST_RUSH_CHECK_TIME_MS  20
#define BACKUP_ESCAPE_BACK_TIME_MS      300
#define BACKUP_TURN_TIME_MS             550
#define BACKUP_FORWARD_SPEED            300
#define BACKUP_ESCAPE_BACK_SPEED        200

void Backup_Init(void);
void Backup_Update(void);
bool Backup_IsDone(void);
uint8_t Backup_DebugGetStage(void);
char Backup_DebugGetStableVisionType(void);

#endif // __ROBOT_BACKUP_H
