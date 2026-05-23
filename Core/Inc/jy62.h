#ifndef __JY62_H
#define __JY62_H

#include "main.h"

typedef struct {
    int16_t ax_raw;
    int16_t ay_raw;
    int16_t az_raw;
    int16_t gx_raw;
    int16_t gy_raw;
    int16_t gz_raw;
    int16_t roll_raw;
    int16_t pitch_raw;
    int16_t yaw_raw;
    float ax_g;
    float ay_g;
    float az_g;
    float gx_dps;
    float gy_dps;
    float gz_dps;
    float roll_deg;
    float pitch_deg;
    float yaw_deg;
    uint32_t last_update_ms;
    uint32_t rx_bytes;
    uint32_t frame_count;
    uint32_t checksum_error_count;
    uint8_t last_frame_type;
    uint8_t online;
} JY62_Data_t;

extern volatile JY62_Data_t jy62_data;

void JY62_Init(void);
void JY62_Update(void);
void JY62_UART_RxEventCallback(UART_HandleTypeDef *huart, uint16_t Size);
uint8_t JY62_IsOnline(void);
uint8_t JY62_IsStable(float max_roll_deg, float max_pitch_deg);

#endif
