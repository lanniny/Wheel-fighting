#ifndef ROBOT_FIGHT_H
#define ROBOT_FIGHT_H

#include "main.h"
#include <stdbool.h>

/*======时间参数(ms)======*/
#define FIGHT_ENGAGE_TIMEOUT 3000 //交战时间
#define FIGHT_ENGAGE_LOST 300 //交战丢失时间
#define FIGHT_EDGE_STOP_TIME 15 //边缘确认后停顿时间
#define FIGHT_FRONT_HOLD_TIME 300U //IR4+IR11双遮挡后正前直推保持时间
#define FIGHT_REAR_EDGE_CONFIRM_COUNT 2 //后方边缘连续确认次数
#define FIGHT_REAR_ESCAPE_TIME 300 //后方边缘触发后的前进脱离时间
#define FIGHT_REAR_ESCAPE_SPEED 450 //后方边缘前进脱离速度
#define FIGHT_RETREAT_TIME 550 //前边缘直退时间
#define FIGHT_RETREAT_SOFT_TIME 250 //前边缘初段中速后退时间
#define FIGHT_RETREAT_SOFT_SPEED 250 //前边缘初段中速后退速度
#define FIGHT_RETREAT_SPEED 430 //前边缘后退基础速度
#define FIGHT_EDGE_RETREAT_DIFF 100 //单侧前边缘小弧后退时的左右轮差速
#define FIGHT_TURN_TIME   550 //普通掉头时间(180°)
#define FIGHT_EDGE_TURN_TIME 320 //单侧边缘逃逸后的短转时间
#define FIGHT_EDGE_ARC_MEMORY_MS 800U //双前边缘逃逸可参考的最近前侧方弧追记忆时间
#define FIGHT_TURN_SPEED 500 //原地转向速度
#define FIGHT_FB_RETREAT_TIME 500 //F/B回避先后退时间
#define FIGHT_FB_RETREAT_SPEED 320
#define FIGHT_FB_FORWARD_TIME 700 //F/B回避后前进时间
#define FIGHT_FB_ADVANCE_SPEED 350
#define FIGHT_TRACK_FRONT_ARC_INNER_SPEED -100//前侧方甩头内侧轮速度
#define FIGHT_TRACK_FRONT_ARC_OUTER_SPEED 550 //前侧方甩头外侧轮速度
#define FIGHT_TRACK_FRONT_SMALL_ARC_INNER_SPEED 400 //前方小弧线内侧轮速度
#define FIGHT_TRACK_FRONT_SMALL_ARC_OUTER_SPEED 550 //前方小弧线外侧轮速度
#define FIGHT_TRACK_PUSH_SPEED 520 //正前推进速度
#define FIGHT_TRACK_SIDE_ARC_BACK_SPEED 200 //正侧方甩头内侧反转速度
#define FIGHT_TRACK_SIDE_ARC_FORWARD_SPEED 600 //正侧方甩头外侧前进速度
#define FIGHT_TRACK_REAR_ARC_BACK_SPEED 300 //后侧方甩头内侧反转速度
#define FIGHT_TRACK_REAR_ARC_FORWARD_SPEED 700 //后侧方甩头外侧前进速度
#define FIGHT_VISION_CONFIRM_COUNT 2 //视觉类型消抖次数
#define FIGHT_FRIEND_AREA_ON 5000 //友方回避进入面积阈值
#define FIGHT_FRIEND_AREA_OFF 4200 //友方回避确认清除面积阈值
#define FIGHT_BOMB_AREA_ON 3500 //炸弹回避进入面积阈值
#define FIGHT_BOMB_AREA_OFF 2800 //炸弹回避确认清除面积阈值
#define FIGHT_FB_CONFIRM_COUNT 2 //F/B回避连续新视觉帧确认次数
#define FIGHT_FRIEND_DIR_GATE 35 //友方回避正前方向窗口
#define FIGHT_SHADE_CONFIRM_TIME 100U //V0/V1灰度掉台持续确认时间

/*======敌人（能量块）方向======*/
typedef enum{
    DIR_NONE = 0,
    DIR_FRONT, //正前方
    DIR_FRONT_LEFT, //左前方
    DIR_FRONT_RIGHT, //右前方
    DIR_FRONT_SLIGHT_LEFT, //左前小偏
    DIR_FRONT_SLIGHT_RIGHT, //右前小偏
    DIR_LEFT, //正左
    DIR_RIGHT, //正右
    DIR_BACK_LEFT, //左后方
    DIR_BACK_RIGHT, //右后方
    DIR_BACK, //正后方
}EnemyDir;

/*======进攻状态======*/
typedef enum{
    FIGHT_ENGAGE, //交战主状态：按IR方向推进、弧追或转入侧后甩头
    FIGHT_FRONT_HOLD, //IR4+IR11正前双遮挡后的定时直推保持
    FIGHT_EDGE_STOP, //前边缘触发后短暂停顿，等待制动结束
    FIGHT_REAR_EDGE_STOP, //后边缘触发后短暂停顿，等待制动结束
    FIGHT_REAR_ESCAPE, //后边缘触发后的前进脱离
    FIGHT_RETREAT, //前边缘后退脱离
    FIGHT_TURN,   //前边缘后退后的恢复转向
    FIGHT_FB_TURN, //F/B回避先后退
    FIGHT_FORWARD, //F/B回避后180°掉头
    FIGHT_FB_ADVANCE, //F/B回避后短前进
    FIGHT_TRACK_SPIN, //左右侧/后侧目标的甩头追踪
    FIGHT_DONE, //交还控制权回漫游
}FightState;

/* ============ 函数声明 ============ */
void     Fight_Init(void);
void     Fight_InitWithDir(EnemyDir dir);
void     Fight_Update(void);
bool     Fight_IsDone(void);
EnemyDir Fight_GetEnemyDir(void);       // 获取敌人方向
bool     Fight_IsDown(void);           // 检测是否掉台

#endif // ROBOT_FIGHT_H
