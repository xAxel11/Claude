/* oai_train.h -- the training loop, its worker thread and its cancellation.
 *
 * Training runs on its own thread so the interface never blocks. The loop
 * checks a cancel flag between every batch, which means "stop" is honoured in
 * milliseconds, and the weights are checkpointed before the thread returns --
 * cancelling loses nothing but the batch in flight.
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#ifndef OAI_TRAIN_H
#define OAI_TRAIN_H

#include "oai.h"
#include "oai_net.h"
#include "oai_data.h"
#include "oai_log.h"
#include "oai_platform.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    OAI_TRAIN_IDLE = 0,
    OAI_TRAIN_RUNNING,
    OAI_TRAIN_PAUSED,
    OAI_TRAIN_STOPPING,
    OAI_TRAIN_FINISHED
} oai_train_state;

/* A snapshot of the run, copied under lock for the UI to render. */
typedef struct {
    oai_train_state state;
    long   step;
    long   max_steps;
    float  loss;            /* most recent batch */
    float  loss_avg;        /* exponential moving average */
    float  loss_best;
    float  val_loss;        /* -1 until the first validation pass */
    float  grad_norm;
    float  lr;
    double steps_per_sec;
    double elapsed;
    double chars_per_sec;
    long   chars_seen;
    int    clipped;         /* gradient clips so far */
    char   note[128];       /* what the trainer is doing right now */
} oai_train_stats;

typedef struct {
    oai_config   cfg;
    oai_net      net;
    oai_dataset  data;
    oai_stream  *learning;   /* the scrolling "what I am learning" feed */

    oai_thread     *worker;
    oai_mutex      *model_lock;   /* guards net during a step and while sampling */
    oai_mutex      *stats_lock;
    oai_atomic_i32  cancel;       /* set to 1 to stop; the loop polls it */
    oai_atomic_i32  pause;
    oai_atomic_i32  running;

    oai_train_stats stats;
    oai_rng         rng;
    /* Points at oai_app::feed_width, or NULL when nothing is publishing one.
     * Owned by the app; the trainer only reads it. */
    oai_atomic_i32 *feed_width;
    int             initialised;
} oai_trainer;

/* Loads the corpus, builds or restores the model and prepares the trainer. */
int  oai_trainer_init(oai_trainer *t, const oai_config *cfg,
                      oai_stream *learning);
void oai_trainer_free(oai_trainer *t);

/* Starts the worker thread. Returns 0 if training began. */
int  oai_trainer_start(oai_trainer *t);
/* Asks the worker to stop. Returns immediately; the loop notices within one
 * batch, checkpoints and exits. Call oai_trainer_wait to block for it. */
void oai_trainer_stop(oai_trainer *t);
void oai_trainer_wait(oai_trainer *t);
void oai_trainer_toggle_pause(oai_trainer *t);
int  oai_trainer_is_running(const oai_trainer *t);

void oai_trainer_get_stats(oai_trainer *t, oai_train_stats *out);
/* Adjusts the learning rate mid-run. */
void oai_trainer_set_lr(oai_trainer *t, float lr);

/* Generates text from the current weights; safe to call while training. */
int  oai_trainer_sample(oai_trainer *t, const char *seed, float temperature,
                        char *out, int max_len);
/* Writes "the model's current top guesses after <probe>" into out. */
void oai_trainer_describe_prediction(oai_trainer *t, const char *probe,
                                     char *out, size_t out_len);
int  oai_trainer_save(oai_trainer *t, const char *path);

#ifdef __cplusplus
}
#endif
#endif /* OAI_TRAIN_H */
