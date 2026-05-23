/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.h
  * @brief          : Header for main.c file.
  *                   This file contains the common defines of the application.
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

/* Define to prevent recursive inclusion -------------------------------------*/
#ifndef __MAIN_H
#define __MAIN_H

#ifdef __cplusplus
extern "C" {
#endif

/* Includes ------------------------------------------------------------------*/
#include "stm32f4xx_hal.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */

/* USER CODE END Includes */

/* Exported types ------------------------------------------------------------*/
/* USER CODE BEGIN ET */

/* USER CODE END ET */

/* Exported constants --------------------------------------------------------*/
/* USER CODE BEGIN EC */

/* USER CODE END EC */

/* Exported macro ------------------------------------------------------------*/
/* USER CODE BEGIN EM */

/* USER CODE END EM */

/* Exported functions prototypes ---------------------------------------------*/
void Error_Handler(void);

/* USER CODE BEGIN EFP */

/* USER CODE END EFP */

/* Private defines -----------------------------------------------------------*/
#define IR_3_Pin GPIO_PIN_2
#define IR_3_GPIO_Port GPIOE
#define IR_4_Pin GPIO_PIN_3
#define IR_4_GPIO_Port GPIOE
#define IR_5_Pin GPIO_PIN_4
#define IR_5_GPIO_Port GPIOE
#define shade_sensor1_Pin GPIO_PIN_0
#define shade_sensor1_GPIO_Port GPIOC
#define shade_sensor2_Pin GPIO_PIN_1
#define shade_sensor2_GPIO_Port GPIOC
#define IR11_Pin GPIO_PIN_2
#define IR11_GPIO_Port GPIOC
#define IR12_Pin GPIO_PIN_3
#define IR12_GPIO_Port GPIOC
#define encoder1_a_Pin GPIO_PIN_0
#define encoder1_a_GPIO_Port GPIOA
#define encoder1_b_Pin GPIO_PIN_1
#define encoder1_b_GPIO_Port GPIOA
#define IR_9_Pin GPIO_PIN_4
#define IR_9_GPIO_Port GPIOA
#define IR_10_Pin GPIO_PIN_5
#define IR_10_GPIO_Port GPIOA
#define encoder2_a_Pin GPIO_PIN_6
#define encoder2_a_GPIO_Port GPIOA
#define encoder2_b_Pin GPIO_PIN_7
#define encoder2_b_GPIO_Port GPIOA
#define IR13_Pin GPIO_PIN_4
#define IR13_GPIO_Port GPIOC
#define encoder3_a_Pin GPIO_PIN_9
#define encoder3_a_GPIO_Port GPIOE
#define encoder3_b_Pin GPIO_PIN_11
#define encoder3_b_GPIO_Port GPIOE
#define IR_6_Pin GPIO_PIN_12
#define IR_6_GPIO_Port GPIOE
#define IR_7_Pin GPIO_PIN_13
#define IR_7_GPIO_Port GPIOE
#define IR_8_Pin GPIO_PIN_14
#define IR_8_GPIO_Port GPIOE
#define jy62_tx_Pin GPIO_PIN_10
#define jy62_tx_GPIO_Port GPIOB
#define jy62_rx_Pin GPIO_PIN_11
#define jy62_rx_GPIO_Port GPIOB
#define motor1_a_Pin GPIO_PIN_12
#define motor1_a_GPIO_Port GPIOD
#define motor1_b_Pin GPIO_PIN_13
#define motor1_b_GPIO_Port GPIOD
#define motor2_a_Pin GPIO_PIN_14
#define motor2_a_GPIO_Port GPIOD
#define motor2_b_Pin GPIO_PIN_15
#define motor2_b_GPIO_Port GPIOD
#define encoder4_a_Pin GPIO_PIN_6
#define encoder4_a_GPIO_Port GPIOC
#define encoder4_b_Pin GPIO_PIN_7
#define encoder4_b_GPIO_Port GPIOC
#define pi_TX_Pin GPIO_PIN_5
#define pi_TX_GPIO_Port GPIOD
#define pi_RX_Pin GPIO_PIN_6
#define pi_RX_GPIO_Port GPIOD
#define oled_scl_Pin GPIO_PIN_6
#define oled_scl_GPIO_Port GPIOB
#define oled_sda_Pin GPIO_PIN_7
#define oled_sda_GPIO_Port GPIOB
#define IR_1_Pin GPIO_PIN_0
#define IR_1_GPIO_Port GPIOE
#define IR_2_Pin GPIO_PIN_1
#define IR_2_GPIO_Port GPIOE

/* USER CODE BEGIN Private defines */

/* USER CODE END Private defines */

#ifdef __cplusplus
}
#endif

#endif /* __MAIN_H */
