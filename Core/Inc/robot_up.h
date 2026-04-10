#ifndef __ROBOT_UP_H
#define __ROBOT_UP_H

#include <stdbool.h>
#include "main.h"
#include "shade.h"

/*======非接触式启动======*/
/*引脚定义*/
#define STARTUP_LEFT_PIN IR_9_Pin
#define STARTUP_LEFT_PORT IR_9_GPIO_Port
#define STARTUP_RIGHT_PIN IR_7_Pin
#define STARTUP_RIGHT_PORT IR_7_GPIO_Port

/* 遮挡时传感器输出电平（遮挡=0，未遮挡=1）*/
#define STARTUP_BLOCKED_STATE GPIO_PIN_RESET
#define STARTUP_UNBLOCKED_STATE GPIO_PIN_SET

/*消抖时间*/
#define STARTUP_DEBOUNCE_MS 80

/*队伍颜色定义*/
typedef enum{
    TEAM_NONE = 0,
    TEAM_YELLOW, //黄色方 左侧遮挡
    TEAM_BLUE //蓝色方 右侧遮挡
}TeamColor;

extern TeamColor Current_Team;

/*======上台状态======*/
typedef enum{
    GOUP_RUSH,//梯度加速冲台
    GOUP_CONFIRM,//冲台结束后灰度确认已上台
    GOUP_TURN,//差速掉头
    GOUP_DONE
}GoUpState;

/*======时间参数======*/
#define GOUP_RUSH_TIME 1100
#define GOUP_RUSH_STAGE1_TIME 200
#define GOUP_RUSH_STAGE2_TIME 500
#define GOUP_TURN_TIME 400
#define GOUP_SHADE_CONFIRM_COUNT 2

/*======速度参数======*/
#define GOUP_RUSH_SPEED_STAGE1 500
#define GOUP_RUSH_SPEED_STAGE2 600
#define GOUP_RUSH_SPEED_STAGE3 700

/*======函数声明======*/
void GoUp_Init(void);
void GoUp_Update(void);
bool GoUp_IsDone(void);
TeamColor Startup_WaitForTrigger(void);
void Startup_Notify(TeamColor team);

#endif  