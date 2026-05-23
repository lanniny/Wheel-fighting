#include "motor.h"
#include "tim.h"
#include "pid.h"
#include "encoder.h"

/* 记录左右电机组最后的目标速度方向, 供制动用 */
static int16_t motor_last_left  = 0;
static int16_t motor_last_right = 0;
static uint8_t motor_braking = 0;
static uint8_t motor_brake_stop_on_finish = 1;
static uint32_t motor_brake_start = 0;
static int16_t motor_brake_left = 0;
static int16_t motor_brake_right = 0;

#define MOTOR_LEFT_ENCODER        ENCODER_1
#define MOTOR_RIGHT_ENCODER       ENCODER_2
#define MOTOR_LEFT_ENCODER_DIR    1
#define MOTOR_RIGHT_ENCODER_DIR   -1
#define MOTOR_LEFT_SPEED_SCALE    10.0f
#define MOTOR_RIGHT_SPEED_SCALE   10.0f
#define MOTOR_LEFT_MEASURE_FILTER_ALPHA  0.16f
#define MOTOR_RIGHT_MEASURE_FILTER_ALPHA 0.26f
#define MOTOR_PID_PERIOD_MS       5U
#define MOTOR_LEFT_PID_KP         0.30f
#define MOTOR_LEFT_PID_KI         0.035f
#define MOTOR_LEFT_PID_KD         0.0f
#define MOTOR_LEFT_PID_INTEGRAL_MAX 3000.0f
#define MOTOR_RIGHT_PID_KP        0.33f
#define MOTOR_RIGHT_PID_KI        0.04f
#define MOTOR_RIGHT_PID_KD        0.0f
#define MOTOR_RIGHT_PID_INTEGRAL_MAX 3000.0f

static int16_t motor_target_left = 0;
static int16_t motor_target_right = 0;
static int16_t motor_measured_left = 0;
static int16_t motor_measured_right = 0;
static int16_t motor_output_left = 0;
static int16_t motor_output_right = 0;
static float motor_filtered_left = 0.0f;
static float motor_filtered_right = 0.0f;
static uint32_t motor_pid_last_tick = 0;
static PID_TypeDef motor_pid_left;
static PID_TypeDef motor_pid_right;

/* 清空 PID 历史量，避免停机或制动后残留输出 */
static void Motor_PID_Reset(void)
{
    motor_pid_left.integral = 0.0f;
    motor_pid_left.last_error = 0.0f;
    motor_pid_right.integral = 0.0f;
    motor_pid_right.last_error = 0.0f;
    motor_filtered_left = 0.0f;
    motor_filtered_right = 0.0f;
}

/* 记录状态机给出的左右轮组目标速度 */
static void Motor_SetTargetSpeed(int16_t left_speed, int16_t right_speed)
{
    motor_target_left = left_speed;
    motor_target_right = right_speed;
    motor_last_left = left_speed;
    motor_last_right = right_speed;
}

/* 将左右轮组速度一次性写入四路电机 */
static void Motor_SetGroupSpeed(int16_t left_speed, int16_t right_speed)
{
    MOTOR_SetSpeed(MOTOR_1, left_speed);
    MOTOR_SetSpeed(MOTOR_2, left_speed);
    MOTOR_SetSpeed(MOTOR_3, right_speed);
    MOTOR_SetSpeed(MOTOR_4, right_speed);
}

/* 根据一级制动方向生成二级拖刹速度 */
static int16_t Motor_BrakeHoldSpeed(int16_t brake_speed)
{
    return (brake_speed > 0) ? BRAKE_HOLD_SPEED :
           (brake_speed < 0) ? -BRAKE_HOLD_SPEED : 0;
}

/* 限制 PID 修正后的速度不超过电机输入范围 */
static int16_t Motor_LimitSpeed(float speed)
{
    if (speed > SPEED_MAX_INPUT)
    {
        return SPEED_MAX_INPUT;
    }
    if (speed < -SPEED_MAX_INPUT)
    {
        return -SPEED_MAX_INPUT;
    }
    return (int16_t)speed;
}

/* 将编码器脉冲差换算到 5ms 等效 SPEED_xxx 速度单位 */
static float Motor_EncoderToSpeed(int16_t encoder_speed,
                                  int16_t encoder_dir,
                                  float speed_scale,
                                  uint32_t elapsed_ms)
{
    float speed = (float)(encoder_dir * encoder_speed) * speed_scale;
    speed = speed * (float)MOTOR_PID_PERIOD_MS / (float)elapsed_ms;

    if (speed > SPEED_MAX_INPUT)
    {
        return (float)SPEED_MAX_INPUT;
    }
    if (speed < -SPEED_MAX_INPUT)
    {
        return (float)-SPEED_MAX_INPUT;
    }
    return speed;
}

static float Motor_FilterMeasured(float *filtered_speed, float measured_speed, float filter_alpha)
{
    *filtered_speed += (measured_speed - *filtered_speed) * filter_alpha;
    return *filtered_speed;
}

static float Motor_GetMeasuredSpeed(ENCODER_ID encoder_id,
                                    int16_t encoder_dir,
                                    float speed_scale,
                                    float *filtered_speed,
                                    float filter_alpha,
                                    uint32_t elapsed_ms)
{
    return Motor_FilterMeasured(filtered_speed,
                                Motor_EncoderToSpeed(ENCODER[encoder_id - 1].speed,
                                                     encoder_dir,
                                                     speed_scale,
                                                     elapsed_ms),
                                filter_alpha);
}

static void Motor_PID_StopOutput(void)
{
    Motor_PID_Reset();
    motor_measured_left = 0;
    motor_measured_right = 0;
    motor_output_left = 0;
    motor_output_right = 0;
    Motor_SetGroupSpeed(0, 0);
}

/* 启动 TIM4 PWM 并确保电机初始停止 */
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

/* 初始化左右轮组速度闭环 PID */
void Motor_PID_Init(void)
{
    PID_Init(&motor_pid_left, MOTOR_LEFT_PID_KP, MOTOR_LEFT_PID_KI, MOTOR_LEFT_PID_KD, -SPEED_MAX_INPUT, SPEED_MAX_INPUT);
    PID_Init(&motor_pid_right, MOTOR_RIGHT_PID_KP, MOTOR_RIGHT_PID_KI, MOTOR_RIGHT_PID_KD, -SPEED_MAX_INPUT, SPEED_MAX_INPUT);
    motor_pid_left.integral_max = MOTOR_LEFT_PID_INTEGRAL_MAX;
    motor_pid_right.integral_max = MOTOR_RIGHT_PID_INTEGRAL_MAX;
    motor_pid_last_tick = HAL_GetTick();
}

/* 每 5ms 更新编码器并输出左右轮组闭环速度 */
void Motor_PID_Service(void)
{
    uint32_t now = HAL_GetTick();
    uint32_t elapsed_ms = now - motor_pid_last_tick;
    int16_t left_output;
    int16_t right_output;
    float left_measured;
    float right_measured;

    if (elapsed_ms < MOTOR_PID_PERIOD_MS)
    {
        return;
    }

    motor_pid_last_tick = now;
    ENCODER_Update();

    if (MOTOR_IsBraking())
    {
        return;
    }

    left_measured = Motor_GetMeasuredSpeed(MOTOR_LEFT_ENCODER,
                                           MOTOR_LEFT_ENCODER_DIR,
                                           MOTOR_LEFT_SPEED_SCALE,
                                           &motor_filtered_left,
                                           MOTOR_LEFT_MEASURE_FILTER_ALPHA,
                                           elapsed_ms);
    right_measured = Motor_GetMeasuredSpeed(MOTOR_RIGHT_ENCODER,
                                            MOTOR_RIGHT_ENCODER_DIR,
                                            MOTOR_RIGHT_SPEED_SCALE,
                                            &motor_filtered_right,
                                            MOTOR_RIGHT_MEASURE_FILTER_ALPHA,
                                            elapsed_ms);
    motor_measured_left = (int16_t)left_measured;
    motor_measured_right = (int16_t)right_measured;

    if (motor_target_left == 0 && motor_target_right == 0)
    {
        Motor_PID_StopOutput();
        return;
    }

    motor_pid_left.setpoint = (float)motor_target_left;
    motor_pid_right.setpoint = (float)motor_target_right;

    left_output = Motor_LimitSpeed((float)motor_target_left + PID_Compute(&motor_pid_left, left_measured));
    right_output = Motor_LimitSpeed((float)motor_target_right + PID_Compute(&motor_pid_right, right_measured));

    motor_output_left = left_output;
    motor_output_right = right_output;

    Motor_SetGroupSpeed(left_output, right_output);
}

void Motor_Debug_GetStatus(Motor_DebugStatus_t *status)
{
    status->target_left = motor_target_left;
    status->target_right = motor_target_right;
    status->measured_left = motor_measured_left;
    status->measured_right = motor_measured_right;
    status->output_left = motor_output_left;
    status->output_right = motor_output_right;
}

/* 将单侧轮组速度转换为 TIM4 H 桥 PWM 输出 */
void MOTOR_SetSpeed(MOTOR_ID motor_id, int16_t speed)
{
    uint32_t pulse = (speed > 0) ? speed : -speed; // 计算PWM脉冲宽度
    pulse =  (pulse * PWM_MAX_VALUE) / SPEED_MAX_INPUT; // 速度映射
    if (pulse > PWM_MAX_VALUE) pulse = PWM_MAX_VALUE; // 限制最大值

    switch(motor_id)
    {
        case MOTOR_1:
        case MOTOR_2:
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

/* 清空目标、PID 和 PWM 输出，立即停机 */
void MOTOR_StopAll(void)
{
    motor_braking = 0;
    Motor_SetTargetSpeed(0, 0);
    Motor_PID_Reset();
    Motor_SetGroupSpeed(0, 0);
}

/* 根据当前运动方向启动两级非阻塞反向制动 */
static void motor_start_brake(uint8_t stop_on_finish)
{
    if (motor_braking)
    {
        if (stop_on_finish)
        {
            motor_brake_stop_on_finish = 1;
        }
        return;
    }

    motor_brake_left = (motor_last_left > 0) ? -BRAKE_PULSE_SPEED :
                       (motor_last_left < 0) ?  BRAKE_PULSE_SPEED : 0;
    motor_brake_right = (motor_last_right > 0) ? -BRAKE_PULSE_SPEED :
                        (motor_last_right < 0) ?  BRAKE_PULSE_SPEED : 0;

    Motor_SetTargetSpeed(0, 0);
    Motor_PID_Reset();
    motor_braking = 1;
    motor_brake_stop_on_finish = stop_on_finish;
    motor_brake_start = HAL_GetTick();

    Motor_SetGroupSpeed(motor_brake_left, motor_brake_right);
}

/* 启动制动，结束后保持停机 */
void MOTOR_BrakeAll(void)
{
    // 两级非阻塞制动：先强反向脉冲，再短拖刹，最后停机
    motor_start_brake(1);
}

/* 启动制动，结束后释放给状态机继续接管 */
void MOTOR_BrakeAllRelease(void)
{
    // 两级非阻塞制动：先强反向脉冲，再短拖刹，结束后不主动停机
    motor_start_brake(0);
}

/* 查询当前是否处于非阻塞制动流程 */
bool MOTOR_IsBraking(void)
{
    return (motor_braking != 0);
}

/* 推进两级非阻塞制动流程，应在主循环高频调用 */
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
        Motor_SetGroupSpeed(Motor_BrakeHoldSpeed(motor_brake_left),
                            Motor_BrakeHoldSpeed(motor_brake_right));
    }
}

void drive_For_L(void)//前进(低中高)
{
    Motor_SetTargetSpeed(SPEED_LOW, SPEED_LOW);
}

void drive_For_M(void)
{
    Motor_SetTargetSpeed(SPEED_MEDIUM, SPEED_MEDIUM);
}

void drive_For_H(void)
{
    Motor_SetTargetSpeed(SPEED_HIGH, SPEED_HIGH);
}

void drive_For_Roaming(void)
{
    Motor_SetTargetSpeed(SPEED_ROAMING, SPEED_ROAMING);
}

void drive_Back_L(void)//后退(低中高)
{
    Motor_SetTargetSpeed(-SPEED_LOW, -SPEED_LOW);
}

void drive_Back_M(void)
{
    Motor_SetTargetSpeed(-SPEED_MEDIUM, -SPEED_MEDIUM);
}

void drive_Back_H(void)
{
    Motor_SetTargetSpeed(-SPEED_HIGH, -SPEED_HIGH);
}

void drive_Left_L(void)//慢左右转
{
    Motor_SetTargetSpeed(-SPEED_TURN_L, SPEED_TURN_L);
}

void drive_Right_L(void)
{
    Motor_SetTargetSpeed(SPEED_TURN_L, -SPEED_TURN_L);
}

void drive_Left_M(void)//微左右转
{
    Motor_SetTargetSpeed(-SPEED_TURN_R, SPEED_TURN_R);
}

void drive_Right_M(void)
{
    Motor_SetTargetSpeed(SPEED_TURN_R, -SPEED_TURN_R);
}

void drive_Left_S(void)//超级左右转
{
    Motor_SetTargetSpeed(-SPEED_TURN_S, SPEED_TURN_S);
}

void drive_Right_S(void)
{
    Motor_SetTargetSpeed(SPEED_TURN_S, -SPEED_TURN_S);
}

void drive_Retreat_L(void)//后退左转
{
    Motor_SetTargetSpeed(-SPEED_TURN_S, -SPEED_TURN_S + 200);
}

void drive_Retreat_R(void)//后退右转
{
    Motor_SetTargetSpeed(-SPEED_TURN_S + 200, -SPEED_TURN_S);
}

void drive_user_defined(int16_t left_speed, int16_t right_speed)//自定义速度
{
    Motor_SetTargetSpeed(left_speed, right_speed);
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
    ramp_left_cur = ramp_left_tgt = motor_target_left;
    ramp_right_cur = ramp_right_tgt = motor_target_right;
}
