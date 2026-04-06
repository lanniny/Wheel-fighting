#ifndef ROBOT_ROAMING_H
#define ROBOT_ROAMING_H

#include <stdbool.h>
#include "main.h"

/*======漫游状态======*/
typedef enum{
    ROAMING_FORWARD,      /* 前进状态 */
    ROAMING_BACK,         /* 后退状态 */
    ROAMING_TURN_LEFT,    /* 左转状态 */
    ROAMING_TURN_RIGHT,   /* 右转状态 */
    ROAMING_TURN_BOTH,    /* 双边同时触发转向 */
    ROAMING_DONE          /* 完成状态（掉落擂台） */
}RoamingState;

/*======后退原因======*/
typedef enum {
    BACK_REASON_NONE = 0,
    BACK_REASON_BOTH,
    BACK_REASON_LEFT,
    BACK_REASON_RIGHT
} RoamingBackReason;

/*======时间参数======*/
#define ROAMING_BACK_TIME          550   /* 后退时间 */
#define ROAMING_BACKAND_TURN_TIME  600   /* 双边触发后转向时间 */
#define ROAMING_TURN_LEFT_TIME     380   /* 左转时间 */
#define ROAMING_TURN_RIGHT_TIME    460   /* 右转时间 */
#define ROAMING_FORWARD_TIME       360   /* 前进时间 */
#define ROAMING_EDGE_DEBOUNCE_MS   20    /* 边缘检测消抖窗口(ms) */
#define ROAMING_SHADE_CONFIRM_COUNT 3    /* 灰度掉台确认次数(紧急通道外) */

void Roaming_Init(void);
void Roaming_Update(void);
bool Roaming_IsDone(void);
bool Roaming_IsForward(void);

#endif // ROBOT_ROAMING_H