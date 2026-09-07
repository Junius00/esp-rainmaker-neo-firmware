/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file rm_mirror_internal.h
 * @brief Declarations shared between the mirror engine's modules.
 */

#ifndef __RM_MIRROR_INTERNAL_H__
#define __RM_MIRROR_INTERNAL_H__

#include "esp_rmaker_data_model_introspect.h"

#include "rm_mirror_engine.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Initialize the sync module (binding table, lock, port reference).
 */
esp_rmaker_error_t rm_mirror_sync_init(const rm_mirror_port_ops_t *port);

/**
 * @brief Lower the node into a Matter tree via the port (called by engine init).
 */
esp_rmaker_error_t rm_mirror_lowering_run(const esp_rmaker_node_t *node, const rm_mirror_port_ops_t *port);

/**
 * @brief Lower a single device (rule match, endpoint/cluster creation, bindings).
 */
esp_rmaker_error_t rm_mirror_lowering_lower_device(const rm_mirror_port_ops_t *port,
        const esp_rmaker_device_t *device);

/**
 * @brief Outbound sync handler (the param update observer body). Exposed for
 *        deterministic testing without task/observer plumbing.
 */
void rm_mirror_sync_handle_param_update(const esp_rmaker_param_t *param, const esp_rmaker_param_val_t *val);

/**
 * @brief Register a param <-> attribute binding with its seeded Matter value.
 */
esp_rmaker_error_t rm_mirror_sync_add_binding(const esp_rmaker_param_t *param,
        const rm_mirror_capability_t *cap, uint16_t endpoint_id, int32_t seeded_matter_val);

/**
 * @brief Register a composite (N params <-> M attributes) binding.
 *
 * @param[in] params Member param handles, ordered per the composite's
 *            param_types (comp->n_params entries).
 * @param[in] seeded_matter Seeded per-attribute Matter values, ordered per the
 *            composite's attribute_ids (comp->n_attributes entries).
 */
esp_rmaker_error_t rm_mirror_sync_add_composite_binding(const rm_mirror_composite_t *comp,
        const esp_rmaker_param_t *const *params, uint16_t endpoint_id, const int32_t *seeded_matter);

/**
 * @brief Current param value as an int32 in the RainMaker Neo domain (bool as 0/1).
 *
 * No transform is applied - this is the value composites convert from, and the
 * one the observer suppresses against.
 *
 * @return ESP_RMAKER_NOT_SUPPORTED for anything but a bool or int param.
 */
esp_rmaker_error_t rm_mirror_param_val_as_i32(const esp_rmaker_param_t *param, int32_t *out);

/**
 * @brief Current param value transformed into the Matter domain.
 *
 * Bool params cross as 0/1, int params as-is, float params through the capability's transform in
 * the float domain (so a scale transform keeps the fractional part).
 *
 * @return ESP_RMAKER_NOT_SUPPORTED for param value types the mirror does not carry.
 */
esp_rmaker_error_t rm_mirror_param_to_matter(const esp_rmaker_param_t *param,
        const rm_mirror_capability_t *cap, int32_t *out);

/**
 * @brief Whether the param holds state worth asserting on the Matter side.
 *
 * A write-only param is a request channel, not state: its value is a
 * compiled-in zero, and the attribute it matches is one the cluster manages itself. The binding
 * still exists - it carries writes outbound as the capability's write_as command.
 */
static inline bool rm_mirror_param_holds_state(const esp_rmaker_param_t *param)
{
    return (esp_rmaker_param_get_prop_flags((esp_rmaker_param_t *)param) & PROP_FLAG_READ) != 0;
}

/**
 * @brief Push one Matter-domain value at the port: writes the attribute (with its shadows), or
 *        hands it to the port's report_as hook for capabilities whose cluster does not expose a
 *        plain scalar attribute.
 *
 * @param[in] init true during lowering, before the Matter stack is up (uses the port's set rather
 *            than update entry point).
 */
esp_rmaker_error_t rm_mirror_port_push(const rm_mirror_port_ops_t *port, uint16_t endpoint_id,
                                       const rm_mirror_capability_t *cap, int32_t matter_val, bool init);

/** @brief A port attribute write: either attribute_set or attribute_update. */
typedef esp_rmaker_error_t (*rm_mirror_attr_write_fn)(uint16_t endpoint_id, uint32_t cluster_id,
        uint32_t attribute_id, int32_t value, rm_mirror_val_type_t type);

/**
 * @brief Write a capability's matched attribute through @p write, followed by
 *        the shadow attributes the mapping declares for it (same cluster, same
 *        value - e.g. ColorMode into EnhancedColorMode). Shadows are skipped
 *        when the matched write fails.
 */
esp_rmaker_error_t rm_mirror_write_capability_attr(rm_mirror_attr_write_fn write, uint16_t endpoint_id,
        const rm_mirror_capability_t *cap, int32_t matter_val);

/**
 * @brief Register a string binding: @p param (a string param, typically
 *        esp.param.name) syncs bidirectionally with the capability's matched
 *        attribute on @p endpoint_id, truncated to its transform max_len.
 */
esp_rmaker_error_t rm_mirror_sync_add_str_binding(const esp_rmaker_param_t *param,
        const rm_mirror_capability_t *cap, uint16_t endpoint_id);

#ifdef __cplusplus
}
#endif

#endif /* __RM_MIRROR_INTERNAL_H__ */
