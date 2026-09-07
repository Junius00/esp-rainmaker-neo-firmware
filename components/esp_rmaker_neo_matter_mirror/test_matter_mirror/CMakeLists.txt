# POSIX unit tests for the Matter mirror engine (fake port, no esp-matter).

set(RMNG_MATTER_MIRROR_TEST_NAME rmng_matter_mirror_tests)

add_executable(${RMNG_MATTER_MIRROR_TEST_NAME} test_mirror.c fake_port.c test_runner_posix.c)
target_include_directories(${RMNG_MATTER_MIRROR_TEST_NAME} PRIVATE ${CMAKE_CURRENT_LIST_DIR})
target_link_libraries(${RMNG_MATTER_MIRROR_TEST_NAME} PRIVATE esp_rmaker_neo_matter_mirror unity::framework)

add_test(NAME ${RMNG_MATTER_MIRROR_TEST_NAME} COMMAND ${RMNG_MATTER_MIRROR_TEST_NAME})
