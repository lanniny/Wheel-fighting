/*
 * @Author: Xiang xin wang wxinxiang8@gmail.com
 * @Date: 2026-03-14 17:53:19
 * @LastEditors: Xiang xin wang wxinxiang8@gmail.com
 * @LastEditTime: 2026-03-30 19:25:36
 * @FilePath: \MDK-ARMd:\robot fighting\robot\Core\Inc\robot_roaming.h
 * @Description: 这是默认设置,请设置`customMade`, 打开koroFileHeader查看配置 进行设置: https://github.com/OBKoro1/koro1FileHeader/wiki/%E9%85%8D%E7%BD%AE
 */
#ifndef ROBOT_ROAMING_H
#define ROBOT_ROAMING_H

#include <stdbool.h>
#include "main.h"

/*======漫游状态======*/
typedef enum{
    ROAMING_FORWARD,      // 前进状态
    ROAMING_EDGE_STOP,    // 边缘确认后短暂停顿
    ROAMING_BACK,         // 后退状态
    ROAMING_TURN_LEFT,    // 左转状态
    ROAMING_TURN_RIGHT,   // 右转状态
    ROAMING_TURN_BOTH,    // 后退并转向状态
    ROAMING_DONE          // 完成状态（掉落擂台）
}RoamingState;

/*======后退原因======*/
typedef enum {
    BACK_REASON_NONE = 0,
    BACK_REASON_BOTH,
    BACK_REASON_LEFT,
    BACK_REASON_RIGHT
} RoamingBackReason;

/*======时间参数======*/
#define ROAMING_BACK_TIME  550   // 后退时间
#define ROAMING_BACKAND_TURN_TIME  600   // 后退并转向时间
#define ROAMING_TURN_LEFT_TIME  380   // 左转时间
#define ROAMING_TURN_RIGHT_TIME  460   // 右转时间
#define ROAMING_EDGE_CONFIRM_COUNT 1 // 边缘光电消抖次数
#define ROAMING_EDGE_STOP_TIME 15 // 边缘确认后停顿时间
#define ROAMING_SHADE_CONFIRM_COUNT 3 // V0/V1灰度掉台确认次数

void Roaming_Init(void);
void Roaming_Update(void);
bool Roaming_IsDone(void);

#endif // ROBOT_ROAMING_H