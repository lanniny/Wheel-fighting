#include "jy62.h"
#include "usart.h"
#include <string.h>

#define JY62_UART_RX_BUF_SIZE       128u
#define JY62_FRAME_SIZE             11u
#define JY62_FRAME_HEAD             0x55u
#define JY62_FRAME_ACC              0x51u
#define JY62_FRAME_GYRO             0x52u
#define JY62_FRAME_ANGLE            0x53u
#define JY62_OFFLINE_TIMEOUT_MS     100u
#define JY62_ACC_SCALE_G            (16.0f / 32768.0f)
#define JY62_GYRO_SCALE_DPS         (2000.0f / 32768.0f)
#define JY62_ANGLE_SCALE_DEG        (180.0f / 32768.0f)

volatile JY62_Data_t jy62_data = {0};

static uint8_t jy62_rx_dma_buf[JY62_UART_RX_BUF_SIZE];
static uint8_t jy62_frame_buf[JY62_FRAME_SIZE];
static uint8_t jy62_frame_index = 0u;

static int16_t jy62_unpack_i16(const uint8_t *low_byte)
{
    return (int16_t)(((uint16_t)low_byte[1] << 8) | low_byte[0]);
}

static uint8_t jy62_checksum_ok(const uint8_t *frame)
{
    uint8_t sum = 0u;

    for (uint8_t i = 0u; i < (JY62_FRAME_SIZE - 1u); i++)
    {
        sum = (uint8_t)(sum + frame[i]);
    }

    return (sum == frame[JY62_FRAME_SIZE - 1u]) ? 1u : 0u;
}

static void jy62_parse_frame(const uint8_t *frame)
{
    if (!jy62_checksum_ok(frame))
    {
        jy62_data.checksum_error_count++;
        return;
    }

    uint32_t now = HAL_GetTick();

    if (frame[1] == JY62_FRAME_ACC)
    {
        jy62_data.ax_raw = jy62_unpack_i16(&frame[2]);
        jy62_data.ay_raw = jy62_unpack_i16(&frame[4]);
        jy62_data.az_raw = jy62_unpack_i16(&frame[6]);
        jy62_data.ax_g = (float)jy62_data.ax_raw * JY62_ACC_SCALE_G;
        jy62_data.ay_g = (float)jy62_data.ay_raw * JY62_ACC_SCALE_G;
        jy62_data.az_g = (float)jy62_data.az_raw * JY62_ACC_SCALE_G;
    }
    else if (frame[1] == JY62_FRAME_GYRO)
    {
        jy62_data.gx_raw = jy62_unpack_i16(&frame[2]);
        jy62_data.gy_raw = jy62_unpack_i16(&frame[4]);
        jy62_data.gz_raw = jy62_unpack_i16(&frame[6]);
        jy62_data.gx_dps = (float)jy62_data.gx_raw * JY62_GYRO_SCALE_DPS;
        jy62_data.gy_dps = (float)jy62_data.gy_raw * JY62_GYRO_SCALE_DPS;
        jy62_data.gz_dps = (float)jy62_data.gz_raw * JY62_GYRO_SCALE_DPS;
    }
    else if (frame[1] == JY62_FRAME_ANGLE)
    {
        jy62_data.roll_raw = jy62_unpack_i16(&frame[2]);
        jy62_data.pitch_raw = jy62_unpack_i16(&frame[4]);
        jy62_data.yaw_raw = jy62_unpack_i16(&frame[6]);
        jy62_data.roll_deg = (float)jy62_data.roll_raw * JY62_ANGLE_SCALE_DEG;
        jy62_data.pitch_deg = (float)jy62_data.pitch_raw * JY62_ANGLE_SCALE_DEG;
        jy62_data.yaw_deg = (float)jy62_data.yaw_raw * JY62_ANGLE_SCALE_DEG;
    }
    else
    {
        return;
    }

    jy62_data.last_update_ms = now;
    jy62_data.frame_count++;
    jy62_data.last_frame_type = frame[1];
    jy62_data.online = 1u;
}

static void jy62_parse_byte(uint8_t data)
{
    if (jy62_frame_index == 0u)
    {
        if (data != JY62_FRAME_HEAD)
        {
            return;
        }
    }
    else if (jy62_frame_index == 1u)
    {
        if (data != JY62_FRAME_ACC && data != JY62_FRAME_GYRO && data != JY62_FRAME_ANGLE)
        {
            jy62_frame_index = 0u;
            if (data == JY62_FRAME_HEAD)
            {
                jy62_frame_buf[jy62_frame_index++] = data;
            }
            return;
        }
    }

    jy62_frame_buf[jy62_frame_index++] = data;

    if (jy62_frame_index >= JY62_FRAME_SIZE)
    {
        jy62_parse_frame(jy62_frame_buf);
        jy62_frame_index = 0u;
    }
}

static void jy62_restart_rx(void)
{
    HAL_UARTEx_ReceiveToIdle_DMA(&huart3, jy62_rx_dma_buf, JY62_UART_RX_BUF_SIZE);
    __HAL_DMA_DISABLE_IT(huart3.hdmarx, DMA_IT_HT);
}

void JY62_Init(void)
{
    memset((void *)&jy62_data, 0, sizeof(jy62_data));
    memset(jy62_rx_dma_buf, 0, sizeof(jy62_rx_dma_buf));
    jy62_frame_index = 0u;
    jy62_restart_rx();
}

void JY62_Update(void)
{
    uint32_t now = HAL_GetTick();

    if (jy62_data.last_update_ms != 0u &&
        (now - jy62_data.last_update_ms) > JY62_OFFLINE_TIMEOUT_MS)
    {
        jy62_data.online = 0u;
    }
}

void JY62_UART_RxEventCallback(UART_HandleTypeDef *huart, uint16_t Size)
{
    if (huart->Instance != USART3)
    {
        return;
    }

    jy62_data.rx_bytes += Size;

    for (uint16_t i = 0u; i < Size; i++)
    {
        jy62_parse_byte(jy62_rx_dma_buf[i]);
    }

    jy62_restart_rx();
}

uint8_t JY62_IsOnline(void)
{
    if (jy62_data.online == 0u)
    {
        return 0u;
    }

    return ((HAL_GetTick() - jy62_data.last_update_ms) <= JY62_OFFLINE_TIMEOUT_MS) ? 1u : 0u;
}

uint8_t JY62_IsStable(float max_roll_deg, float max_pitch_deg)
{
    if (!JY62_IsOnline())
    {
        return 0u;
    }

    if (jy62_data.roll_deg > max_roll_deg || jy62_data.roll_deg < -max_roll_deg)
    {
        return 0u;
    }

    if (jy62_data.pitch_deg > max_pitch_deg || jy62_data.pitch_deg < -max_pitch_deg)
    {
        return 0u;
    }

    return 1u;
}
