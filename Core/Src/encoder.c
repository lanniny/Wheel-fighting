#include "encoder.h"
#include "tim.h"

extern Encoder_TypeDef ENCODER[4]= {0};//4个编码器数据，索引0-3对应ENCODER_1-4

void ENCODER_Init(void)
{
    // 启动编码器计数器
    HAL_TIM_Encoder_Start(&htim5, TIM_CHANNEL_ALL); // 编码器1
    HAL_TIM_Encoder_Start(&htim3, TIM_CHANNEL_ALL); // 编码器2
    HAL_TIM_Encoder_Start(&htim1, TIM_CHANNEL_ALL); // 编码器3
    HAL_TIM_Encoder_Start(&htim8, TIM_CHANNEL_ALL); // 编码器4
}

// ==================== 读取编码器原始计数值====================
int32_t ENCODER_GetCount(ENCODER_ID encoder_id)
{
    int32_t count=0;
    switch(encoder_id)
    {
        case ENCODER_1:
            count = __HAL_TIM_GetCounter(&htim5);
            break;
        case ENCODER_2:
            count = __HAL_TIM_GetCounter(&htim3);
            break;
        case ENCODER_3:
            count = __HAL_TIM_GetCounter(&htim1);
            break;
        case ENCODER_4:
            count =   __HAL_TIM_GetCounter(&htim8);
            break;
    }
    return count;
}

// ==================== 重置单个编码器计数====================
void ENCODER_ResetCount(ENCODER_ID encoder_id)
{
    switch(encoder_id)
    {
        case ENCODER_1:
            __HAL_TIM_SET_COUNTER(&htim5, 0);
            break;
        case ENCODER_2:
            __HAL_TIM_SET_COUNTER(&htim3, 0);
            break;
        case ENCODER_3:
            __HAL_TIM_SET_COUNTER(&htim1, 0);
            break;
        case ENCODER_4:
            __HAL_TIM_SET_COUNTER(&htim8, 0);
            break;
    }
    // 清除对应编码器数据
    uint8_t idx = encoder_id - 1;
    if(idx < 4)
    {
        ENCODER[idx].position = 0;
        ENCODER[idx].last_count = 0;
        ENCODER[idx].speed = 0;
        ENCODER[idx].direction = 0;
    }
}

// ==================== 重置所有编码器====================
void ENCODER_ResetAll(void)
{
    ENCODER_ResetCount(ENCODER_1);
    ENCODER_ResetCount(ENCODER_2);
    ENCODER_ResetCount(ENCODER_3);
    ENCODER_ResetCount(ENCODER_4);
}

 // ==================== 更新编码器数据（按控制周期调用）====================
void ENCODER_Update(void)
{
    int32_t current_count;
    int32_t delta;

    for(uint8_t i = 0; i < 4; i++)
    {
        // 读取当前计数值
        current_count = ENCODER_GetCount((ENCODER_ID)(i + 1));

        // 计算计数差值（速度）
        delta = current_count - ENCODER[i].last_count;

        // 处理16位定时器溢出（TIM1, TIM3, TIM8）
        // TIM5是32位，不需要处理溢出
        if(i != 0)  // ENCODER_2, 3, 4 使用16位定时器
        {
            if(delta > 32767)
                delta -= 65536;
            else if(delta < -32767)
                delta += 65536;
        }

        // 更新速度
        ENCODER[i].speed = (int16_t)delta;

        // 更新累计位置
        ENCODER[i].position += delta;

        // 更新方向
        if(delta > 0)
            ENCODER[i].direction = 1;   // 正转
        else if(delta < 0)
            ENCODER[i].direction = -1;  // 反转
        else
            ENCODER[i].direction = 0;   // 静止 

        // 保存当前计数值供下次使用
        ENCODER[i].last_count = current_count;
    }
}