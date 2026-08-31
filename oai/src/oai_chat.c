/* oai_chat.c -- command parsing and replies for the chat box.
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#include "oai_chat.h"
#include "oai_gpu.h"

#include <ctype.h>
#include <stdarg.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Used when no interface has published a width yet -- headless mode, or the
 * very first message before the first frame is drawn. */
#define CHAT_WRAP_DEFAULT 72

static int chat_wrap(const oai_app *app)
{
    int w = oai_atomic_load((oai_atomic_i32 *)&app->chat_width);
    if (w < 24) w = CHAT_WRAP_DEFAULT;
    if (w > OAI_LINE_MAX - 1) w = OAI_LINE_MAX - 1;
    return w;
}

static void say(oai_app *app, const char *fmt, ...)
{
    char buf[OAI_LINE_MAX];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    oai_stream_push_wrapped(app->chat, OAI_LINE_AGENT, buf, chat_wrap(app));
}

/* Copies the lower-cased first word of `text` into `out` and returns a pointer
 * to whatever follows it. A word longer than `out` is truncated in `out` but
 * still skipped in full, so the remainder never starts mid-word. */
static const char *first_word(const char *text, char *out, size_t n)
{
    size_t i = 0;
    while (*text == ' ' || *text == '\t') text++;
    while (text[i] && !isspace((unsigned char)text[i])) {
        if (i < n - 1) out[i] = (char)tolower((unsigned char)text[i]);
        i++;
    }
    out[i < n - 1 ? i : n - 1] = '\0';
    while (text[i] == ' ' || text[i] == '\t') i++;
    return text + i;
}

static int matches(const char *word, const char *const *options, int count)
{
    int i;
    for (i = 0; i < count; ++i)
        if (strcmp(word, options[i]) == 0) return 1;
    return 0;
}

/* --------------------------------------------------------------- commands */

static void cmd_help(oai_app *app)
{
    say(app, "Commands: train | stop | pause | status | sample [seed] | "
             "learned | lr <rate> | temp <t> | gpu <0.1-1.0> | save | "
             "corpus | clear | help | exit");
    say(app, "Anything else you type is fed to the model and it continues "
             "your text. Early on that is noise; it gets better as the loss "
             "falls.");
    say(app, "Keys: Ctrl+T train, Ctrl+X stop, Ctrl+P pause, Ctrl+L redraw, "
             "Ctrl+C quit. Tab moves focus; PgUp/PgDn scroll the learning "
             "feed and End returns to the live tail. Letters are never "
             "shortcuts while you are typing here.");
}

static void cmd_status(oai_app *app)
{
    oai_train_stats st;
    oai_gpu_info gi;
    const char *state = "idle";

    oai_trainer_get_stats(&app->trainer, &st);
    oai_gpu_get_info(&gi);

    switch (st.state) {
    case OAI_TRAIN_RUNNING:  state = "training"; break;
    case OAI_TRAIN_PAUSED:   state = "paused";   break;
    case OAI_TRAIN_STOPPING: state = "stopping"; break;
    case OAI_TRAIN_FINISHED: state = "stopped";  break;
    default:                 state = "idle";     break;
    }

    say(app, "State: %s at step %ld.", state, st.step);
    if (st.step > 0) {
        say(app, "Loss %.4f (average %.4f, best %.4f) -- perplexity %.1f, "
                 "meaning it is choosing between about %.0f characters at "
                 "each position.",
            st.loss, st.loss_avg, st.loss_best, expf(st.loss_avg),
            expf(st.loss_avg));
        if (st.val_loss >= 0.0f)
            say(app, "Held-out loss %.4f.", st.val_loss);
        say(app, "%.0f steps/s, %.0f examples/s, %.1f s elapsed.",
            st.steps_per_sec, st.chars_per_sec, st.elapsed);
    }
    if (gi.active)
        say(app, "GPU: %s, budget %.0f%% (%s).", gi.device_name,
            gi.budget * 100.0f,
            gi.partitioned ? "enforced by device fission" : "duty cycled");
    else
        say(app, "Compute: CPU. %s", gi.status[0] ? gi.status : "");
}

static void cmd_train(oai_app *app)
{
    if (oai_trainer_is_running(&app->trainer)) {
        say(app, "Already training. Say \"stop\" to cancel.");
        return;
    }
    if (oai_trainer_start(&app->trainer) == 0)
        say(app, "Training. Watch the left pane -- it shows the loss falling "
                 "and samples of what the model can write. Say \"stop\" or "
                 "press Ctrl+X whenever you want; nothing is lost.");
    else
        say(app, "Could not start the training thread.");
}

static void cmd_stop(oai_app *app)
{
    if (!oai_trainer_is_running(&app->trainer)) {
        say(app, "Not training right now.");
        return;
    }
    oai_trainer_stop(&app->trainer);
    say(app, "Cancelling. The loop stops after the batch it is on and writes "
             "a checkpoint first.");
}

static void cmd_pause(oai_app *app)
{
    if (!oai_trainer_is_running(&app->trainer)) {
        say(app, "Not training right now.");
        return;
    }
    oai_trainer_toggle_pause(&app->trainer);
    say(app, "Toggled pause.");
}

static void cmd_sample(oai_app *app, const char *seed)
{
    char buf[600];
    if (!seed || !*seed) seed = "the ";
    oai_trainer_sample(&app->trainer, seed, app->cfg.temperature, buf,
                       (int)sizeof buf - 1);
    say(app, "%s%s", seed, buf);
}

static void cmd_learned(oai_app *app)
{
    static const char *const probes[] = { "the ", "a", "learn", "\n" };
    char line[192];
    int i;
    oai_train_stats st;

    oai_trainer_get_stats(&app->trainer, &st);
    if (st.step == 0) {
        say(app, "Nothing yet -- the weights are still random. Say \"train\".");
        return;
    }
    say(app, "At step %ld its strongest expectations are:", st.step);
    for (i = 0; i < 4; ++i) {
        oai_trainer_describe_prediction(&app->trainer, probes[i], line,
                                        sizeof line);
        say(app, "  %s", line);
    }
    say(app, "Average loss %.4f. A loss of %.2f on this vocabulary would be "
             "pure guessing.", st.loss_avg, logf((float)app->trainer.net.vocab_size));
}

static void cmd_lr(oai_app *app, const char *arg)
{
    float lr;
    if (!arg || !*arg) {
        oai_train_stats st;
        oai_trainer_get_stats(&app->trainer, &st);
        say(app, "Learning rate is %.5f.", st.lr);
        return;
    }
    lr = (float)atof(arg);
    if (lr <= 0.0f) { say(app, "That is not a usable learning rate."); return; }
    oai_trainer_set_lr(&app->trainer, lr);
    say(app, "Learning rate set to %.5f. It takes effect on the next step.", lr);
}

static void cmd_temp(oai_app *app, const char *arg)
{
    float t;
    if (!arg || !*arg) {
        say(app, "Sampling temperature is %.2f.", app->cfg.temperature);
        return;
    }
    t = (float)atof(arg);
    if (t < 0.05f) t = 0.05f;
    if (t > 3.0f)  t = 3.0f;
    app->cfg.temperature = t;
    app->trainer.cfg.temperature = t;
    say(app, "Temperature set to %.2f. Lower is more repetitive, higher is "
             "more adventurous.", t);
}

static void cmd_gpu(oai_app *app, const char *arg)
{
    oai_gpu_info gi;
    float b;

    oai_gpu_get_info(&gi);
    if (!arg || !*arg) {
        if (gi.active)
            say(app, "%s -- %d of %d compute units, %lu MB of %lu MB, "
                     "budget %.0f%%.",
                gi.device_name, gi.compute_units_used, gi.compute_units_total,
                gi.budget_mem_mb, gi.global_mem_mb, gi.budget * 100.0f);
        else
            say(app, "No GPU in use. %s", gi.status);
        return;
    }
    b = (float)atof(arg);
    if (b > 1.0f && b <= 100.0f) b /= 100.0f;   /* accept "50" as 50% */
    if (b < 0.05f || b > 1.0f) {
        say(app, "Give a budget between 0.05 and 1.0.");
        return;
    }
    app->cfg.gpu_budget = b;
    oai_gpu_set_budget(b);
    if (gi.active && gi.partitioned)
        say(app, "Budget noted (%.0f%%), but this device is hard-partitioned; "
                 "restart Oai to re-partition it.", b * 100.0f);
    else if (gi.active)
        say(app, "GPU budget set to %.0f%%. The duty cycle adjusts on the "
                 "next batch.", b * 100.0f);
    else
        say(app, "Noted, but no GPU is active, so this changes nothing today.");
}

static void cmd_corpus(oai_app *app)
{
    say(app, "Corpus: %s -- %lu characters, %d distinct symbols, %lu held out "
             "for validation.",
        app->trainer.data.source, (unsigned long)app->trainer.data.len,
        app->trainer.data.vocab.size,
        (unsigned long)app->trainer.data.val_len);
    say(app, "Point Oai at your own text with --corpus <file> and it will "
             "learn that instead.");
}

static void cmd_save(oai_app *app)
{
    if (oai_trainer_save(&app->trainer, app->cfg.checkpoint_path) == 0)
        say(app, "Saved to %s.", app->cfg.checkpoint_path);
    else
        say(app, "Could not write %s.", app->cfg.checkpoint_path);
}

/* ----------------------------------------------------------------- entry */

void oai_chat_greet(oai_app *app)
{
    oai_stream_push_wrapped(app->chat, OAI_LINE_AGENT,
                            "Oai " OAI_VERSION_STRING " -- a small agent that "
                            "learns to write, in C.", chat_wrap(app));
    say(app, "Type \"train\" to start learning, \"stop\" to cancel it, or "
             "\"help\" for everything else. Whatever else you type, I will "
             "try to continue.");
}

void oai_chat_submit(oai_app *app, const char *text)
{
    char verb[32];
    const char *rest;

    static const char *const w_help[]   = {"help","?","commands","h"};
    static const char *const w_train[]  = {"train","start","go","learn","run"};
    static const char *const w_stop[]   = {"stop","cancel","halt","abort","kill"};
    static const char *const w_pause[]  = {"pause","resume","hold"};
    static const char *const w_status[] = {"status","stats","state","how"};
    static const char *const w_sample[] = {"sample","write","generate","say"};
    static const char *const w_learn[]  = {"learned","knows","what","insight"};
    static const char *const w_quit[]   = {"exit","quit","bye","q"};
    static const char *const w_clear[]  = {"clear","cls"};

    if (!text) return;
    while (*text == ' ') text++;
    if (!*text) return;

    oai_stream_push_wrapped(app->chat, OAI_LINE_USER, text, chat_wrap(app));
    rest = first_word(text, verb, sizeof verb);

    if (matches(verb, w_help, 4))        { cmd_help(app);   return; }
    if (matches(verb, w_train, 5))       { cmd_train(app);  return; }
    if (matches(verb, w_stop, 5))        { cmd_stop(app);   return; }
    if (matches(verb, w_pause, 3))       { cmd_pause(app);  return; }
    if (matches(verb, w_status, 4))      { cmd_status(app); return; }
    if (matches(verb, w_sample, 4))      { cmd_sample(app, rest); return; }
    if (matches(verb, w_learn, 4))       { cmd_learned(app); return; }
    if (matches(verb, w_clear, 2))       { oai_stream_clear(app->chat);
                                           oai_chat_greet(app); return; }
    if (strcmp(verb, "lr") == 0)         { cmd_lr(app, rest);   return; }
    if (strcmp(verb, "temp") == 0
     || strcmp(verb, "temperature") == 0){ cmd_temp(app, rest); return; }
    if (strcmp(verb, "gpu") == 0)        { cmd_gpu(app, rest);  return; }
    if (strcmp(verb, "corpus") == 0
     || strcmp(verb, "data") == 0)       { cmd_corpus(app);     return; }
    if (strcmp(verb, "save") == 0)       { cmd_save(app);       return; }
    if (matches(verb, w_quit, 4)) {
        say(app, "Stopping and saving. Goodbye.");
        oai_atomic_store(&app->quit, 1);
        return;
    }

    /* Not a command: let the model continue what the user wrote. */
    {
        char buf[400];
        oai_train_stats st;
        oai_trainer_get_stats(&app->trainer, &st);
        oai_trainer_sample(&app->trainer, text, app->cfg.temperature, buf,
                           (int)sizeof buf - 1);
        if (st.step < 200)
            say(app, "(only %ld steps in, so this is mostly noise) %s",
                st.step, buf);
        else
            say(app, "%s", buf);
    }
}
