#ifndef ROBOT_FIGHT_H
#define ROBOT_FIGHT_H

#include "main.h"
#include <stdbool.h>

/*======引脚配置 (以远程仓库实际接线为准)======*/
#define FIGHT_IR_NW_PIN       IR_3_Pin       /* 左前角 */
#define FIGHT_IR_NW_PORT      IR_3_GPIO_Port
#define FIGHT_IR_NE_PIN       IR_5_Pin       /* 右前角 */
#define FIGHT_IR_NE_PORT      IR_5_GPIO_Port
#define FIGHT_IR_L_PIN        IR_10_Pin      /* 左侧 */
#define FIGHT_IR_L_PORT       IR_10_GPIO_Port
#define FIGHT_IR_R_PIN        IR_6_Pin       /* 右侧 */
#define FIGHT_IR_R_PORT       IR_6_GPIO_Port
#define FIGHT_IR_SW_PIN       IR_9_Pin       /* 左后角 */
#define FIGHT_IR_SW_PORT      IR_9_GPIO_Port
#define FIGHT_IR_SE_PIN       IR_7_Pin       /* 右后角 */
#define FIGHT_IR_SE_PORT      IR_7_GPIO_Port
#define FIGHT_IR_FRONT_PIN    IR_4_Pin       /* 正前方 */
#define FIGHT_IR_FRONT_PORT   IR_4_GPIO_Port
#define FIGHT_IR_BACK_PIN     IR_8_Pin       /* 正后方 */
#define FIGHT_IR_BACK_PORT    IR_8_GPIO_Port

/*======触发电平 (远程: RESET=有障碍物)======*/
#define FIGHT_IR_TRIGGERED GPIO_PIN_RESET

/*======时间参数(ms)======*/
#define FIGHT_ENGAGE_TIMEOUT       3000  /* 交战超时 */
#define FIGHT_ENGAGE_LOST          500   /* 目标丢失超时 */
#define FIGHT_RETREAT_TIME         400   /* 边缘后退时间 */
#define FIGHT_TURN_TIME            550   /* 掉头时间(180°) */
#define FIGHT_FB_FORWARD_TIME      400   /* F/B回避后前进时间 */
#define FIGHT_FB_REAR_IGNORE_TIME  800   /* F/B掉头后后方光电忽略时间 */
#define FIGHT_VISION_CONFIRM_COUNT 2     /* 视觉类型消抖次数 */

/*======敌人（能量块）方向======*/
typedef enum{
    DIR_NONE = 0,
    DIR_FRONT,       /* 正前方 */
    DIR_FRONT_LEFT,  /* 左前方 */
    DIR_FRONT_RIGHT, /* 右前方 */
    DIR_LEFT,        /* 正左 */
    DIR_RIGHT,       /* 正右 */
    DIR_BACK_LEFT,   /* 左后方 */
    DIR_BACK_RIGHT,  /* 右后方 */
    DIR_BACK,        /* 正后方 */
}EnemyDir;

/*======进攻状态======*/
typedef enum{
    FIGHT_ENGAGE,    /* 交战 */
    FIGHT_RETREAT,   /* 边缘后退脱离 */
    FIGHT_TURN,      /* 掉头180° */
    FIGHT_FORWARD,   /* F/B回避后短前进 */
    FIGHT_DONE,      /* 交还控制权回漫游 */
}FightState;

/* ============ 函数声明 ============ */
void     Fight_Init(void);
void     Fight_Update(void);
bool     Fight_IsDone(void);
bool     Fight_IsDown(void);
EnemyDir Fight_GetEnemyDir(void);

#endif // ROBOT_FIGHT_H