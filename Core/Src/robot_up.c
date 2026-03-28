#include "motor.h"
#include "robot_up.h"
#include "usart.h"

static GoUpState GoUp_Stage = GOUP_RUSH;
static uint32_t GoUp_StartTime = 0;
static bool GoUp_Done = false;
TeamColor Current_Team = TEAM_NONE;

/**
 * @description: 获取当前队伍信息
 * @param void
 * @return Current_Team
 */
TeamColor Startup_WaitForTrigger(void)
{
    uint32_t left_hold = 0;
    uint32_t right_hold = 0;
    uint32_t last_time = HAL_GetTick();

    while(1)
    {
        /*计算两次循环之间的时间差，用于消抖*/
        uint32_t now = HAL_GetTick();
        uint32_t dt = now - last_time;
        last_time = now;

        GPIO_PinState left = HAL_GPIO_ReadPin(STARTUP_LEFT_PORT,STARTUP_LEFT_PIN);
        GPIO_PinState right = HAL_GPIO_ReadPin(STARTUP_RIGHT_PORT,STARTUP_RIGHT_PIN);

        /*左侧遮挡 黄方*/
        if(left == STARTUP_BLOCKED_STATE)
        {
            left_hold += dt;
            if(left_hold >= STARTUP_DEBOUNCE_MS)
            {
                Current_Team = TEAM_YELLOW;
                break;
            }
        }
        else
        {
            left_hold = 0;
        }
        /*右侧遮挡 蓝方*/
        if(right == STARTUP_BLOCKED_STATE)
        {
            right_hold += dt;
            if(right_hold >= STARTUP_DEBOUNCE_MS)
            {
                Current_Team = TEAM_BLUE;
                break;
            }
        }
        else
        {
            right_hold = 0;
        }
    }

    /*通知鲁班猫*/
    Startup_Notify(Current_Team);
    return Current_Team;
}

/**
 * @description: 通知鲁班猫当前的队伍信息
 * @param TeamColor team
 * @return void
 */
void Startup_Notify(TeamColor team)
{
    uint8_t msg = (team == TEAM_YELLOW) ? 'y' : 'b';
    HAL_UART_Transmit(&huart2, &msg, 1, 100);
}

/**
 * @description: 设置初始状态；获取初始事件；设置完成标志位为false
 * @param void
 * @return void
 */
void GoUp_Init(void)
{
    GoUp_Stage = GOUP_RUSH;
    GoUp_StartTime = HAL_GetTick();
    GoUp_Done = false;
}

/**
 * @description: 更新上台状态机，根据时间和状态执行相应动作
 * @param void
 * @return void
 */
void GoUp_Update()
{
    uint32_t current_time = HAL_GetTick();
    uint32_t elapsed_time = current_time - GoUp_StartTime;

    switch(GoUp_Stage)
    {
        case GOUP_RUSH:
            /*全速倒退冲台*/
            drive_Back_H();
            if(elapsed_time >= GOUP_RUSH_TIME)
            {
                GoUp_Stage = GOUP_TURN;
                GoUp_StartTime = current_time;
            }
            break;
        case GOUP_TURN:
            /*原地掉头*/
            drive_Left_S();
            if(elapsed_time >= GOUP_TURN_TIME)
            {
                GoUp_Stage = GOUP_DONE;
                GoUp_StartTime = current_time;
            }
            break;

        case GOUP_DONE:
            MOTOR_StopAll();
            GoUp_Done = true;
            break;

    }

}

/**
 * @description: 返回上台任务是否完成
 * @param void
 * @return bool
 */
bool GoUp_IsDone(void)
{
    return GoUp_Done;
}
