/*
 * @file robot_backup.h
 * @brief 掉台后回台恢复模块接口与时间参数
 */
#ifndef __ROBOT_BACKUP_H
#define __ROBOT_BACKUP_H

#include "main.h"
#include <stdbool.h>

#define BACKUP_FORWARD_TIME_MS   1000
#define BACKUP_BACK_TIME_MS      2000
#define BACKUP_ESCAPE_BACK_TIME_MS 300
#define BACKUP_TURN_TIME_MS      550

void Backup_Init(void);
void Backup_Update(void);
bool Backup_IsDone(void);
uint8_t Backup_DebugGetStage(void);
char Backup_DebugGetStableVisionType(void);
uint8_t Backup_DebugGetVisionCount(void);

#endif // __ROBOT_BACKUP_H
