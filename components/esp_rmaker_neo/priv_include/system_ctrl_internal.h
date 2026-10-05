/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file system_ctrl_internal.h
 * @brief Internal system control helpers shared within the RainMaker Neo SDK.
 */

#ifndef __SYSTEM_CTRL_INTERNAL_H__
#define __SYSTEM_CTRL_INTERNAL_H__

#include "esp_rmaker_error_types.h"
#include "esp_rmaker_system_ctrl.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Create the worker task and queue that run delayed resets. Safe to call more than once.
 * @return ESP_RMAKER_OK on success, ESP_RMAKER_NO_MEM if the task or the queue cannot be created.
 */
esp_rmaker_error_t esp_rmaker_system_ctrl_init(void);

/**
 * @brief Clear the RainMaker Neo-owned data NVS namespaces.
 *
 * Erases only the RainMaker Neo data namespaces without touching the rest of the NVS partition (e.g. network
 * credentials) and without rebooting. Shared by the public data reset (esp_rmaker_system_ctrl_data_reset),
 * public factory reset (esp_rmaker_system_ctrl_factory_reset) and the remote-control reset flow.
 *
 * @return ESP_RMAKER_OK on success, otherwise error code.
 */
esp_rmaker_error_t esp_rmaker_system_ctrl_clear_data_namespaces(void);

#ifdef __cplusplus
}
#endif

#endif /* __SYSTEM_CTRL_INTERNAL_H__ */
