/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file test_factory_reset_participants.c
 * @brief Tests for factory reset participants.
 */

#include "unity.h"
#include "test_rmng_prototypes.h"

#include <stddef.h>
#include <string.h>

#include "esp_rmaker_system_ctrl.h"

/* Recording *******************************************************************/

#define WIPE_LOG_MAX 8

static const char *__wipe_log[WIPE_LOG_MAX];
static uint8_t __wipe_count;
static esp_rmaker_error_t __wipe_result[WIPE_LOG_MAX];

static esp_rmaker_error_t __record(const char *name)
{
    esp_rmaker_error_t err = ESP_RMAKER_OK;
    if (__wipe_count < WIPE_LOG_MAX) {
        __wipe_log[__wipe_count] = name;
        err = __wipe_result[__wipe_count];
        __wipe_count++;
    }
    return err;
}

static esp_rmaker_error_t __wipe_a(void *priv)
{
    (void) priv;
    return __record("a");
}

static esp_rmaker_error_t __wipe_b(void *priv)
{
    (void) priv;
    return __record("b");
}

static esp_rmaker_error_t __wipe_c(void *priv)
{
    (void) priv;
    return __record("c");
}

static esp_rmaker_error_t __wipe_d(void *priv)
{
    (void) priv;
    return __record("d");
}

static esp_rmaker_error_t __wipe_e(void *priv)
{
    (void) priv;
    return __record("e");
}

static esp_rmaker_error_t __fake_network_reset(void)
{
    return ESP_RMAKER_OK;
}

static esp_rmaker_system_ctrl_factory_reset_wipe_fn_t __all_wipes[] = { __wipe_a, __wipe_b, __wipe_c, __wipe_d, __wipe_e };

static void __setup(void)
{
    memset(__wipe_log, 0, sizeof(__wipe_log));
    memset(__wipe_result, 0, sizeof(__wipe_result));
    __wipe_count = 0;
}

static void __teardown(void)
{
    for (size_t i = 0; i < sizeof(__all_wipes) / sizeof(__all_wipes[0]); i++) {
        (void) esp_rmaker_system_ctrl_factory_reset_participant_unregister(__all_wipes[i]);
    }
}

static esp_rmaker_system_ctrl_factory_reset_participant_t __participant(const char *name, esp_rmaker_system_ctrl_factory_reset_wipe_fn_t wipe,
        bool reboots_on_wipe)
{
    esp_rmaker_system_ctrl_factory_reset_participant_t participant = {
        .name = name,
        .reboots_on_wipe = reboots_on_wipe,
        .wipe = wipe,
        .priv = NULL,
    };
    return participant;
}

/* reset_reboot_s = -1 keeps the reboot out of the test: on POSIX it would exit the process. */
static esp_rmaker_error_t __factory_reset(void)
{
    return esp_rmaker_system_ctrl_factory_reset(0, -1, __fake_network_reset);
}

/* Registration ****************************************************************/

void test_factory_reset_participant_register_arg_validation(void)
{
    __setup();

    TEST_ASSERT_EQUAL(ESP_RMAKER_INVALID_ARG, esp_rmaker_system_ctrl_factory_reset_participant_register(NULL));

    esp_rmaker_system_ctrl_factory_reset_participant_t no_wipe = __participant("no_wipe", NULL, false);
    TEST_ASSERT_EQUAL(ESP_RMAKER_INVALID_ARG, esp_rmaker_system_ctrl_factory_reset_participant_register(&no_wipe));

    TEST_ASSERT_EQUAL(ESP_RMAKER_INVALID_ARG, esp_rmaker_system_ctrl_factory_reset_participant_unregister(NULL));
    TEST_ASSERT_EQUAL(ESP_RMAKER_NOT_FOUND, esp_rmaker_system_ctrl_factory_reset_participant_unregister(__wipe_a));

    __teardown();
}

void test_factory_reset_participant_register_rejects_second_rebooting(void)
{
    __setup();

    esp_rmaker_system_ctrl_factory_reset_participant_t first = __participant("first", __wipe_a, true);
    esp_rmaker_system_ctrl_factory_reset_participant_t second = __participant("second", __wipe_b, true);
    TEST_ASSERT_EQUAL(ESP_RMAKER_OK, esp_rmaker_system_ctrl_factory_reset_participant_register(&first));
    TEST_ASSERT_EQUAL(ESP_RMAKER_INVALID_STATE, esp_rmaker_system_ctrl_factory_reset_participant_register(&second));

    /* The rejected one must not have been stored. */
    TEST_ASSERT_EQUAL(ESP_RMAKER_NOT_FOUND, esp_rmaker_system_ctrl_factory_reset_participant_unregister(__wipe_b));

    __teardown();
}

void test_factory_reset_participant_register_table_full(void)
{
    __setup();

    /* SYSTEM_CTRL_FACTORY_RESET_PARTICIPANT_MAX_COUNT is 4. */
    esp_rmaker_system_ctrl_factory_reset_participant_t p;
    for (size_t i = 0; i < 4; i++) {
        p = __participant("p", __all_wipes[i], false);
        TEST_ASSERT_EQUAL(ESP_RMAKER_OK, esp_rmaker_system_ctrl_factory_reset_participant_register(&p));
    }
    p = __participant("overflow", __wipe_e, false);
    TEST_ASSERT_EQUAL(ESP_RMAKER_NO_MEM, esp_rmaker_system_ctrl_factory_reset_participant_register(&p));

    __teardown();
}

void test_factory_reset_participant_reregister_updates_in_place(void)
{
    __setup();

    esp_rmaker_system_ctrl_factory_reset_participant_t p = __participant("a", __wipe_a, false);
    TEST_ASSERT_EQUAL(ESP_RMAKER_OK, esp_rmaker_system_ctrl_factory_reset_participant_register(&p));
    TEST_ASSERT_EQUAL(ESP_RMAKER_OK, esp_rmaker_system_ctrl_factory_reset_participant_register(&p));

    (void) __factory_reset();
    TEST_ASSERT_EQUAL_UINT8(1, __wipe_count);

    /* A single entry, so a single unregister clears it. */
    TEST_ASSERT_EQUAL(ESP_RMAKER_OK, esp_rmaker_system_ctrl_factory_reset_participant_unregister(__wipe_a));
    TEST_ASSERT_EQUAL(ESP_RMAKER_NOT_FOUND, esp_rmaker_system_ctrl_factory_reset_participant_unregister(__wipe_a));

    __teardown();
}

/* Wipe sequencing *************************************************************/

void test_factory_reset_participants_wiped_in_registration_order(void)
{
    __setup();

    esp_rmaker_system_ctrl_factory_reset_participant_t a = __participant("a", __wipe_a, false);
    esp_rmaker_system_ctrl_factory_reset_participant_t b = __participant("b", __wipe_b, false);
    esp_rmaker_system_ctrl_factory_reset_participant_t c = __participant("c", __wipe_c, false);
    TEST_ASSERT_EQUAL(ESP_RMAKER_OK, esp_rmaker_system_ctrl_factory_reset_participant_register(&a));
    TEST_ASSERT_EQUAL(ESP_RMAKER_OK, esp_rmaker_system_ctrl_factory_reset_participant_register(&b));
    TEST_ASSERT_EQUAL(ESP_RMAKER_OK, esp_rmaker_system_ctrl_factory_reset_participant_register(&c));

    (void) __factory_reset();

    TEST_ASSERT_EQUAL_UINT8(3, __wipe_count);
    TEST_ASSERT_EQUAL_STRING("a", __wipe_log[0]);
    TEST_ASSERT_EQUAL_STRING("b", __wipe_log[1]);
    TEST_ASSERT_EQUAL_STRING("c", __wipe_log[2]);

    __teardown();
}

void test_factory_reset_participants_rebooting_wiped_last(void)
{
    __setup();

    /* Registered first, so only the reboots_on_wipe handling can move it to the end. */
    esp_rmaker_system_ctrl_factory_reset_participant_t rebooting = __participant("rebooting", __wipe_a, true);
    esp_rmaker_system_ctrl_factory_reset_participant_t b = __participant("b", __wipe_b, false);
    esp_rmaker_system_ctrl_factory_reset_participant_t c = __participant("c", __wipe_c, false);
    TEST_ASSERT_EQUAL(ESP_RMAKER_OK, esp_rmaker_system_ctrl_factory_reset_participant_register(&rebooting));
    TEST_ASSERT_EQUAL(ESP_RMAKER_OK, esp_rmaker_system_ctrl_factory_reset_participant_register(&b));
    TEST_ASSERT_EQUAL(ESP_RMAKER_OK, esp_rmaker_system_ctrl_factory_reset_participant_register(&c));

    (void) __factory_reset();

    TEST_ASSERT_EQUAL_UINT8(3, __wipe_count);
    TEST_ASSERT_EQUAL_STRING("b", __wipe_log[0]);
    TEST_ASSERT_EQUAL_STRING("c", __wipe_log[1]);
    TEST_ASSERT_EQUAL_STRING("a", __wipe_log[2]);

    __teardown();
}

void test_factory_reset_participants_wipe_failure_does_not_stop_others(void)
{
    __setup();

    esp_rmaker_system_ctrl_factory_reset_participant_t a = __participant("a", __wipe_a, false);
    esp_rmaker_system_ctrl_factory_reset_participant_t b = __participant("b", __wipe_b, false);
    esp_rmaker_system_ctrl_factory_reset_participant_t c = __participant("c", __wipe_c, false);
    TEST_ASSERT_EQUAL(ESP_RMAKER_OK, esp_rmaker_system_ctrl_factory_reset_participant_register(&a));
    TEST_ASSERT_EQUAL(ESP_RMAKER_OK, esp_rmaker_system_ctrl_factory_reset_participant_register(&b));
    TEST_ASSERT_EQUAL(ESP_RMAKER_OK, esp_rmaker_system_ctrl_factory_reset_participant_register(&c));

    __wipe_result[1] = ESP_RMAKER_FAIL;
    (void) __factory_reset();

    TEST_ASSERT_EQUAL_UINT8(3, __wipe_count);
    TEST_ASSERT_EQUAL_STRING("c", __wipe_log[2]);

    __teardown();
}

void test_factory_reset_from_participant_skips_only_the_originator(void)
{
    __setup();

    esp_rmaker_system_ctrl_factory_reset_participant_t a = __participant("a", __wipe_a, false);
    esp_rmaker_system_ctrl_factory_reset_participant_t b = __participant("b", __wipe_b, false);
    esp_rmaker_system_ctrl_factory_reset_participant_t c = __participant("c", __wipe_c, false);
    TEST_ASSERT_EQUAL(ESP_RMAKER_OK, esp_rmaker_system_ctrl_factory_reset_participant_register(&a));
    TEST_ASSERT_EQUAL(ESP_RMAKER_OK, esp_rmaker_system_ctrl_factory_reset_participant_register(&b));
    TEST_ASSERT_EQUAL(ESP_RMAKER_OK, esp_rmaker_system_ctrl_factory_reset_participant_register(&c));

    /* 'b' started the reset, so it is already wiping its own state; the other two must still run. */
    (void) esp_rmaker_system_ctrl_factory_reset_from_participant(__wipe_b, __fake_network_reset, -1);

    TEST_ASSERT_EQUAL_UINT8(2, __wipe_count);
    TEST_ASSERT_EQUAL_STRING("a", __wipe_log[0]);
    TEST_ASSERT_EQUAL_STRING("c", __wipe_log[1]);

    __teardown();
}

void test_factory_reset_from_participant_null_self_wipes_all(void)
{
    __setup();

    esp_rmaker_system_ctrl_factory_reset_participant_t a = __participant("a", __wipe_a, false);
    esp_rmaker_system_ctrl_factory_reset_participant_t b = __participant("b", __wipe_b, false);
    TEST_ASSERT_EQUAL(ESP_RMAKER_OK, esp_rmaker_system_ctrl_factory_reset_participant_register(&a));
    TEST_ASSERT_EQUAL(ESP_RMAKER_OK, esp_rmaker_system_ctrl_factory_reset_participant_register(&b));

    (void) esp_rmaker_system_ctrl_factory_reset_from_participant(NULL, __fake_network_reset, -1);

    TEST_ASSERT_EQUAL_UINT8(2, __wipe_count);

    __teardown();
}

/* Re-entry ********************************************************************/

/* Stands in for a participant whose own wipe restarts the external stack, which reports the reset
 * back to the SDK before this call returns. */
static esp_rmaker_error_t __wipe_reentrant(void *priv)
{
    (void) priv;
    esp_rmaker_error_t err = __record("reentrant");
    (void) esp_rmaker_system_ctrl_factory_reset_from_participant(__wipe_reentrant, __fake_network_reset, -1);
    return err;
}

void test_factory_reset_reentrant_request_is_a_noop(void)
{
    __setup();

    esp_rmaker_system_ctrl_factory_reset_participant_t reentrant = __participant("reentrant", __wipe_reentrant, false);
    esp_rmaker_system_ctrl_factory_reset_participant_t b = __participant("b", __wipe_b, false);
    TEST_ASSERT_EQUAL(ESP_RMAKER_OK, esp_rmaker_system_ctrl_factory_reset_participant_register(&reentrant));
    TEST_ASSERT_EQUAL(ESP_RMAKER_OK, esp_rmaker_system_ctrl_factory_reset_participant_register(&b));

    (void) __factory_reset();

    /* One wipe each: the nested request must not restart the sweep. */
    TEST_ASSERT_EQUAL_UINT8(2, __wipe_count);
    TEST_ASSERT_EQUAL_STRING("reentrant", __wipe_log[0]);
    TEST_ASSERT_EQUAL_STRING("b", __wipe_log[1]);

    (void) esp_rmaker_system_ctrl_factory_reset_participant_unregister(__wipe_reentrant);
    __teardown();
}

void test_factory_reset_participants_unregister_preserves_order(void)
{
    __setup();

    esp_rmaker_system_ctrl_factory_reset_participant_t a = __participant("a", __wipe_a, false);
    esp_rmaker_system_ctrl_factory_reset_participant_t b = __participant("b", __wipe_b, false);
    esp_rmaker_system_ctrl_factory_reset_participant_t c = __participant("c", __wipe_c, false);
    TEST_ASSERT_EQUAL(ESP_RMAKER_OK, esp_rmaker_system_ctrl_factory_reset_participant_register(&a));
    TEST_ASSERT_EQUAL(ESP_RMAKER_OK, esp_rmaker_system_ctrl_factory_reset_participant_register(&b));
    TEST_ASSERT_EQUAL(ESP_RMAKER_OK, esp_rmaker_system_ctrl_factory_reset_participant_register(&c));
    TEST_ASSERT_EQUAL(ESP_RMAKER_OK, esp_rmaker_system_ctrl_factory_reset_participant_unregister(__wipe_b));

    (void) __factory_reset();

    TEST_ASSERT_EQUAL_UINT8(2, __wipe_count);
    TEST_ASSERT_EQUAL_STRING("a", __wipe_log[0]);
    TEST_ASSERT_EQUAL_STRING("c", __wipe_log[1]);

    __teardown();
}
