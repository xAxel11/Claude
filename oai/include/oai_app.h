/* oai_app.h -- the object every part of the running program shares.
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#ifndef OAI_APP_H
#define OAI_APP_H

#include "oai.h"
#include "oai_train.h"
#include "oai_log.h"
#include "oai_gpu.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    oai_config     cfg;
    oai_trainer    trainer;
    oai_stream    *learning;    /* the scrolling learning feed */
    oai_stream    *chat;        /* the conversation */
    oai_gpu_info   gpu;
    oai_atomic_i32 quit;        /* set by Ctrl+C or "exit" */

    /* The interface publishes its current pane widths here every frame, and
     * the chat and the trainer wrap their output to match. Without this a
     * narrow terminal clips messages instead of wrapping them. */
    oai_atomic_i32 chat_width;
    oai_atomic_i32 feed_width;

    int            ready;       /* trainer initialised successfully */
} oai_app;

int  oai_app_init(oai_app *app, const oai_config *cfg);
void oai_app_free(oai_app *app);
/* Brings the compute backend up according to cfg.backend and cfg.gpu_budget,
 * logging what it settled on. */
void oai_app_setup_backend(oai_app *app);

#ifdef __cplusplus
}
#endif
#endif /* OAI_APP_H */
