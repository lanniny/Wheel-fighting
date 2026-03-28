#ifndef __ROBOT_CONTROL_H
#define __ROBOT_CONTROL_H

#include "main.h"

typedef enum {
    ROBOT_GO_UP,     // 上台中
    ROBOT_ROAMING,   // 巡台寻敌
    ROBOT_ATTACK,    // 进攻
    ROBOT_BACKUP     // 掉台回台
} RobotState;

void Robot_Control_Init(void);
void Robot_Control_Update(void);

#endif
