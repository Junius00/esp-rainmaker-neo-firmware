/*
 * SPDX-FileCopyrightText: 2025-2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file ota_partition.c
 * @brief Partition utility functions
 */

/* Includes **********************************************************************/

/* Declaration includes */
#include "util/ota_partition.h"

/* OTA common includes */
#include "osal_ota.h"

/* Private function definitions *****************************************************/

bool esp_rmaker_ota_partition_running_is_pending_verify(void)
{
    const osal_ota_partition_t *partition = osal_ota_get_running_partition();
    if (partition == NULL) {
        return false;
    }
    osal_ota_img_states_t state;
    if (osal_ota_get_state_partition(partition, &state) == OSAL_ERR_OK) {
        return (state == OSAL_OTA_IMG_PENDING_VERIFY);
    }

    return false;
}

bool esp_rmaker_ota_partition_rollback_detected(void)
{
    const osal_ota_partition_t *running = osal_ota_get_running_partition();
    if (running == NULL) {
        return false;
    }

    /* The bootloader marks a rolled back image invalid, and that slot is never the
     * one we run now. */
    const osal_ota_partition_t *last_invalid = osal_ota_get_last_invalid_partition();
    return (last_invalid != NULL) && (last_invalid->address != running->address);
}
