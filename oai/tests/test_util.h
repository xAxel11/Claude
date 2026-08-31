/* test_util.h -- the whole test harness. No framework, no dependencies.
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#ifndef OAI_TEST_UTIL_H
#define OAI_TEST_UTIL_H

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int oai_test_failures = 0;
static int oai_test_checks   = 0;

#define CHECK(cond, ...)                                                      \
    do {                                                                      \
        oai_test_checks++;                                                    \
        if (!(cond)) {                                                        \
            oai_test_failures++;                                              \
            printf("  FAIL %s:%d: ", __FILE__, __LINE__);                     \
            printf(__VA_ARGS__);                                              \
            printf("\n");                                                     \
        }                                                                     \
    } while (0)

#define CHECK_NEAR(a, b, tol, ...)                                            \
    do {                                                                      \
        double va_ = (double)(a), vb_ = (double)(b);                          \
        oai_test_checks++;                                                    \
        if (fabs(va_ - vb_) > (tol)) {                                        \
            oai_test_failures++;                                              \
            printf("  FAIL %s:%d: %g vs %g (tolerance %g): ",                 \
                   __FILE__, __LINE__, va_, vb_, (double)(tol));              \
            printf(__VA_ARGS__);                                              \
            printf("\n");                                                     \
        }                                                                     \
    } while (0)

#define TEST(name) printf("  %s\n", name)

#define TEST_MAIN_END()                                                       \
    do {                                                                      \
        printf("%s: %d checks, %d failures\n",                                \
               oai_test_failures ? "FAILED" : "ok",                           \
               oai_test_checks, oai_test_failures);                           \
        return oai_test_failures ? 1 : 0;                                     \
    } while (0)

#endif /* OAI_TEST_UTIL_H */
