/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file rm_mirror_lowering.c
 * @brief Lowering: walk the RainMaker Neo node and construct the Matter tree via the
 *        port, per the generated mapping tables.
 */

#include <stdlib.h>
#include <string.h>

#include "esp_rmaker_data_model_introspect.h"
#include "esp_rmaker_val.h"
#include "constants/esp_rmaker_nvs_common.h"
#include "util/esp_rmaker_nvs.h"

#include "rm_mirror_engine.h"
#include "rm_mirror_internal.h"

#include "osal_log.h"

static const char *TAG = "rm_mirror_lowering";

/* Marks that lowering has run at least once, i.e. that the boot-state attributes in the port's
 * store hold a value this node put there rather than a cluster default. */
#define RM_MIRROR_NVS_NAMESPACE      "rm_mirror"
#define RM_MIRROR_NVS_LOWERED_KEY    "lowered"

esp_rmaker_error_t rm_mirror_param_val_as_i32(const esp_rmaker_param_t *param, int32_t *out)
{
    esp_rmaker_param_val_t *val = esp_rmaker_param_get_val((esp_rmaker_param_t *)param);
    if (!val) {
        return ESP_RMAKER_FAIL;
    }
    switch (val->type) {
    case RMAKER_VAL_TYPE_BOOLEAN:
        *out = val->val.b ? 1 : 0;
        return ESP_RMAKER_OK;
    case RMAKER_VAL_TYPE_INTEGER:
        *out = val->val.i;
        return ESP_RMAKER_OK;
    default:
        return ESP_RMAKER_NOT_SUPPORTED;
    }
}

esp_rmaker_error_t rm_mirror_param_to_matter(const esp_rmaker_param_t *param,
        const rm_mirror_capability_t *cap, int32_t *out)
{
    esp_rmaker_param_val_t *val = esp_rmaker_param_get_val((esp_rmaker_param_t *)param);
    if (!val) {
        return ESP_RMAKER_FAIL;
    }
    switch (val->type) {
    case RMAKER_VAL_TYPE_BOOLEAN:
        *out = rm_mirror_xform_to_matter(&cap->xform, val->val.b ? 1 : 0);
        return ESP_RMAKER_OK;
    case RMAKER_VAL_TYPE_INTEGER:
        *out = rm_mirror_xform_to_matter(&cap->xform, val->val.i);
        return ESP_RMAKER_OK;
    case RMAKER_VAL_TYPE_FLOAT:
        *out = rm_mirror_xform_to_matter_f(&cap->xform, val->val.f);
        return ESP_RMAKER_OK;
    default:
        return ESP_RMAKER_NOT_SUPPORTED;
    }
}

/** Cached ::__lowered_before state: -1 = not read yet, 0/1 = value. */
static int __lowered_cache = -1;

/**
 * @brief Whether a previous boot already lowered this node.
 *
 * Before that, a boot-state attribute holds whatever default its cluster was created with, which
 * is the port's idea of a default and not the application's - so it must not be adopted.
 */
static bool __lowered_before(void)
{
    if (__lowered_cache < 0) {
        size_t len = 0;
        uint8_t *val = esp_rmaker_nvs_get_binary(RMAKER_NVS_PART_NAME, RM_MIRROR_NVS_NAMESPACE,
                       RM_MIRROR_NVS_LOWERED_KEY, &len);
        __lowered_cache = (val != NULL) ? 1 : 0;
        free(val);
    }
    return __lowered_cache == 1;
}

static void __mark_lowered(void)
{
    if (__lowered_before()) {
        return;
    }
    const uint8_t one = 1;
    if (esp_rmaker_nvs_update_binary(RMAKER_NVS_PART_NAME, RM_MIRROR_NVS_NAMESPACE,
                                     RM_MIRROR_NVS_LOWERED_KEY, &one, sizeof(one)) != ESP_RMAKER_OK) {
        OSAL_LOGW(TAG, "Could not record that the node has been lowered; the next boot will seed "
                  "the boot state from the params rather than adopt the restored values.");
        return;
    }
    __lowered_cache = 1;
}

static esp_rmaker_error_t __lower_capability(const rm_mirror_port_ops_t *port, uint16_t endpoint_id,
        const esp_rmaker_param_t *param, const rm_mirror_capability_t *cap)
{
    esp_rmaker_error_t err = port->cluster_create(endpoint_id, cap->cluster_id, cap->feature_map);
    if (err != ESP_RMAKER_OK) {
        return err;
    }

    /* Feed bounds-derived attributes: the param's min/max bound, transformed, lands on
     * the attribute the mapping names for it (CCT: kelvin min -> PhysicalMaxMireds) */
    esp_rmaker_param_val_t min, max;
    if ((cap->bounds_min_attr || cap->bounds_max_attr) &&
            esp_rmaker_param_get_bounds(param, &min, &max, NULL) == ESP_RMAKER_OK &&
            min.type == RMAKER_VAL_TYPE_INTEGER) {
        if (cap->bounds_min_attr) {
            port->attribute_set(endpoint_id, cap->cluster_id, cap->bounds_min_attr,
                                rm_mirror_xform_to_matter(&cap->xform, min.val.i), cap->bounds_value_type);
        }
        if (cap->bounds_max_attr) {
            port->attribute_set(endpoint_id, cap->cluster_id, cap->bounds_max_attr,
                                rm_mirror_xform_to_matter(&cap->xform, max.val.i), cap->bounds_value_type);
        }
    }

    /* Seed the attribute from the current param value and register the binding */
    int32_t matter_val = 0;
    esp_rmaker_error_t val_err = rm_mirror_param_to_matter(param, cap, &matter_val);
    if (val_err == ESP_RMAKER_NOT_SUPPORTED) {
        OSAL_LOGW(TAG, "Param type not mirrorable (bool/int/float only); skipping binding.");
        return ESP_RMAKER_OK;
    }
    if (val_err != ESP_RMAKER_OK) {
        return val_err;
    }

    /* A capability with a startup policy carries the node's boot state, and the port keeps that
     * attribute across reboots: on any boot but the first it already holds the state the node had
     * when it went down. Adopt that rather than overwrite it with the param's compiled-in default,
     * which is all the param can hold this early - the injection below hands it to the
     * application, and the StartUp* policies are then resolved against a real previous value. */
    int32_t restored = 0;
    const bool adopt = cap->startup_policy != RM_MIRROR_STARTUP_NONE && port->attribute_get &&
                       __lowered_before() &&
                       port->attribute_get(endpoint_id, cap->cluster_id, cap->attribute_id,
                                           &restored) == ESP_RMAKER_OK;
    if (adopt) {
        matter_val = restored;
    } else if (rm_mirror_param_holds_state(param)) {
        rm_mirror_port_push(port, endpoint_id, cap, matter_val, true);
    }

    /* After the seed, so that write still persists immediately; every write from
     * here on (all of them post-start) gets the deferral. */
    if (cap->deferred_persistence && port->attribute_defer_persistence) {
        port->attribute_defer_persistence(endpoint_id, cap->cluster_id, cap->attribute_id);
    }

    esp_rmaker_error_t bind_err = rm_mirror_sync_add_binding(param, cap, endpoint_id, matter_val);
    if (bind_err == ESP_RMAKER_OK && adopt) {
        rm_mirror_engine_apply_boot_value(endpoint_id, cap->cluster_id, cap->attribute_id, matter_val);
    }
    return bind_err;
}

/**
 * @brief Whether @p param_type appears in the rule's params or optional_params.
 */
static bool __rule_lists_param(const rm_mirror_devtype_rule_t *rule, const char *param_type)
{
    for (size_t i = 0; i < rule->n_params; i++) {
        if (strcmp(rule->param_types[i], param_type) == 0) {
            return true;
        }
    }
    for (size_t i = 0; i < rule->n_optional_params; i++) {
        if (strcmp(rule->optional_params[i], param_type) == 0) {
            return true;
        }
    }
    return false;
}

#if RM_MIRROR_USES_COMPOSITES
/**
 * @brief Lower every composite whose member params are all present (and
 *        rule-listed) on this endpoint: enable its cluster features, seed the
 *        derived attributes from current param values, register the binding.
 */
static esp_rmaker_error_t __lower_composites(const rm_mirror_port_ops_t *port, uint16_t endpoint_id,
        const esp_rmaker_device_t *device, const rm_mirror_devtype_rule_t *rule)
{
    for (size_t c = 0; c < rm_mirror_n_composites; c++) {
        const rm_mirror_composite_t *comp = &rm_mirror_composites[c];
        if (comp->n_params > RM_MIRROR_COMPOSITE_MAX_PARAMS ||
                comp->n_attributes > RM_MIRROR_COMPOSITE_MAX_ATTRS) {
            continue;
        }
        const esp_rmaker_param_t *members[RM_MIRROR_COMPOSITE_MAX_PARAMS] = {0};
        int32_t rmng_vals[RM_MIRROR_COMPOSITE_MAX_PARAMS] = {0};
        bool complete = true;
        for (size_t i = 0; complete && i < comp->n_params; i++) {
            complete = __rule_lists_param(rule, comp->param_types[i]);
            if (complete) {
                members[i] = esp_rmaker_device_get_param_by_type(device, comp->param_types[i]);
            }
            complete = complete && members[i] &&
                       (rm_mirror_param_val_as_i32(members[i], &rmng_vals[i]) == ESP_RMAKER_OK);
        }
        if (!complete) {
            continue;
        }

        esp_rmaker_error_t err = port->cluster_create(endpoint_id, comp->cluster_id, comp->feature_map);
        if (err != ESP_RMAKER_OK) {
            return err;
        }

        /* Seed the derived attributes from the members' current values */
        int32_t matter_vals[RM_MIRROR_COMPOSITE_MAX_ATTRS] = {0};
        rm_mirror_composite_to_matter(comp, rmng_vals, matter_vals);
        for (size_t i = 0; i < comp->n_attributes; i++) {
            port->attribute_set(endpoint_id, comp->cluster_id, comp->attribute_ids[i], matter_vals[i],
                                comp->value_types[i]);
            if (comp->deferred_persistence && port->attribute_defer_persistence) {
                port->attribute_defer_persistence(endpoint_id, comp->cluster_id, comp->attribute_ids[i]);
            }
        }

        err = rm_mirror_sync_add_composite_binding(comp, members, endpoint_id, matter_vals);
        if (err != ESP_RMAKER_OK) {
            return err;
        }
        OSAL_LOGI(TAG, "Composite %s lowered on endpoint %u.", comp->id, endpoint_id);
    }
    return ESP_RMAKER_OK;
}
#endif /* RM_MIRROR_USES_COMPOSITES */

static bool __device_has_param(const char *param_type, void *ctx)
{
    return esp_rmaker_device_get_param_by_type((const esp_rmaker_device_t *)ctx, param_type) != NULL;
}

typedef struct {
    const rm_mirror_port_ops_t *port;
    const rm_mirror_devtype_rule_t *rule;
    uint16_t endpoint_id;
} __lower_param_ctx_t;

static esp_rmaker_error_t __lower_param(const esp_rmaker_param_t *param, void *priv)
{
    const __lower_param_ctx_t *ctx = (const __lower_param_ctx_t *)priv;
    const char *param_type = esp_rmaker_param_get_type((esp_rmaker_param_t *)param);
    const rm_mirror_capability_t *cap = param_type ? rm_mirror_table_find_capability(param_type) : NULL;
    if (!cap || cap->node_scoped) {
        return ESP_RMAKER_OK; /* custom/unmapped param (cloud-only by design), or node-scoped
                               * (bound on endpoint 0 by the engine, not per device endpoint) */
    }
    if (cap->rule_gated && !__rule_lists_param(ctx->rule, cap->param_type)) {
        /* Present on the device but not part of the matched rule's shape: stays
         * cloud-only. Keeps e.g. ColorControl off device types whose Matter
         * definition forbids it. */
        OSAL_LOGI(TAG, "Param type %s not in the matched rule; cloud-only.", cap->param_type);
        return ESP_RMAKER_OK;
    }
    return __lower_capability(ctx->port, ctx->endpoint_id, param, cap);
}

esp_rmaker_error_t rm_mirror_lowering_lower_device(const rm_mirror_port_ops_t *port, const esp_rmaker_device_t *device)
{
    const char *device_type = esp_rmaker_device_get_type(device);
    if (!device_type) {
        OSAL_LOGI(TAG, "Device '%s' has no type; not mirrored.", esp_rmaker_device_get_id(device));
        return ESP_RMAKER_OK;
    }

    const rm_mirror_devtype_rule_t *rule = rm_mirror_table_match_rule(device_type, __device_has_param,
                                           (void *)device);
    if (!rule) {
        OSAL_LOGI(TAG, "No mapping rule for device '%s' (%s); not mirrored.",
                  esp_rmaker_device_get_id(device), device_type);
        return ESP_RMAKER_OK;
    }

    uint16_t endpoint_id = 0;
    esp_rmaker_error_t err = port->endpoint_create(rule->matter_device_type_id,
                             rule->matter_device_type_version, &endpoint_id);
    if (err != ESP_RMAKER_OK) {
        OSAL_LOGE(TAG, "Failed to create endpoint for device '%s'.", esp_rmaker_device_get_id(device));
        return err;
    }
    OSAL_LOGI(TAG, "Device '%s' lowered to endpoint %u (device type 0x%04X).",
              esp_rmaker_device_get_id(device), endpoint_id, (unsigned)rule->matter_device_type_id);

    for (size_t i = 0; i < rule->n_mandatory_clusters; i++) {
        err = port->cluster_create(endpoint_id, rule->mandatory_clusters[i], 0);
        if (err != ESP_RMAKER_OK) {
            return err;
        }
    }

    /* Clusters that belong to a device type of their own (electrical measurement to an Electrical
     * Sensor, say) only count as declared once the endpoint carries that device type too. */
    for (size_t i = 0; i < rule->n_extra_device_types; i++) {
        const rm_mirror_extra_devtype_t *extra = &rule->extra_device_types[i];
        if (!port->endpoint_device_type_add) {
            OSAL_LOGE(TAG, "Rule needs device type 0x%04X on the endpoint, which the port cannot add.",
                      (unsigned)extra->device_type_id);
            return ESP_RMAKER_NOT_SUPPORTED;
        }
        err = port->endpoint_device_type_add(endpoint_id, extra->device_type_id, extra->device_type_version);
        if (err != ESP_RMAKER_OK) {
            return err;
        }
        for (size_t c = 0; c < extra->n_mandatory_clusters; c++) {
            err = port->cluster_create(endpoint_id, extra->mandatory_clusters[c], 0);
            if (err != ESP_RMAKER_OK) {
                return err;
            }
        }
        OSAL_LOGI(TAG, "Endpoint %u also declares device type 0x%04X.", endpoint_id,
                  (unsigned)extra->device_type_id);
    }

    __lower_param_ctx_t ctx = { .port = port, .rule = rule, .endpoint_id = endpoint_id };
    err = esp_rmaker_device_for_each_param(device, __lower_param, &ctx);
    if (err != ESP_RMAKER_OK) {
        return err;
    }

#if RM_MIRROR_USES_COMPOSITES
    err = __lower_composites(port, endpoint_id, device, rule);
    if (err != ESP_RMAKER_OK) {
        return err;
    }
#endif
    return ESP_RMAKER_OK;
}

static esp_rmaker_error_t __lower_device(const esp_rmaker_device_t *device, void *priv)
{
    if (esp_rmaker_device_is_service(device)) {
        return ESP_RMAKER_OK;
    }
    return rm_mirror_lowering_lower_device((const rm_mirror_port_ops_t *)priv, device);
}

esp_rmaker_error_t rm_mirror_lowering_run(const esp_rmaker_node_t *node, const rm_mirror_port_ops_t *port)
{
    esp_rmaker_error_t err = esp_rmaker_node_for_each_device(node, __lower_device, (void *)port);
    if (err != ESP_RMAKER_OK) {
        return err;
    }
    __mark_lowered();
    return ESP_RMAKER_OK;
}
