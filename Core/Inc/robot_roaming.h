#ifndef ROBOT_ROAMING_H
#define ROBOT_ROAMING_H

#include <stdbool.h>
#include "main.h"

/*======漫游状态======*/
typedef enum{
    ROAMING_FORWARD,      // 前进状态
    ROAMING_BACK,         // 后退状态
    ROAMING_TURN_LEFT,    // 左转状态
    ROAMING_TURN_RIGHT,   // 右转状态
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
#define ROAMING_BACK_TIME  500   // 后退时间
#define ROAMING_BACKAND_TURN_TIME  500   // 后退并转向时间
#define ROAMING_TURN_TIME  300   // 转向时间
#define ROAMING_FORWARD_TIME 300 // 前进时间
#define ROAMING_EDGE_DEBOUNCE_MS  20  // 边缘检测消抖窗口(ms)

void Roaming_Init(void);
void Roaming_Update(void);
bool Roaming_IsDone(void);
bool Roaming_IsForward(void);

#endif // ROBOT_ROAMING_H