#include "motor.h"
#include "tim.h"

/* 记录左右电机组最后的速度方向, 供制动用 */
static int16_t motor_last_left  = 0;
static int16_t motor_last_right = 0;
static uint8_t motor_braking = 0;
static uint8_t motor_brake_stop_on_finish = 1;
static uint32_t motor_brake_start = 0;
static int16_t motor_brake_left = 0;
static int16_t motor_brake_right = 0;

void MOTOR_Init(void)
{
     // 启动TIM4 PWM - 电机1和电机2
      HAL_TIM_PWM_Start(&htim4, TIM_CHANNEL_1);  // PD12 - Motor1_A
      HAL_TIM_PWM_Start(&htim4, TIM_CHANNEL_2);  // PD13 - Motor1_B
      HAL_TIM_PWM_Start(&htim4, TIM_CHANNEL_3);  // PD14 - Motor2_A
      HAL_TIM_PWM_Start(&htim4, TIM_CHANNEL_4);  // PD15 - Motor2_B

      // 初始停止所有电机
      MOTOR_StopAll();
}

void MOTOR_SetSpeed(MOTOR_ID motor_id, int16_t speed)
{
    uint32_t pulse = (speed > 0) ? speed : -speed; // 计算PWM脉冲宽度
    pulse =  (pulse * PWM_MAX_VALUE) / SPEED_MAX_INPUT; // 速度映射
    if (pulse > PWM_MAX_VALUE) pulse = PWM_MAX_VALUE; // 限制最大值

    switch(motor_id)
    {
        case MOTOR_1:
        case MOTOR_2:
            motor_last_left = speed;
            if (speed >= 0)
            {
                __HAL_TIM_SET_COMPARE(&htim4, TIM_CHANNEL_1, pulse); // Motor1_A
                __HAL_TIM_SET_COMPARE(&htim4, TIM_CHANNEL_2, 0);     // Motor1_B
            }
            else
            {
                __HAL_TIM_SET_COMPARE(&htim4, TIM_CHANNEL_1, 0);     // Motor1_A
                __HAL_TIM_SET_COMPARE(&htim4, TIM_CHANNEL_2, pulse); // Motor1_B
            }
            break;

        case MOTOR_3:
        case MOTOR_4:
            motor_last_right = speed;
            if (speed >= 0)
            {
                __HAL_TIM_SET_COMPARE(&htim4, TIM_CHANNEL_3, pulse); // Motor4_A
                __HAL_TIM_SET_COMPARE(&htim4, TIM_CHANNEL_4, 0);     // Motor4_B
            }
            else
            {
                __HAL_TIM_SET_COMPARE(&htim4, TIM_CHANNEL_3, 0);     // Motor4_A
                __HAL_TIM_SET_COMPARE(&htim4, TIM_CHANNEL_4, pulse); // Motor4_B
            }
            break;
        }
    }

void MOTOR_StopAll(void)
{
    motor_braking = 0;
    MOTOR_SetSpeed(MOTOR_1, 0);
    MOTOR_SetSpeed(MOTOR_2, 0);
    MOTOR_SetSpeed(MOTOR_3, 0);
    MOTOR_SetSpeed(MOTOR_4, 0);
}

static void motor_start_brake(uint8_t stop_on_finish)
{
    motor_brake_left = (motor_last_left > 0) ? -BRAKE_PULSE_SPEED :
                       (motor_last_left < 0) ?  BRAKE_PULSE_SPEED : 0;
    motor_brake_right = (motor_last_right > 0) ? -BRAKE_PULSE_SPEED :
                        (motor_last_right < 0) ?  BRAKE_PULSE_SPEED : 0;

    motor_braking = 1;
    motor_brake_stop_on_finish = stop_on_finish;
    motor_brake_start = HAL_GetTick();

    MOTOR_SetSpeed(MOTOR_1, motor_brake_left);
    MOTOR_SetSpeed(MOTOR_2, motor_brake_left);
    MOTOR_SetSpeed(MOTOR_3, motor_brake_right);
    MOTOR_SetSpeed(MOTOR_4, motor_brake_right);
}

void MOTOR_BrakeAll(void)
{
    // 两级非阻塞制动：先强反向脉冲，再短拖刹，最后停机
    motor_start_brake(1);
}

void MOTOR_BrakeAllRelease(void)
{
    // 两级非阻塞制动：先强反向脉冲，再短拖刹，结束后不主动停机
    motor_start_brake(0);
}

bool MOTOR_IsBraking(void)
{
    return (motor_braking != 0);
}

void MOTOR_Service(void)
{
    uint32_t elapsed;

    if (!motor_braking)
    {
        return;
    }

    elapsed = HAL_GetTick() - motor_brake_start;

    if (elapsed >= (BRAKE_PULSE_MS + BRAKE_HOLD_MS))
    {
        if (motor_brake_stop_on_finish)
        {
            MOTOR_StopAll();
        }
        else
        {
            motor_braking = 0;
        }
    }
    else if (elapsed >= BRAKE_PULSE_MS)
    {
        int16_t hold_left = 0;
        int16_t hold_right = 0;

        if (motor_brake_left > 0)
        {
            hold_left = BRAKE_HOLD_SPEED;
        }
        else if (motor_brake_left < 0)
        {
            hold_left = -BRAKE_HOLD_SPEED;
        }

        if (motor_brake_right > 0)
        {
            hold_right = BRAKE_HOLD_SPEED;
        }
        else if (motor_brake_right < 0)
        {
            hold_right = -BRAKE_HOLD_SPEED;
        }

        MOTOR_SetSpeed(MOTOR_1, hold_left);
        MOTOR_SetSpeed(MOTOR_2, hold_left);
        MOTOR_SetSpeed(MOTOR_3, hold_right);
        MOTOR_SetSpeed(MOTOR_4, hold_right);
    }
}

void drive_For_L(void)//前进(低中高)
{
    MOTOR_SetSpeed(MOTOR_1, SPEED_LOW);
    MOTOR_SetSpeed(MOTOR_2, SPEED_LOW);
    MOTOR_SetSpeed(MOTOR_3, SPEED_LOW);
    MOTOR_SetSpeed(MOTOR_4, SPEED_LOW);
}

void drive_For_M(void)
{
    MOTOR_SetSpeed(MOTOR_1, SPEED_MEDIUM);
    MOTOR_SetSpeed(MOTOR_2, SPEED_MEDIUM);
    MOTOR_SetSpeed(MOTOR_3, SPEED_MEDIUM);
    MOTOR_SetSpeed(MOTOR_4, SPEED_MEDIUM);
}

void drive_For_H(void)
{
    MOTOR_SetSpeed(MOTOR_1, SPEED_HIGH);
    MOTOR_SetSpeed(MOTOR_2, SPEED_HIGH);
    MOTOR_SetSpeed(MOTOR_3, SPEED_HIGH);
    MOTOR_SetSpeed(MOTOR_4, SPEED_HIGH);
}

void drive_For_Roaming(void)
{
    MOTOR_SetSpeed(MOTOR_1, SPEED_ROAMING);
    MOTOR_SetSpeed(MOTOR_2, SPEED_ROAMING);
    MOTOR_SetSpeed(MOTOR_3, SPEED_ROAMING);
    MOTOR_SetSpeed(MOTOR_4, SPEED_ROAMING);
}

void drive_Back_L(void)//后退(低中高)
{
    MOTOR_SetSpeed(MOTOR_1, -SPEED_LOW);
    MOTOR_SetSpeed(MOTOR_2, -SPEED_LOW);
    MOTOR_SetSpeed(MOTOR_3, -SPEED_LOW);
    MOTOR_SetSpeed(MOTOR_4, -SPEED_LOW);
}

void drive_Back_M(void)
{
    MOTOR_SetSpeed(MOTOR_1, -SPEED_MEDIUM);
    MOTOR_SetSpeed(MOTOR_2, -SPEED_MEDIUM);
    MOTOR_SetSpeed(MOTOR_3, -SPEED_MEDIUM);
    MOTOR_SetSpeed(MOTOR_4, -SPEED_MEDIUM);
}

void drive_Back_H(void)
{
    MOTOR_SetSpeed(MOTOR_1, -SPEED_HIGH);
    MOTOR_SetSpeed(MOTOR_2, -SPEED_HIGH);
    MOTOR_SetSpeed(MOTOR_3, -SPEED_HIGH);
    MOTOR_SetSpeed(MOTOR_4, -SPEED_HIGH);
}

void drive_Left_L(void)//慢左右转
{
   MOTOR_SetSpeed(MOTOR_1, -SPEED_TURN_L);
   MOTOR_SetSpeed(MOTOR_2, -SPEED_TURN_L);
   MOTOR_SetSpeed(MOTOR_3, SPEED_TURN_L);
   MOTOR_SetSpeed(MOTOR_4, SPEED_TURN_L);
}

void drive_Right_L(void)
{
    MOTOR_SetSpeed(MOTOR_1, SPEED_TURN_L);
    MOTOR_SetSpeed(MOTOR_2, SPEED_TURN_L);
    MOTOR_SetSpeed(MOTOR_3, -SPEED_TURN_L);
    MOTOR_SetSpeed(MOTOR_4, -SPEED_TURN_L);
}

void drive_Left_M(void)//微左右转
{
   MOTOR_SetSpeed(MOTOR_1, -SPEED_TURN_R);
   MOTOR_SetSpeed(MOTOR_2, -SPEED_TURN_R);
   MOTOR_SetSpeed(MOTOR_3, SPEED_TURN_R);
   MOTOR_SetSpeed(MOTOR_4, SPEED_TURN_R);
}

void drive_Right_M(void)
{
    MOTOR_SetSpeed(MOTOR_1, SPEED_TURN_R);
    MOTOR_SetSpeed(MOTOR_2, SPEED_TURN_R);
    MOTOR_SetSpeed(MOTOR_3, -SPEED_TURN_R);
    MOTOR_SetSpeed(MOTOR_4, -SPEED_TURN_R);
}

void drive_Left_S(void)//超级左右转
{
    MOTOR_SetSpeed(MOTOR_1, -SPEED_TURN_S);
    MOTOR_SetSpeed(MOTOR_2, -SPEED_TURN_S);
    MOTOR_SetSpeed(MOTOR_3, SPEED_TURN_S);
    MOTOR_SetSpeed(MOTOR_4, SPEED_TURN_S);
}

void drive_Right_S(void)
{
    MOTOR_SetSpeed(MOTOR_1, SPEED_TURN_S);
    MOTOR_SetSpeed(MOTOR_2, SPEED_TURN_S);
    MOTOR_SetSpeed(MOTOR_3, -SPEED_TURN_S);
    MOTOR_SetSpeed(MOTOR_4, -SPEED_TURN_S);
}

void drive_Retreat_L(void)//后退左转
{
    MOTOR_SetSpeed(MOTOR_1, -SPEED_TURN_S);
    MOTOR_SetSpeed(MOTOR_2, -SPEED_TURN_S);
    MOTOR_SetSpeed(MOTOR_3, -SPEED_TURN_S+200);
    MOTOR_SetSpeed(MOTOR_4, -SPEED_TURN_S+200);   
}

void drive_Retreat_R(void)//后退右转
{
    MOTOR_SetSpeed(MOTOR_1, -SPEED_TURN_S+200);
    MOTOR_SetSpeed(MOTOR_2, -SPEED_TURN_S+200);
    MOTOR_SetSpeed(MOTOR_3, -SPEED_TURN_S);
    MOTOR_SetSpeed(MOTOR_4, -SPEED_TURN_S);   
}

void drive_user_defined(int16_t left_speed, int16_t right_speed)//自定义速度
{
    MOTOR_SetSpeed(MOTOR_1, left_speed);
    MOTOR_SetSpeed(MOTOR_2, left_speed);
    MOTOR_SetSpeed(MOTOR_3, right_speed);
    MOTOR_SetSpeed(MOTOR_4, right_speed);
}

/*======斜坡平滑控制======*/
static int16_t ramp_left_cur = 0, ramp_left_tgt = 0;
static int16_t ramp_right_cur = 0, ramp_right_tgt = 0;

static int16_t ramp_toward(int16_t cur, int16_t tgt)
{
    if (cur < tgt) {
        cur += RAMP_STEP;
        if (cur > tgt) cur = tgt;
    } else if (cur > tgt) {
        cur -= RAMP_STEP;
        if (cur < tgt) cur = tgt;
    }
    return cur;
}

void Motor_Ramp_SetTarget(int16_t left, int16_t right)
{
    ramp_left_tgt = left;
    ramp_right_tgt = right;
}

void Motor_Ramp_Update(void)
{
    ramp_left_cur = ramp_toward(ramp_left_cur, ramp_left_tgt);
    ramp_right_cur = ramp_toward(ramp_right_cur, ramp_right_tgt);
    drive_user_defined(ramp_left_cur, ramp_right_cur);
}

void Motor_Ramp_ForceStop(void)
{
    ramp_left_cur = ramp_left_tgt = 0;
    ramp_right_cur = ramp_right_tgt = 0;
    MOTOR_StopAll();
}

void Motor_Ramp_SyncFromCurrent(void)
{
    ramp_left_cur = ramp_left_tgt = motor_last_left;
    ramp_right_cur = ramp_right_tgt = motor_last_right;
}
