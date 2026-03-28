#ifndef ROBOT_FIGHT_H
#define ROBOT_FIGHT_H

#include "main.h"
#include <stdbool.h>

/*======引脚配置======*/
#define FIGHT_IR_NW_PIN IR_3_Pin //左前角
#define FIGHT_IR_NW_PORT      IR_3_GPIO_Port
#define FIGHT_IR_NE_PIN IR_4_Pin //右前角
#define FIGHT_IR_NE_PORT      IR_4_GPIO_Port
#define FIGHT_IR_L_PIN IR_5_Pin //左侧角
#define FIGHT_IR_L_PORT       IR_5_GPIO_Port
#define FIGHT_IR_R_PIN IR_6_Pin //右侧角
#define FIGHT_IR_R_PORT       IR_6_GPIO_Port
#define FIGHT_IR_SW_PIN IR_7_Pin //左后角
#define FIGHT_IR_SW_PORT      IR_7_GPIO_Port
#define FIGHT_IR_SE_PIN IR_8_Pin //右后角
#define FIGHT_IR_SE_PORT      IR_8_GPIO_Port
#define FIGHT_IR_FRONT_PIN    IR_9_Pin //正前方
#define FIGHT_IR_FRONT_PORT   IR_9_GPIO_Port
#define FIGHT_IR_BACK_PIN     IR_10_Pin //正后方
#define FIGHT_IR_BACK_PORT    IR_10_GPIO_Port

/*======触发电平======*/
#define FIGHT_IR_TRIGGERED GPIO_PIN_SET

/*======时间参数(ms)======*/
#define FIGHT_ENGAGE_TIMEOUT 3000 //交战时间
#define FIGHT_ENGAGE_LOST 500 //交战丢失时间
#define FIGHT_RETREAT_TIME 400 //撤退时间
#define FIGHT_TURN_TIME 550 //后退后掉头时间

/*======敌人（能量块）方向======*/
typedef enum{
    DIR_NONE = 0,
    DIR_FRONT, //正前方
    DIR_FRONT_LEFT, //左前方
    DIR_FRONT_RIGHT, //右前方
    DIR_LEFT, //正左
    DIR_RIGHT, //正右
    DIR_BACK_LEFT, //左后方
    DIR_BACK_RIGHT, //右后方
    DIR_BACK, //正后方
}EnemyDir;

/*======进攻状态======*/
typedef enum{
    FIGHT_ENGAGE, //交战
    FIGHT_RETREAT, //后退脱离
    FIGHT_TURN, //后退后掉头面向敌人
    FIGHT_DONE, //交还控制权回漫游
}FightState;

/* ============ 函数声明 ============ */
void     Fight_Init(void);
void     Fight_Update(void);
bool     Fight_IsDone(void);
EnemyDir Fight_GetEnemyDir(void);       // 获取敌人方向

#endif // ROBOT_FIGHT_H