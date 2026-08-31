/* test_train.c -- the trainer's lifecycle: it starts, it learns, it stops
 * promptly when cancelled, and it leaves a checkpoint behind.
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#include "oai_train.h"
#include "oai_pool.h"
#include "test_util.h"

static void write_corpus(const char *path)
{
    FILE *f = fopen(path, "wb");
    int i;
    for (i = 0; i < 400; ++i)
        fputs("the model reads a window of characters and guesses the next "
              "one. every step it is a little less wrong than before.\n", f);
    fclose(f);
}

/* Blocks until `pred` holds or the deadline passes. Returns seconds waited. */
static double wait_until(oai_trainer *t, int (*pred)(oai_trainer *),
                         double timeout)
{
    double start = oai_time_now();
    while (!pred(t) && oai_time_now() - start < timeout)
        oai_sleep_ms(2.0);
    return oai_time_now() - start;
}

static int has_stopped(oai_trainer *t) { return !oai_trainer_is_running(t); }
static int has_progressed(oai_trainer *t)
{
    oai_train_stats st;
    oai_trainer_get_stats(t, &st);
    return st.step >= 30;
}

int main(void)
{
    oai_config   cfg;
    oai_trainer  t;
    oai_stream  *feed;
    const char  *corpus = "test-train-corpus.tmp";
    const char  *ckpt   = "test-train-ckpt.tmp";

    printf("test_train\n");
    oai_pool_init(2);
    write_corpus(corpus);

    oai_config_defaults(&cfg);
    snprintf(cfg.corpus_path, sizeof cfg.corpus_path, "%s", corpus);
    snprintf(cfg.checkpoint_path, sizeof cfg.checkpoint_path, "%s", ckpt);
    cfg.hidden = 64;
    cfg.embed_dim = 8;
    cfg.context = 6;
    cfg.batch_size = 16;
    cfg.sample_every = 0;
    cfg.max_steps = 0;         /* run until we cancel it */
    oai_config_validate(&cfg);

    feed = oai_stream_new(256);
    CHECK(oai_trainer_init(&t, &cfg, feed) == 0, "trainer init");

    TEST("a fresh trainer is idle");
    {
        oai_train_stats st;
        oai_trainer_get_stats(&t, &st);
        CHECK(st.state == OAI_TRAIN_IDLE, "state %d", (int)st.state);
        CHECK(st.step == 0, "step %ld", st.step);
        CHECK(!oai_trainer_is_running(&t), "should not be running");
    }

    TEST("it can be sampled before it has learned anything");
    {
        char out[64];
        int n = oai_trainer_sample(&t, "the ", 0.9f, out, (int)sizeof out);
        CHECK(n > 0, "sample returned %d characters", n);
    }

    TEST("starting the worker makes progress");
    CHECK(oai_trainer_start(&t) == 0, "start");
    CHECK(oai_trainer_start(&t) != 0, "a second start should be refused");
    {
        double waited = wait_until(&t, has_progressed, 10.0);
        oai_train_stats st;
        oai_trainer_get_stats(&t, &st);
        CHECK(st.step >= 30, "only reached step %ld in %.1fs", st.step, waited);
        CHECK(st.loss > 0.0f, "loss %f", st.loss);
        CHECK(st.state == OAI_TRAIN_RUNNING || st.state == OAI_TRAIN_PAUSED,
              "state %d while training", (int)st.state);
    }

    TEST("cancelling is honoured promptly");
    {
        double waited;
        oai_trainer_stop(&t);
        waited = wait_until(&t, has_stopped, 5.0);
        CHECK(!oai_trainer_is_running(&t), "still running after %.2fs", waited);
        /* One batch at this size is well under a millisecond; allow plenty of
         * slack for a loaded CI machine and still catch a hung flag. */
        CHECK(waited < 2.0, "cancel took %.2fs, expected well under a second",
              waited);
        oai_trainer_wait(&t);
    }

    TEST("cancelling left a checkpoint that reloads");
    {
        oai_net reloaded;
        CHECK(oai_file_exists(ckpt), "no checkpoint was written");
        CHECK(oai_net_load(&reloaded, ckpt) == 0, "checkpoint does not reload");
        CHECK(reloaded.adam_step > 0, "checkpoint has no optimizer progress");
        oai_net_free(&reloaded);
    }

    TEST("it can be restarted and keeps its step count");
    {
        oai_train_stats before, after;
        oai_trainer_get_stats(&t, &before);
        CHECK(oai_trainer_start(&t) == 0, "restart");
        wait_until(&t, has_progressed, 5.0);
        oai_sleep_ms(60.0);
        oai_trainer_stop(&t);
        wait_until(&t, has_stopped, 5.0);
        oai_trainer_wait(&t);
        oai_trainer_get_stats(&t, &after);
        CHECK(after.step > before.step, "step went %ld -> %ld",
              before.step, after.step);
    }

    TEST("the learning feed described what happened");
    {
        long i, total = oai_stream_total(feed);
        int saw_start = 0, saw_stop = 0;
        oai_line ln;
        CHECK(total > 2, "feed has %ld lines", total);
        for (i = total > 250 ? total - 250 : 0; i < total; ++i) {
            if (!oai_stream_get(feed, i, &ln)) continue;
            if (strstr(ln.text, "training started")) saw_start = 1;
            if (strstr(ln.text, "training stopped")) saw_stop = 1;
        }
        CHECK(saw_start, "no start line in the feed");
        CHECK(saw_stop, "no stop line in the feed");
    }

    TEST("resuming from the checkpoint restores the run");
    {
        oai_trainer t2;
        oai_train_stats st;
        oai_stream *feed2 = oai_stream_new(64);
        CHECK(oai_trainer_init(&t2, &cfg, feed2) == 0, "resume init");
        CHECK(t2.net.adam_step > 0, "did not resume optimizer state");
        oai_trainer_get_stats(&t2, &st);
        oai_trainer_free(&t2);
        oai_stream_free(feed2);
    }

    oai_trainer_free(&t);
    oai_stream_free(feed);
    oai_pool_shutdown();
    remove(corpus);
    remove(ckpt);
    TEST_MAIN_END();
}
