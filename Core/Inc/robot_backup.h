#ifndef __ROBOT_BACKUP_H
#define __ROBOT_BACKUP_H

#include "main.h"
#include <stdbool.h>

#define BACKUP_SPIN_TIME_MS      500
#define BACKUP_FORWARD_TIME_MS   800
#define BACKUP_BACK_TIME_MS      500

void Backup_Init(void);
void Backup_Update(void);
bool Backup_IsDone(void);

#endif // __ROBOT_BACKUP_H
