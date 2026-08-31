/* oai_train.c -- the training loop and everything it reports.
 *
 * The loop is deliberately plain:
 *
 *     while (not cancelled and steps remain)
 *         draw a batch -> forward -> backward -> clip -> Adam -> report
 *
 * Cancellation is a single atomic flag polled at the top of each iteration, so
 * the worst-case latency of a stop is one batch (a few milliseconds at the
 * default sizes). On the way out the loop always writes a checkpoint.
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#include "oai_train.h"
#include "oai_gpu.h"

#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* How often the loop reports, in steps. Report cadence is decoupled from the
 * sample cadence so the feed stays readable at any speed. */
#define OAI_REPORT_EVERY 20
#define OAI_VALIDATE_EVERY 200

/* ============================================================ construction */

int oai_trainer_init(oai_trainer *t, const oai_config *cfg, oai_stream *learning)
{
    int loaded = 0;

    memset(t, 0, sizeof *t);
    t->cfg      = *cfg;
    t->learning = learning;

    if (oai_dataset_load(&t->data, cfg->corpus_path, 0.1f) != 0) {
        oai_stream_push(learning, OAI_LINE_WARN, "could not load a corpus");
        return -1;
    }

    oai_stream_push(learning, OAI_LINE_INFO,
                    "corpus: %s  (%lu characters, %d distinct symbols)",
                    t->data.source, (unsigned long)t->data.len,
                    t->data.vocab.size);

    /* Resume from a checkpoint when one exists and matches this corpus. */
    if (cfg->checkpoint_path[0] && oai_file_exists(cfg->checkpoint_path)) {
        if (oai_net_load(&t->net, cfg->checkpoint_path) == 0) {
            if (t->net.vocab_size == t->data.vocab.size) {
                loaded = 1;
                oai_stream_push(learning, OAI_LINE_EVENT,
                    "resumed from %s at optimiser step %ld",
                    cfg->checkpoint_path, t->net.adam_step);
            } else {
                oai_net_free(&t->net);
                oai_stream_push(learning, OAI_LINE_WARN,
                    "checkpoint vocabulary does not match this corpus; "
                    "starting fresh");
            }
        }
    }

    if (!loaded) {
        if (oai_net_init(&t->net, &t->data.vocab, cfg->context, cfg->embed_dim,
                         cfg->hidden, (unsigned int)cfg->seed) != 0) {
            oai_stream_push(learning, OAI_LINE_WARN,
                            "could not allocate the model");
            oai_dataset_free(&t->data);
            return -1;
        }
    }

    t->model_lock = oai_mutex_new();
    t->stats_lock = oai_mutex_new();
    if (!t->model_lock || !t->stats_lock) {
        oai_trainer_free(t);
        return -1;
    }

    oai_rng_seed(&t->rng, (unsigned int)cfg->seed + 12345u);

    t->stats.state     = OAI_TRAIN_IDLE;
    t->stats.step      = 0;
    t->stats.max_steps = cfg->max_steps;
    t->stats.loss      = 0.0f;
    t->stats.loss_avg  = 0.0f;
    t->stats.loss_best = 1e9f;
    t->stats.val_loss  = -1.0f;
    t->stats.lr        = cfg->learning_rate;
    snprintf(t->stats.note, sizeof t->stats.note, "ready");

    oai_stream_push(learning, OAI_LINE_INFO,
        "model: context %d, embed %d, hidden %d, %lu parameters",
        t->net.context, t->net.embed_dim, t->net.hidden,
        (unsigned long)oai_net_param_count(&t->net));

    t->initialised = 1;
    return 0;
}

void oai_trainer_free(oai_trainer *t)
{
    if (!t) return;
    oai_trainer_stop(t);
    oai_trainer_wait(t);
    oai_net_free(&t->net);
    oai_dataset_free(&t->data);
    oai_mutex_free(t->model_lock);
    oai_mutex_free(t->stats_lock);
    t->model_lock = t->stats_lock = NULL;
    t->initialised = 0;
}

/* ================================================================== stats */

static void set_state(oai_trainer *t, oai_train_state s, const char *note)
{
    oai_mutex_lock(t->stats_lock);
    t->stats.state = s;
    if (note) snprintf(t->stats.note, sizeof t->stats.note, "%s", note);
    oai_mutex_unlock(t->stats_lock);
}

void oai_trainer_get_stats(oai_trainer *t, oai_train_stats *out)
{
    oai_mutex_lock(t->stats_lock);
    *out = t->stats;
    oai_mutex_unlock(t->stats_lock);
}

void oai_trainer_set_lr(oai_trainer *t, float lr)
{
    if (lr < 1e-6f) lr = 1e-6f;
    if (lr > 1.0f)  lr = 1.0f;
    oai_mutex_lock(t->stats_lock);
    t->cfg.learning_rate = lr;
    t->stats.lr = lr;
    oai_mutex_unlock(t->stats_lock);
}

int oai_trainer_is_running(const oai_trainer *t)
{
    return oai_atomic_load((oai_atomic_i32 *)&t->running) != 0;
}

/* Appends to a fixed buffer, returning the new length.
 *
 * `used += snprintf(buf + used, cap - used, ...)` looks right and is not:
 * snprintf returns what it *would* have written, so on truncation `used` runs
 * past `cap`, the next `buf + used` is out of bounds and `cap - used`
 * underflows to a huge size_t. This clamps instead. */
static size_t str_append(char *buf, size_t cap, size_t used, const char *fmt, ...)
{
    va_list ap;
    int n;

    if (cap == 0 || used >= cap - 1) return used;
    va_start(ap, fmt);
    n = vsnprintf(buf + used, cap - used, fmt, ap);
    va_end(ap);
    if (n < 0) return used;
    used += (size_t)n;
    if (used >= cap) used = cap - 1;   /* it truncated; stay inside the buffer */
    return used;
}

/* =============================================================== sampling */

int oai_trainer_sample(oai_trainer *t, const char *seed, float temperature,
                       char *out, int max_len)
{
    int n;
    if (!t->initialised) { if (max_len) out[0] = '\0'; return 0; }
    oai_mutex_lock(t->model_lock);
    n = oai_net_sample(&t->net, &t->rng, seed, temperature, out, max_len);
    oai_mutex_unlock(t->model_lock);
    return n;
}

/* Renders the top three characters the model expects after `probe`, which is
 * the most direct answer to "what have you learned so far". */
void oai_trainer_describe_prediction(oai_trainer *t, const char *probe,
                                     char *out, size_t out_len)
{
    float *probs;
    int   *ctx;
    int    i, j, top[3];
    float  topp[3];
    char   sym[16];
    size_t used = 0;
    int    space_id;

    if (!t->initialised) { snprintf(out, out_len, "(no model)"); return; }

    probs = (float *)calloc((size_t)t->net.vocab_size, sizeof(float));
    ctx   = (int *)calloc((size_t)t->net.context, sizeof(int));
    if (!probs || !ctx) {
        free(probs);
        free(ctx);
        if (out_len) out[0] = '\0';
        return;
    }

    oai_mutex_lock(t->model_lock);
    space_id = oai_vocab_id(&t->net.vocab, (unsigned char)' ');
    if (space_id < 0) space_id = 0;
    for (i = 0; i < t->net.context; ++i) ctx[i] = space_id;
    if (probe) {
        size_t plen = strlen(probe), k;
        for (k = 0; k < plen; ++k) {
            int id = oai_vocab_id(&t->net.vocab, (unsigned char)probe[k]);
            if (id < 0) continue;
            memmove(ctx, ctx + 1, (size_t)(t->net.context - 1) * sizeof(int));
            ctx[t->net.context - 1] = id;
        }
    }
    oai_net_next_dist(&t->net, ctx, probs);
    oai_mutex_unlock(t->model_lock);

    for (j = 0; j < 3; ++j) { top[j] = 0; topp[j] = -1.0f; }
    for (i = 0; i < t->net.vocab_size; ++i) {
        for (j = 0; j < 3; ++j) {
            if (probs[i] > topp[j]) {
                int  k;
                for (k = 2; k > j; --k) { topp[k] = topp[k-1]; top[k] = top[k-1]; }
                topp[j] = probs[i];
                top[j]  = i;
                break;
            }
        }
    }

    /* The probe is shown back to the user, and it may contain a newline or a
     * tab -- both of which would split the line in the feed. Escape it. */
    {
        char shown[64];
        size_t k, w = 0;
        const char *p2 = probe ? probe : "";
        for (k = 0; p2[k] && w + 3 < sizeof shown; ++k) {
            unsigned char ch = (unsigned char)p2[k];
            if (ch == '\n')      { shown[w++] = '\\'; shown[w++] = 'n'; }
            else if (ch == '\t') { shown[w++] = '\\'; shown[w++] = 't'; }
            else if (ch < 32)    { shown[w++] = '.'; }
            else                 { shown[w++] = (char)ch; }
        }
        shown[w] = '\0';
        used = str_append(out, out_len, 0, "after \"%s\" -> ", shown);
    }
    for (j = 0; j < 3 && used + 1 < out_len; ++j) {
        oai_vocab_describe(&t->net.vocab, top[j], sym, sizeof sym);
        used = str_append(out, out_len, used, "%s%s %.0f%%",
                          j ? ", " : "", sym, topp[j] * 100.0f);
    }
    free(probs);
    free(ctx);
}

int oai_trainer_save(oai_trainer *t, const char *path)
{
    int rc;
    if (!t->initialised) return -1;
    oai_mutex_lock(t->model_lock);
    rc = oai_net_save(&t->net, path);
    oai_mutex_unlock(t->model_lock);
    return rc;
}

/* ============================================================ the loop */

/* One held-out pass over a handful of validation batches. */
static float validate(oai_trainer *t, oai_batch *vb)
{
    float total = 0.0f;
    int   i, passes = 4;
    for (i = 0; i < passes; ++i) {
        oai_dataset_batch(&t->data, &t->rng, vb->batch, t->net.context, 1,
                          vb->ctx, vb->y);
        total += oai_net_forward(&t->net, vb);
    }
    return total / (float)passes;
}

/* The width the learning feed should wrap to, as published by the interface. */
static int feed_width(const oai_trainer *t)
{
    int w = t->feed_width ? oai_atomic_load(t->feed_width) : 0;
    if (w < 24) w = 78;          /* headless, or the UI has not drawn yet */
    if (w > OAI_LINE_MAX - 1) w = OAI_LINE_MAX - 1;
    return w;
}

static void report_sample(oai_trainer *t, long step)
{
    char buf[600];
    char insight[192];
    int  n = t->cfg.sample_len;

    if (n > (int)sizeof buf - 1) n = (int)sizeof buf - 1;

    oai_mutex_lock(t->model_lock);
    oai_net_sample(&t->net, &t->rng, "the ", t->cfg.temperature, buf, n + 1);
    oai_mutex_unlock(t->model_lock);

    oai_stream_push(t->learning, OAI_LINE_EVENT,
                    "--- step %ld: this is what it writes now ---", step);
    oai_stream_push_wrapped(t->learning, OAI_LINE_SAMPLE, buf, feed_width(t));

    oai_trainer_describe_prediction(t, "the ", insight, sizeof insight);
    oai_stream_push(t->learning, OAI_LINE_INSIGHT, "%s", insight);
    oai_trainer_describe_prediction(t, "learn", insight, sizeof insight);
    oai_stream_push(t->learning, OAI_LINE_INSIGHT, "%s", insight);
}

static void train_worker(void *arg)
{
    oai_trainer *t = (oai_trainer *)arg;
    oai_batch    b, vb;
    double       t_start, t_last_report;
    long         steps_since_report = 0;
    long         step = 0;
    int          have_val;

    if (oai_batch_init(&b, &t->net, t->cfg.batch_size) != 0) {
        oai_stream_push(t->learning, OAI_LINE_WARN,
                        "out of memory allocating the batch");
        oai_atomic_store(&t->running, 0);
        set_state(t, OAI_TRAIN_FINISHED, "out of memory");
        return;
    }
    have_val = (t->data.val_len > (size_t)(t->net.context + 2))
               && oai_batch_init(&vb, &t->net, t->cfg.batch_size) == 0;

    oai_mutex_lock(t->stats_lock);
    step = t->stats.step;
    oai_mutex_unlock(t->stats_lock);

    t_start = oai_time_now();
    t_last_report = t_start;

    set_state(t, OAI_TRAIN_RUNNING, "training");
    oai_stream_push(t->learning, OAI_LINE_EVENT,
        "training started -- press [s] or type \"stop\" to cancel at any time");

    for (;;) {
        float loss, gnorm, lr;
        int   clipped = 0;

        /* --- the cancellation point ------------------------------------ */
        if (oai_atomic_load(&t->cancel)) break;

        if (oai_atomic_load(&t->pause)) {
            set_state(t, OAI_TRAIN_PAUSED, "paused");
            oai_sleep_ms(40.0);
            continue;
        }
        if (t->cfg.max_steps > 0 && step >= t->cfg.max_steps) break;

        set_state(t, OAI_TRAIN_RUNNING, "training");

        oai_mutex_lock(t->stats_lock);
        lr = t->cfg.learning_rate;
        oai_mutex_unlock(t->stats_lock);

        oai_dataset_batch(&t->data, &t->rng, b.batch, t->net.context, 0,
                          b.ctx, b.y);

        oai_mutex_lock(t->model_lock);
        loss  = oai_net_forward(&t->net, &b);
        oai_net_backward(&t->net, &b);
        gnorm = oai_net_grad_norm(&t->net);
        if (t->cfg.grad_clip > 0.0f && gnorm > t->cfg.grad_clip) {
            oai_net_scale_grads(&t->net, t->cfg.grad_clip / (gnorm + 1e-6f));
            clipped = 1;
        }
        oai_net_adam_step(&t->net, lr, t->cfg.weight_decay);
        oai_mutex_unlock(t->model_lock);

        step++;
        steps_since_report++;

        oai_mutex_lock(t->stats_lock);
        t->stats.step      = step;
        t->stats.loss      = loss;
        t->stats.grad_norm = gnorm;
        t->stats.lr        = lr;
        t->stats.loss_avg  = (t->stats.loss_avg == 0.0f)
                           ? loss
                           : 0.98f * t->stats.loss_avg + 0.02f * loss;
        if (t->stats.loss_avg < t->stats.loss_best)
            t->stats.loss_best = t->stats.loss_avg;
        t->stats.elapsed    = oai_time_now() - t_start;
        t->stats.chars_seen += (long)b.batch;
        if (clipped) t->stats.clipped++;
        oai_mutex_unlock(t->stats_lock);

        /* --- periodic reporting ---------------------------------------- */
        if (step % OAI_REPORT_EVERY == 0) {
            double now = oai_time_now();
            double dt  = now - t_last_report;
            double sps = dt > 0.0 ? (double)steps_since_report / dt : 0.0;
            float  avg, best;

            oai_mutex_lock(t->stats_lock);
            t->stats.steps_per_sec = sps;
            t->stats.chars_per_sec = sps * (double)b.batch;
            avg  = t->stats.loss_avg;
            best = t->stats.loss_best;
            oai_mutex_unlock(t->stats_lock);

            /* Kept short on purpose: this line is the most frequent thing in
             * the feed and it has to fit the pane without being clipped. */
            oai_stream_push(t->learning, OAI_LINE_STEP,
                "step %-6ld loss %.3f  avg %.3f  best %.3f  |g| %.2f  %.0f/s",
                step, loss, avg, best, gnorm, sps);

            steps_since_report = 0;
            t_last_report = now;
        }

        if (have_val && step % OAI_VALIDATE_EVERY == 0) {
            float v;
            oai_mutex_lock(t->model_lock);
            v = validate(t, &vb);
            oai_mutex_unlock(t->model_lock);
            oai_mutex_lock(t->stats_lock);
            t->stats.val_loss = v;
            oai_mutex_unlock(t->stats_lock);
            oai_stream_push(t->learning, OAI_LINE_INSIGHT,
                "held-out loss %.4f  (perplexity %.1f)", v, expf(v));
        }

        if (t->cfg.sample_every > 0 && step % t->cfg.sample_every == 0)
            report_sample(t, step);

        /* Checkpoint periodically so a hard kill still leaves progress. */
        if (step % 500 == 0 && t->cfg.checkpoint_path[0]) {
            if (oai_trainer_save(t, t->cfg.checkpoint_path) == 0)
                oai_stream_push(t->learning, OAI_LINE_EVENT,
                                "checkpoint written to %s",
                                t->cfg.checkpoint_path);
        }
    }

    /* --- unwinding: always leave a usable checkpoint behind ------------- */
    set_state(t, OAI_TRAIN_STOPPING, "saving checkpoint");
    if (t->cfg.checkpoint_path[0]) {
        if (oai_trainer_save(t, t->cfg.checkpoint_path) == 0)
            oai_stream_push(t->learning, OAI_LINE_EVENT,
                            "checkpoint written to %s at step %ld",
                            t->cfg.checkpoint_path, step);
        else
            oai_stream_push(t->learning, OAI_LINE_WARN,
                            "could not write %s", t->cfg.checkpoint_path);
    }

    {
        float avg;
        oai_mutex_lock(t->stats_lock);
        avg = t->stats.loss_avg;
        oai_mutex_unlock(t->stats_lock);
        oai_stream_push(t->learning, OAI_LINE_EVENT,
            "training stopped at step %ld with average loss %.4f "
            "(perplexity %.1f)", step, avg, expf(avg));
    }

    oai_batch_free(&b);
    if (have_val) oai_batch_free(&vb);

    oai_atomic_store(&t->running, 0);
    set_state(t, OAI_TRAIN_FINISHED, "stopped");
}

int oai_trainer_start(oai_trainer *t)
{
    if (!t->initialised) return -1;
    if (oai_atomic_load(&t->running)) return -1;

    oai_atomic_store(&t->cancel, 0);
    oai_atomic_store(&t->pause, 0);
    oai_atomic_store(&t->running, 1);

    t->worker = oai_thread_start(train_worker, t);
    if (!t->worker) {
        oai_atomic_store(&t->running, 0);
        set_state(t, OAI_TRAIN_IDLE, "could not start the worker thread");
        return -1;
    }
    return 0;
}

void oai_trainer_stop(oai_trainer *t)
{
    if (!t) return;
    oai_atomic_store(&t->cancel, 1);
    oai_atomic_store(&t->pause, 0);   /* a paused loop must wake to notice */
}

void oai_trainer_wait(oai_trainer *t)
{
    if (!t || !t->worker) return;
    oai_thread_join(t->worker);
    t->worker = NULL;
}

void oai_trainer_toggle_pause(oai_trainer *t)
{
    if (!t) return;
    oai_atomic_store(&t->pause, oai_atomic_load(&t->pause) ? 0 : 1);
}
