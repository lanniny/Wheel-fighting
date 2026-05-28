/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include "adc.h"
#include "dma.h"
#include "i2c.h"
#include "tim.h"
#include "usart.h"
#include "gpio.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "FreeRTOS.h"
#include "task.h"
#include "shade.h"
#include "obstacle.h"
#include "motor.h"
#include "encoder.h"
#include "oled.h"
#include "oled_font.h"
#include "robot_up.h"
#include "robot_roaming.h"
#include "robot_backup.h"
#include "robot_control.h"
#include "vision_parser.h"
#include "jy62.h"
#include <stdio.h>
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
#define SHADE_OLED_TEST_MODE   0
#define SHADE_UART_TEST_MODE   0
#define BACKUP_TEST_MODE       0
#define IR_OLED_TEST_MODE      0
#define VISION_OLED_TEST_MODE  0
#define JY62_TEST_MODE         0
#define PID_DEBUG_MODE         0
#define UART_TEST_MODE         0
#define STATE_UART_DEBUG_MODE  0

/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/

/* USER CODE BEGIN PV */

/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
/* USER CODE BEGIN PFP */

/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */
#if BACKUP_TEST_MODE
#define BACKUP_TEST_OFF_STAGE_ADC 3800U
#define BACKUP_TEST_ON_STAGE_ADC  1000U
#endif

#if BACKUP_TEST_MODE
static char Backup_Test_LastNonXType = 'X';
#endif

#if STATE_UART_DEBUG_MODE
#define STATE_UART_DEBUG_PRINT_MS 500U

static const char *State_Debug_GetName(RobotState state)
{
  switch (state)
  {
    case ROBOT_GO_UP:   return "GO_UP";
    case ROBOT_ROAMING: return "ROAMING";
    case ROBOT_ATTACK:  return "ATTACK";
    case ROBOT_BACKUP:  return "BACKUP";
    default:            return "UNKNOWN";
  }
}

static void State_Debug_Update(void)
{
  static uint32_t state_debug_last_print = 0;
  uint32_t now = HAL_GetTick();

  if ((now - state_debug_last_print) >= STATE_UART_DEBUG_PRINT_MS)
  {
    RobotState state = Robot_Control_GetState();
    char uart_buf[32] = {0};
    int len;

    state_debug_last_print = now;
    len = snprintf(uart_buf, sizeof(uart_buf), "state:%s,%d\r\n", State_Debug_GetName(state), (int)state);
    if (len > 0)
    {
      HAL_UART_Transmit(&huart2, (uint8_t *)uart_buf, (uint16_t)len, 100);
    }
  }
}
#endif

#if JY62_TEST_MODE
#define JY62_TEST_PRINT_MS     100U

static int32_t JY62_Test_Scale100(float value)
{
  if (value >= 0.0f)
  {
    return (int32_t)(value * 100.0f + 0.5f);
  }

  return (int32_t)(value * 100.0f - 0.5f);
}

static void JY62_Test_FormatDecimal(char *buf, uint8_t size, float value)
{
  int32_t scaled = JY62_Test_Scale100(value);
  int32_t whole;
  int32_t fraction;

  if (scaled < 0)
  {
    scaled = -scaled;
    whole = scaled / 100;
    fraction = scaled % 100;
    snprintf(buf, size, "-%ld.%02ld", (long)whole, (long)fraction);
    return;
  }

  whole = scaled / 100;
  fraction = scaled % 100;
  snprintf(buf, size, "%ld.%02ld", (long)whole, (long)fraction);
}

static void JY62_Test_Update(void)
{
  static uint32_t jy62_test_last_print = 0;
  uint32_t now = HAL_GetTick();

  JY62_Update();

  if ((now - jy62_test_last_print) >= JY62_TEST_PRINT_MS)
  {
    char uart_buf[80] = {0};
    char roll_buf[12] = {0};
    char pitch_buf[12] = {0};
    char yaw_buf[12] = {0};
    char gz_buf[12] = {0};
    int len;

    jy62_test_last_print = now;
    JY62_Test_FormatDecimal(roll_buf, sizeof(roll_buf), jy62_data.roll_deg);
    JY62_Test_FormatDecimal(pitch_buf, sizeof(pitch_buf), jy62_data.pitch_deg);
    JY62_Test_FormatDecimal(yaw_buf, sizeof(yaw_buf), jy62_data.yaw_deg);
    JY62_Test_FormatDecimal(gz_buf, sizeof(gz_buf), jy62_data.gz_dps);
    len = snprintf(uart_buf, sizeof(uart_buf),
                   "%s,%s,%s,%s\r\n",
                   roll_buf,
                   pitch_buf,
                   yaw_buf,
                   gz_buf);
    if (len > 0)
    {
      HAL_UART_Transmit(&huart2, (uint8_t *)uart_buf, (uint16_t)len, 100);
    }
  }
}
#endif

#if PID_DEBUG_MODE
#define PID_DEBUG_STEP_MS      3000U
#define PID_DEBUG_PRINT_MS     50U

static int16_t PID_Debug_GetTarget(void)
{
  static const int16_t targets[] = {100, 200, 300, 400, 500, 600, 500, 400, 300, 200, 100};
  static uint32_t pid_debug_start_tick = 0;
  uint32_t index;

  if (pid_debug_start_tick == 0U)
  {
    pid_debug_start_tick = HAL_GetTick();
  }

  index = (HAL_GetTick() - pid_debug_start_tick) / PID_DEBUG_STEP_MS;
  if (index >= (sizeof(targets) / sizeof(targets[0])))
  {
    index = (sizeof(targets) / sizeof(targets[0])) - 1U;
  }

  return targets[index];
}

static void PID_Debug_Update(void)
{
  static uint32_t pid_debug_last_print = 0;
  int16_t target = PID_Debug_GetTarget();
  uint32_t now = HAL_GetTick();

  drive_user_defined(target, target);
  Motor_PID_Service();

  if ((now - pid_debug_last_print) >= PID_DEBUG_PRINT_MS)
  {
    Motor_DebugStatus_t status;
    char uart_buf[128] = {0};
    int len;

    pid_debug_last_print = now;
    Motor_Debug_GetStatus(&status);
    len = snprintf(uart_buf, sizeof(uart_buf),
                   "d:%d,%d,%d,%d,%d,%d\n",
                   status.target_left,
                   status.measured_left,
                   status.output_left,
                   status.target_right,
                   status.measured_right,
                   status.output_right);
    if (len > 0)
    {
      HAL_UART_Transmit(&huart2, (uint8_t *)uart_buf, (uint16_t)len, 100);
    }
  }
}
#endif

/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{

  /* USER CODE BEGIN 1 */
  uint32_t control_last_tick = 0;

  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_DMA_Init();
  MX_ADC2_Init();
  MX_TIM1_Init();
  MX_TIM4_Init();
  MX_I2C1_Init();
  MX_USART2_UART_Init();
  MX_TIM3_Init();
  MX_TIM5_Init();
  MX_TIM8_Init();
  MX_TIM2_Init();
  MX_TIM9_Init();
  MX_USART3_UART_Init();
  /* USER CODE BEGIN 2 */
  
#if STATE_UART_DEBUG_MODE
  MOTOR_Init();
  ENCODER_Init();
  ENCODER_ResetAll();
  Motor_PID_Init();
  JY62_Init();
  Backup_Init();
  MOTOR_StopAll();
  Startup_WaitForTrigger();
  Vision_Init();
  JY62_Init();
  Vision_SendCmd('N');
  Robot_Control_Init();
#elif SHADE_OLED_TEST_MODE
  OLED_Init();
  OLED_Clear();
  Shade_Sensor_Init();
  OLED_ShowString(1, 1, "Gray Sensor Test");
  OLED_ShowString(2, 1, "A0:---- A1:----");
  OLED_ShowString(3, 1, "V0: -.---V");
  OLED_ShowString(4, 1, "V1: -.---V");
#elif SHADE_UART_TEST_MODE
  Shade_Sensor_Init();
#elif BACKUP_TEST_MODE
  MOTOR_Init();
  ENCODER_Init();
  ENCODER_ResetAll();
  Motor_PID_Init();
  JY62_Init();
  Backup_Init();
  MOTOR_StopAll();
  OLED_Init();
  OLED_Clear();
  OLED_ShowString(1, 1, "Wait Team...");
  Startup_WaitForTrigger();
  Vision_Init();
  Vision_SendCmd('N');
  OLED_Clear();
  OLED_ShowString(1, 1, "Backup Test");
  OLED_ShowString(2, 1, "Raw : ");
  OLED_ShowString(3, 1, "Last: ");
  OLED_ShowString(4, 1, "Stg : ");
  Backup_Test_LastNonXType = 'X';
  Shade_TestInject_Enable(BACKUP_TEST_OFF_STAGE_ADC, BACKUP_TEST_OFF_STAGE_ADC);
#elif IR_OLED_TEST_MODE
  /* IR 测试改为串口输出到上位机 */
#elif VISION_OLED_TEST_MODE
  OLED_Init();
  OLED_Clear();
  OLED_ShowString(1, 1, "Wait Team...");
  Startup_WaitForTrigger();
  Vision_Init();
  OLED_ShowString(1, 1, (Current_Team == TEAM_YELLOW) ? "Vision Y" : "Vision B");
#elif JY62_TEST_MODE
  JY62_Init();
#elif PID_DEBUG_MODE
  MOTOR_Init();
  ENCODER_Init();
  ENCODER_ResetAll();
  Motor_PID_Init();
  MOTOR_StopAll();
#elif UART_TEST_MODE
  (void)control_last_tick;
  HAL_UART_Transmit(&huart2, (uint8_t *)"USART2 TEST START\r\n", 19, 100);
#else
  MOTOR_Init();
  ENCODER_Init();
  ENCODER_ResetAll();
  Motor_PID_Init();
  JY62_Init();
  Backup_Init();
  MOTOR_StopAll();
  Startup_WaitForTrigger();
  Vision_Init();
  JY62_Init();
  Vision_SendCmd('N');
  Robot_Control_Init();
#endif
  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */

  while (1)
  {
    MOTOR_Service();
#if STATE_UART_DEBUG_MODE
    Edge_Sensor_Detect();
    JY62_Update();
    uint32_t now = HAL_GetTick();
    if ((now - control_last_tick) >= 5U)
    {
      control_last_tick = now;
      Robot_Control_Update();
      Motor_PID_Service();
    }
    State_Debug_Update();
#elif SHADE_OLED_TEST_MODE
    site_detect_shade();

    uint32_t adc0 = shade_v0;
    uint32_t adc1 = shade_v1;
    uint32_t mv0 = (uint32_t)(voltage_v0 * 1000.0f + 0.5f);
    uint32_t mv1 = (uint32_t)(voltage_v1 * 1000.0f + 0.5f);
    char line2[17] = {0};
    char line3[17] = {0};
    char line4[17] = {0};

    snprintf(line2, sizeof(line2), "A0:%4lu A1:%4lu", adc0, adc1);
    snprintf(line3, sizeof(line3), "V0:%1lu.%03luV", mv0 / 1000U, mv0 % 1000U);
    snprintf(line4, sizeof(line4), "V1:%1lu.%03luV", mv1 / 1000U, mv1 % 1000U);

    OLED_ShowString(2, 1, line2);
    OLED_ShowString(3, 1, line3);
    OLED_ShowString(4, 1, line4);
    HAL_Delay(100);
#elif SHADE_UART_TEST_MODE
    site_detect_shade();

    uint32_t adc0 = shade_v0;
    uint32_t adc1 = shade_v1;
    uint32_t mv0 = (uint32_t)(voltage_v0 * 1000.0f + 0.5f);
    uint32_t mv1 = (uint32_t)(voltage_v1 * 1000.0f + 0.5f);
    char uart_buf[96] = {0};
    int len = snprintf(uart_buf, sizeof(uart_buf),
                       "A0:%lu,V0:%lumV,A1:%lu,V1:%lumV\r\n",
                       adc0, mv0, adc1, mv1);
    if (len > 0)
    {
      HAL_UART_Transmit(&huart2, (uint8_t *)uart_buf, (uint16_t)len, 100);
    }
    HAL_Delay(100);
#elif BACKUP_TEST_MODE
    /* 灰度坏了：测试时始终模拟台下，不做上台检测，成功上台后手动停机 */
    Shade_TestInject_Enable(BACKUP_TEST_OFF_STAGE_ADC, BACKUP_TEST_OFF_STAGE_ADC);
    Backup_Update();
    Motor_PID_Service();
    if (vision_target.type != 'X')
    {
      Backup_Test_LastNonXType = vision_target.type;
    }
    OLED_ShowChar(2, 7, vision_target.type);
    OLED_ShowChar(3, 7, Backup_Test_LastNonXType);
    OLED_ShowNum(4, 7, Backup_DebugGetStage(), 1);
    HAL_Delay(10);
#elif IR_OLED_TEST_MODE
    Obs_Sensor_ReadAll();

    char uart_buf[128] = {0};
    int len = snprintf(uart_buf, sizeof(uart_buf),
                       "IR1:%d,IR2:%d,IR3:%d,IR4:%d,IR5:%d,IR6:%d,IR7:%d,IR8:%d,IR9:%d,IR10:%d,IR11:%d,IR12:%d,IR13:%d\r\n",
                       Obs_Data.IR1 == GPIO_PIN_SET ? 1 : 0,
                       Obs_Data.IR2 == GPIO_PIN_SET ? 1 : 0,
                       Obs_Data.IR3 == GPIO_PIN_SET ? 1 : 0,
                       Obs_Data.IR4 == GPIO_PIN_SET ? 1 : 0,
                       Obs_Data.IR5 == GPIO_PIN_SET ? 1 : 0,
                       Obs_Data.IR6 == GPIO_PIN_SET ? 1 : 0,
                       Obs_Data.IR7 == GPIO_PIN_SET ? 1 : 0,
                       Obs_Data.IR8 == GPIO_PIN_SET ? 1 : 0,
                       Obs_Data.IR9 == GPIO_PIN_SET ? 1 : 0,
                       Obs_Data.IR10 == GPIO_PIN_SET ? 1 : 0,
                       Obs_Data.IR11 == GPIO_PIN_SET ? 1 : 0,
                       Obs_Data.IR12 == GPIO_PIN_SET ? 1 : 0,
                       Obs_Data.IR13 == GPIO_PIN_SET ? 1 : 0);
    if (len > 0)
    {
      HAL_UART_Transmit(&huart2, (uint8_t *)uart_buf, (uint16_t)len, 100);
    }
    HAL_Delay(1000);
#elif VISION_OLED_TEST_MODE
    {
      if (Vision_IsTimeout()) {
        OLED_ShowString(2, 1, "No Data         ");
        OLED_ShowString(3, 1, "                ");
        OLED_ShowString(4, 1, "                ");
      } else {
        /* 第2行: T:x  dir:+xxx */
        OLED_ShowString(2, 1, "T:  dir:        ");
        OLED_ShowChar(2, 3, vision_target.type);
        OLED_ShowSignedNum(2, 9, vision_target.dir, 3);

        /* 第3行: cx:xxxx cy:xxxx */
        OLED_ShowString(3, 1, "cx:     cy:     ");
        OLED_ShowSignedNum(3, 4, vision_target.cx, 4);
        OLED_ShowSignedNum(3, 12, vision_target.cy, 4);

        /* 第4行: area:xxxxxxx */
        OLED_ShowString(4, 1, "area:           ");
        OLED_ShowSignedNum(4, 6, vision_target.area, 7);
      }
      HAL_Delay(100);
    }
#elif JY62_TEST_MODE
    JY62_Test_Update();
    HAL_Delay(10);
#elif PID_DEBUG_MODE
    PID_Debug_Update();
    HAL_Delay(1);
#elif UART_TEST_MODE
    HAL_UART_Transmit(&huart2, (uint8_t *)"USART2 OK\r\n", 11, 100);
    HAL_Delay(2000);
#else
    Edge_Sensor_Detect();
    JY62_Update();
    uint32_t now = HAL_GetTick();
    if ((now - control_last_tick) >= 5U)
    {
      control_last_tick = now;
      Robot_Control_Update();
      Motor_PID_Service();
    }
#endif
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
  }
  /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  /** Configure the main internal regulator output voltage
  */
  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI;
  RCC_OscInitStruct.HSIState = RCC_HSI_ON;
  RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSI;
  RCC_OscInitStruct.PLL.PLLM = 8;
  RCC_OscInitStruct.PLL.PLLN = 168;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV2;
  RCC_OscInitStruct.PLL.PLLQ = 4;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV4;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV2;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_5) != HAL_OK)
  {
    Error_Handler();
  }
}

/* USER CODE BEGIN 4 */

/* USER CODE END 4 */

/**
  * @brief  Period elapsed callback in non blocking mode
  * @note   This function is called  when TIM7 interrupt took place, inside
  * HAL_TIM_IRQHandler(). It makes a direct call to HAL_IncTick() to increment
  * a global variable "uwTick" used as application time base.
  * @param  htim : TIM handle
  * @retval None
  */
void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim)
{
  /* USER CODE BEGIN Callback 0 */

  /* USER CODE END Callback 0 */
  if (htim->Instance == TIM7)
  {
    HAL_IncTick();
  }
  /* USER CODE BEGIN Callback 1 */

  /* USER CODE END Callback 1 */
}

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}
#ifdef USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
