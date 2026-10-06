/*
 * SPDX-FileCopyrightText: 2025-2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file ota_partition.h
 * @brief Partition utility functions
 */

#ifndef __OTA_PARTITION_H__
#define __OTA_PARTITION_H__

/* Includes **********************************************************************/

/* Standard includes */
#include <stdbool.h>

/* Public function declarations ***************************************************/

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Check if the running partition is pending verify
 *
 * @return True if the running partition is pending verify, false otherwise
 */
bool esp_rmaker_ota_partition_running_is_pending_verify(void);

/**
 * @brief Check if the bootloader rolled back to the previous firmware
 *
 * @note Only meaningful while the running partition is not pending verify. A rolled back
 * image stays marked invalid until the next update overwrites its slot.
 *
 * @return True if an app partition other than the running one is marked invalid, false otherwise
 */
bool esp_rmaker_ota_partition_rollback_detected(void);

#ifdef __cplusplus
}
#endif

#endif /* __OTA_PARTITION_H__ */
