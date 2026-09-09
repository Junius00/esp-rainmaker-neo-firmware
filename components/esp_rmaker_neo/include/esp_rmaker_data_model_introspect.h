/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file esp_rmaker_data_model_introspect.h
 * @brief Data model introspection, param update observation and write injection.
 *
 * Introspection here means exposing state that is normally internal to the
 * stack - the node's device/param tree and every applied parameter value
 * change - to a protocol reflector: any component that bridges an external
 * protocol with the Neo protocol (e.g. a Matter mirror, local control).
 *
 * A reflector uses these APIs to walk the node's data model, observe parameter
 * value changes from any source, and deliver parameter writes to a device's
 * registered write callback.
 */

#ifndef __ESP_RMAKER_DATA_MODEL_INTROSPECT_H__
#define __ESP_RMAKER_DATA_MODEL_INTROSPECT_H__

/* Data model types (device/param handles, write request, value types) */
#include "esp_rmaker_data_model.h"

/* Request source */
#include "esp_rmaker_state.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Enumeration *******************************************************/

/**
 * @brief Visitor for ::esp_rmaker_node_for_each_device.
 *
 * @param[in] device Device handle.
 * @param[in] priv Private data passed to the iteration call.
 *
 * @return ESP_RMAKER_OK to continue iteration, any other value to stop
 *         (the value is propagated to the caller).
 */
typedef esp_rmaker_error_t (*esp_rmaker_device_visit_cb_t)(const esp_rmaker_device_t *device, void *priv);

/**
 * @brief Visitor for ::esp_rmaker_device_for_each_param.
 *
 * @param[in] param Parameter handle.
 * @param[in] priv Private data passed to the iteration call.
 *
 * @return ESP_RMAKER_OK to continue iteration, any other value to stop
 *         (the value is propagated to the caller).
 */
typedef esp_rmaker_error_t (*esp_rmaker_param_visit_cb_t)(const esp_rmaker_param_t *param, void *priv);

/**
 * @brief Visit every device (and service) of a node.
 *
 * The device list is snapshotted under the per-node lock and the visitor runs
 * with that lock released, so it is free to call APIs that take it (e.g.
 * esp_rmaker_param_update(), the metadata getters). Devices added or removed
 * after the snapshot are not reflected in the walk. Use
 * esp_rmaker_device_is_service() to filter services out if needed.
 *
 * @param[in] node Node handle.
 * @param[in] cb Visitor callback.
 * @param[in] priv Private data passed through to the visitor.
 *
 * @return ESP_RMAKER_OK if iteration completed, the visitor's return value
 *         if it stopped iteration, ESP_RMAKER_NO_MEM if the snapshot could not
 *         be allocated, error code otherwise.
 */
esp_rmaker_error_t esp_rmaker_node_for_each_device(const esp_rmaker_node_t *node,
        esp_rmaker_device_visit_cb_t cb, void *priv);

/**
 * @brief Visit every parameter of a device.
 *
 * Same snapshot semantics as ::esp_rmaker_node_for_each_device.
 *
 * @param[in] device Device handle.
 * @param[in] cb Visitor callback.
 * @param[in] priv Private data passed through to the visitor.
 *
 * @return ESP_RMAKER_OK if iteration completed, the visitor's return value
 *         if it stopped iteration, ESP_RMAKER_NO_MEM if the snapshot could not
 *         be allocated, error code otherwise.
 */
esp_rmaker_error_t esp_rmaker_device_for_each_param(const esp_rmaker_device_t *device,
        esp_rmaker_param_visit_cb_t cb, void *priv);

/* Param update observer *******************************************************/

/**
 * @brief Callback invoked after a parameter value update has been applied.
 *
 * Invoked by esp_rmaker_param_update() for EVERY applied update, from any
 * source (application, cloud/schedule via the device write callback, init-time
 * persisted value replay) and including updates where the new value equals the
 * old one.
 *
 * @note It may fire before the observing component has seen the param (e.g.
 *       persisted value replay during device creation) - observers must
 *       tolerate unknown params. @p val points to a transient copy valid only
 *       for the duration of the callback; copy it out if it must outlive the
 *       call.
 *
 * @param[in] device Device the parameter belongs to (NULL if not yet added to a device).
 * @param[in] param Parameter handle.
 * @param[in] val The applied value.
 * @param[in] priv Private data passed at registration.
 */
typedef void (*esp_rmaker_param_update_observer_t)(const esp_rmaker_device_t *device,
        const esp_rmaker_param_t *param, const esp_rmaker_param_val_t *val, void *priv);

/**
 * @brief Register a parameter update observer.
 *
 * Registration is not synchronized against in-flight param updates: register
 * before esp_rmaker_start() (or before the observed params can change).
 *
 * @param[in] cb Observer callback.
 * @param[in] priv Private data passed to the callback.
 *
 * @return ESP_RMAKER_OK on success, ESP_RMAKER_NO_MEM if the observer table
 *         is full, error code otherwise.
 */
esp_rmaker_error_t esp_rmaker_param_update_observer_register(esp_rmaker_param_update_observer_t cb, void *priv);

/**
 * @brief Unregister a parameter update observer.
 *
 * Does not guarantee the callback is not currently executing on another task.
 *
 * @param[in] cb Observer callback used at registration.
 *
 * @return ESP_RMAKER_OK on success, ESP_RMAKER_NOT_FOUND if not registered.
 */
esp_rmaker_error_t esp_rmaker_param_update_observer_unregister(esp_rmaker_param_update_observer_t cb);

/* Write injection *******************************************************/

/**
 * @brief Deliver parameter write requests to a device's write callback.
 *
 * Programmatic (JSON-free) equivalent of what an incoming cloud set-params
 * request does: invokes the device's bulk write callback (which also covers
 * devices registered with the per-param write callback) with the given
 * request source. The callback decides whether to apply the values (and
 * drive hardware), exactly as for a cloud write.
 *
 * @param[in] device Device handle.
 * @param[in] write_req Array of parameter write requests. Each param must
 *            belong to @p device and the value type must match the param.
 * @param[in] count Number of entries in @p write_req.
 * @param[in] src Source of the request (e.g. ESP_RMAKER_REQ_SRC_EXTERNAL).
 *
 * @return ESP_RMAKER_OK on success, error code otherwise.
 */
esp_rmaker_error_t esp_rmaker_device_write_params(const esp_rmaker_device_t *device,
        const esp_rmaker_param_write_req_t write_req[], uint8_t count, esp_rmaker_req_src_t src);

#ifdef __cplusplus
}
#endif

#endif /* __ESP_RMAKER_DATA_MODEL_INTROSPECT_H__ */
