/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file dm_introspect.c
 * @brief Data model introspection, param update observation and write injection.
 */

#include "node_internal.h"

#include "esp_rmaker_data_model_introspect.h"

/* Error types */
#include "esp_rmaker_error_types.h"

/* Platform common headers */
#include "osal_log.h"
#include "osal_mem_alloc.h"

/* Standard C headers */
#include <inttypes.h>

/* Global variables *******************************************************/

/**
 * @brief Tag for introspection.
 */
static const char *TAG = "esp_rmaker_introspect";

/**
 * @brief Maximum number of param update observers.
 */
#define PARAM_UPDATE_OBSERVER_MAX_COUNT 4

/**
 * @brief Param update observer slot.
 *
 * Not synchronized against in-flight notifications; register before
 * esp_rmaker_start(), per the API contract.
 */
typedef struct {
    void *priv;
    esp_rmaker_param_update_observer_t cb;
} __param_update_observer_t;

/**
 * @brief Registered param update observers.
 */
static __param_update_observer_t __observers[PARAM_UPDATE_OBSERVER_MAX_COUNT];

/* Function definitions *******************************************************/

esp_rmaker_error_t esp_rmaker_node_for_each_device(const esp_rmaker_node_t *node,
        esp_rmaker_device_visit_cb_t cb, void *priv)
{
    if (!node || !cb) {
        return ESP_RMAKER_INVALID_ARG;
    }
    esp_rmaker_node_lock(node);

    size_t count = 0;
    for (_esp_rmaker_device_t *device = esp_rmaker_node_get_first_device(node); device; device = device->next) {
        count++;
    }
    if (count == 0) {
        esp_rmaker_node_unlock(node);
        return ESP_RMAKER_OK;
    }
    const esp_rmaker_device_t **devices = OSAL_CALLOC_EXTRAM(count, sizeof(esp_rmaker_device_t *));
    if (!devices) {
        esp_rmaker_node_unlock(node);
        OSAL_LOGE(TAG, "Could not allocate memory for %" PRIu32 " device handle(s).", (uint32_t)count);
        return ESP_RMAKER_NO_MEM;
    }
    size_t num_devices = 0;
    for (_esp_rmaker_device_t *device = esp_rmaker_node_get_first_device(node);
            device && num_devices < count; device = device->next) {
        devices[num_devices++] = (const esp_rmaker_device_t *)device;
    }
    esp_rmaker_node_unlock(node);

    /* Visit the snapshot with the lock released, so visitors can call APIs that
     * take the node lock themselves */
    esp_rmaker_error_t err = ESP_RMAKER_OK;
    for (size_t i = 0; i < num_devices; i++) {
        err = cb(devices[i], priv);
        if (err != ESP_RMAKER_OK) {
            break;
        }
    }
    free(devices);
    return err;
}

esp_rmaker_error_t esp_rmaker_device_for_each_param(const esp_rmaker_device_t *device,
        esp_rmaker_param_visit_cb_t cb, void *priv)
{
    _esp_rmaker_device_t *_device = (_esp_rmaker_device_t *)device;
    if (!_device || !cb) {
        return ESP_RMAKER_INVALID_ARG;
    }
    /* NULL until the device is attached to a node (construction phase) */
    const esp_rmaker_node_t *lnode = _device->parent;
    if (lnode) {
        esp_rmaker_node_lock(lnode);
    }

    size_t count = 0;
    for (_esp_rmaker_param_t *param = _device->params; param; param = param->next) {
        count++;
    }
    const esp_rmaker_param_t **params = count ? OSAL_CALLOC_EXTRAM(count, sizeof(esp_rmaker_param_t *)) : NULL;
    if (count && !params) {
        if (lnode) {
            esp_rmaker_node_unlock(lnode);
        }
        OSAL_LOGE(TAG, "Could not allocate memory for %" PRIu32 " param handle(s).", (uint32_t)count);
        return ESP_RMAKER_NO_MEM;
    }
    size_t num_params = 0;
    for (_esp_rmaker_param_t *param = _device->params; param && num_params < count; param = param->next) {
        params[num_params++] = (const esp_rmaker_param_t *)param;
    }
    if (lnode) {
        esp_rmaker_node_unlock(lnode);
    }

    /* Visit the snapshot with the lock released, so visitors can call APIs that
     * take the node lock themselves */
    esp_rmaker_error_t err = ESP_RMAKER_OK;
    for (size_t i = 0; i < num_params; i++) {
        err = cb(params[i], priv);
        if (err != ESP_RMAKER_OK) {
            break;
        }
    }
    free(params);
    return err;
}

esp_rmaker_error_t esp_rmaker_param_update_observer_register(esp_rmaker_param_update_observer_t cb, void *priv)
{
    if (!cb) {
        return ESP_RMAKER_INVALID_ARG;
    }
    int free_slot = -1;
    for (int i = 0; i < PARAM_UPDATE_OBSERVER_MAX_COUNT; i++) {
        if (__observers[i].cb == cb) {
            return ESP_RMAKER_ALREADY_EXISTS;
        }
        if (!__observers[i].cb && free_slot < 0) {
            free_slot = i;
        }
    }
    if (free_slot < 0) {
        OSAL_LOGE(TAG, "No free param update observer slot.");
        return ESP_RMAKER_NO_MEM;
    }
    __observers[free_slot].priv = priv;
    __observers[free_slot].cb = cb;
    return ESP_RMAKER_OK;
}

esp_rmaker_error_t esp_rmaker_param_update_observer_unregister(esp_rmaker_param_update_observer_t cb)
{
    if (!cb) {
        return ESP_RMAKER_INVALID_ARG;
    }
    for (int i = 0; i < PARAM_UPDATE_OBSERVER_MAX_COUNT; i++) {
        if (__observers[i].cb == cb) {
            __observers[i].cb = NULL;
            __observers[i].priv = NULL;
            return ESP_RMAKER_OK;
        }
    }
    return ESP_RMAKER_NOT_FOUND;
}

void esp_rmaker_param_update_observers_notify(const esp_rmaker_param_t *param, const esp_rmaker_param_val_t *val)
{
    _esp_rmaker_param_t *_param = (_esp_rmaker_param_t *)param;
    if (!_param || !val) {
        return;
    }
    const esp_rmaker_device_t *device = (const esp_rmaker_device_t *)_param->parent;
    for (int i = 0; i < PARAM_UPDATE_OBSERVER_MAX_COUNT; i++) {
        void *priv = __observers[i].priv;
        esp_rmaker_param_update_observer_t cb = __observers[i].cb;
        if (cb) {
            cb(device, param, val, priv);
        }
    }
}

esp_rmaker_error_t esp_rmaker_device_write_params(const esp_rmaker_device_t *device,
        const esp_rmaker_param_write_req_t write_req[], uint8_t count, esp_rmaker_req_src_t src)
{
    _esp_rmaker_device_t *_device = (_esp_rmaker_device_t *)device;
    if (!_device || !write_req || count == 0 || src >= ESP_RMAKER_REQ_SRC_MAX) {
        return ESP_RMAKER_INVALID_ARG;
    }
    for (uint8_t i = 0; i < count; i++) {
        _esp_rmaker_param_t *_param = (_esp_rmaker_param_t *)write_req[i].param;
        if (!_param || _param->parent != _device) {
            OSAL_LOGE(TAG, "Write request param does not belong to device %s.", _device->id);
            return ESP_RMAKER_INVALID_ARG;
        }
        if (_param->val.type != write_req[i].val.type) {
            OSAL_LOGE(TAG, "Write request value type mismatch for param %s.", _param->id);
            return ESP_RMAKER_INVALID_ARG;
        }
    }
    if (!_device->bulk_write_cb) {
        return ESP_RMAKER_INVALID_STATE;
    }
    esp_rmaker_write_ctx_t ctx = {
        .src = src,
    };
    return _device->bulk_write_cb(device, write_req, count, _device->priv_data, &ctx);
}
