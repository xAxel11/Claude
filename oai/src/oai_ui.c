/* oai_ui.c -- double-buffered terminal rendering, input handling and layout.
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#include "oai_ui.h"
#include "oai_chat.h"
#include "oai_gpu.h"

#include <ctype.h>
#include <stdarg.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define UI_MAX_W 400
#define UI_MAX_H 200
#define UI_FPS   30.0
#define UI_INPUT_MAX 480
#define UI_HISTORY 32
#define UI_SPARK 96

/* ================================================================ styles */

typedef enum {
    ST_DEFAULT = 0, ST_DIM, ST_TITLE, ST_ACCENT, ST_GOOD, ST_WARN, ST_BAD,
    ST_USER, ST_AGENT, ST_SAMPLE, ST_INSIGHT, ST_STEP, ST_BORDER,
    ST_BORDER_FOCUS, ST_HEADER, ST_FRESH, ST_COUNT
} ui_style;

static const char *const k_sgr[ST_COUNT] = {
    "\033[0m",             /* default          */
    "\033[0;90m",          /* dim              */
    "\033[1;97;44m",       /* title bar        */
    "\033[0;96m",          /* accent           */
    "\033[0;92m",          /* good             */
    "\033[0;93m",          /* warn             */
    "\033[0;91m",          /* bad              */
    "\033[1;95m",          /* chat: you        */
    "\033[0;97m",          /* chat: oai        */
    "\033[0;92m",          /* generated sample */
    "\033[0;96m",          /* insight          */
    "\033[0;37m",          /* step report      */
    "\033[0;90m",          /* border           */
    "\033[0;96m",          /* focused border   */
    "\033[1;96m",          /* pane header      */
    "\033[1;92m"           /* a just-arrived line */
};

/* ============================================================ cell buffers */

typedef struct {
    unsigned int cp;    /* Unicode code point */
    unsigned char st;   /* ui_style */
} ui_cell;

typedef struct {
    int      w, h;
    ui_cell *front;
    ui_cell *back;
    char    *out;       /* output assembly buffer */
    size_t   out_cap;
    size_t   out_len;
    int      valid;     /* front buffer reflects the terminal */
} ui_screen;

static ui_screen S;

static int ui_screen_resize(int w, int h)
{
    size_t n;
    if (w < 40) w = 40;
    if (h < 12) h = 12;
    if (w > UI_MAX_W) w = UI_MAX_W;
    if (h > UI_MAX_H) h = UI_MAX_H;
    if (S.w == w && S.h == h && S.front) return 0;

    n = (size_t)w * (size_t)h;
    free(S.front);
    free(S.back);
    S.front = (ui_cell *)calloc(n, sizeof(ui_cell));
    S.back  = (ui_cell *)calloc(n, sizeof(ui_cell));
    if (!S.front || !S.back) return -1;
    S.w = w;
    S.h = h;
    S.valid = 0;                 /* force a full repaint after a resize */

    /* Worst case per changed cell: a cursor move (~10 bytes), an SGR sequence
     * (~9) and a 4-byte code point. 24 covers it with room to spare; the
     * writers clamp anyway, so this only decides whether a pathological frame
     * is drawn in full or clipped. */
    if (S.out_cap < n * 24 + 4096) {
        free(S.out);
        S.out_cap = n * 24 + 4096;
        S.out = (char *)malloc(S.out_cap);
        if (!S.out) return -1;
    }
    return 0;
}

static void ui_screen_free(void)
{
    free(S.front); free(S.back); free(S.out);
    memset(&S, 0, sizeof S);
}

static void ui_clear_back(void)
{
    size_t i, n = (size_t)S.w * (size_t)S.h;
    for (i = 0; i < n; ++i) {
        S.back[i].cp = ' ';
        S.back[i].st = ST_DEFAULT;
    }
}

static void put_cp(int x, int y, unsigned int cp, ui_style st)
{
    ui_cell *c;
    if (x < 0 || y < 0 || x >= S.w || y >= S.h) return;
    c = &S.back[(size_t)y * S.w + x];
    c->cp = cp;
    c->st = (unsigned char)st;
}

/* Decodes one UTF-8 sequence; returns bytes consumed and writes the code
 * point. Invalid bytes are passed through as U+FFFD-ish placeholders. */
static int utf8_next(const char *s, unsigned int *cp)
{
    unsigned char c = (unsigned char)s[0];
    if (c < 0x80) { *cp = c; return 1; }
    if ((c & 0xE0) == 0xC0 && (s[1] & 0xC0) == 0x80) {
        *cp = (unsigned int)((c & 0x1F) << 6) | (unsigned char)(s[1] & 0x3F);
        return 2;
    }
    if ((c & 0xF0) == 0xE0 && (s[1] & 0xC0) == 0x80 && (s[2] & 0xC0) == 0x80) {
        *cp = (unsigned int)((c & 0x0F) << 12)
            | (unsigned int)((s[1] & 0x3F) << 6)
            | (unsigned int)(s[2] & 0x3F);
        return 3;
    }
    if ((c & 0xF8) == 0xF0 && (s[1] & 0xC0) == 0x80 && (s[2] & 0xC0) == 0x80
        && (s[3] & 0xC0) == 0x80) {
        *cp = (unsigned int)((c & 0x07) << 18)
            | (unsigned int)((s[1] & 0x3F) << 12)
            | (unsigned int)((s[2] & 0x3F) << 6)
            | (unsigned int)(s[3] & 0x3F);
        return 4;
    }
    *cp = '?';
    return 1;
}

/* Draws `text` at (x,y), stopping at `max_w` columns. Returns columns used. */
static int put_str(int x, int y, const char *text, ui_style st, int max_w)
{
    int used = 0;
    if (!text) return 0;
    while (*text && used < max_w) {
        unsigned int cp;
        int adv = utf8_next(text, &cp);
        if (cp == '\n' || cp == '\r' || cp == '\t') cp = ' ';
        put_cp(x + used, y, cp, st);
        text += adv;
        used++;
    }
    return used;
}

static void fill_row(int y, int x0, int x1, unsigned int cp, ui_style st)
{
    int x;
    for (x = x0; x <= x1; ++x) put_cp(x, y, cp, st);
}

/* ------------------------------------------------------------ presenting */

static void out_reset(void) { S.out_len = 0; }

static void out_str(const char *s)
{
    size_t n = strlen(s);
    if (S.out_len + n + 1 >= S.out_cap) return;
    memcpy(S.out + S.out_len, s, n);
    S.out_len += n;
}

static void out_cp(unsigned int cp)
{
    char buf[5];
    int n = 0;
    if (cp < 0x80) {
        buf[n++] = (char)cp;
    } else if (cp < 0x800) {
        buf[n++] = (char)(0xC0 | (cp >> 6));
        buf[n++] = (char)(0x80 | (cp & 0x3F));
    } else if (cp < 0x10000) {
        buf[n++] = (char)(0xE0 | (cp >> 12));
        buf[n++] = (char)(0x80 | ((cp >> 6) & 0x3F));
        buf[n++] = (char)(0x80 | (cp & 0x3F));
    } else {
        buf[n++] = (char)(0xF0 | (cp >> 18));
        buf[n++] = (char)(0x80 | ((cp >> 12) & 0x3F));
        buf[n++] = (char)(0x80 | ((cp >> 6) & 0x3F));
        buf[n++] = (char)(0x80 | (cp & 0x3F));
    }
    if (S.out_len + (size_t)n + 1 >= S.out_cap) return;
    memcpy(S.out + S.out_len, buf, (size_t)n);
    S.out_len += (size_t)n;
}

static void out_goto(int x, int y)
{
    char buf[32];
    snprintf(buf, sizeof buf, "\033[%d;%dH", y + 1, x + 1);
    out_str(buf);
}

/* Writes only the cells that differ from the previous frame. */
static void ui_present(int cursor_x, int cursor_y, int show_cursor)
{
    int y, x;
    int cur_style = -1;

    out_reset();
    out_str("\033[?25l");    /* hide the cursor while we paint */

    for (y = 0; y < S.h; ++y) {
        int run = 0;
        for (x = 0; x < S.w; ++x) {
            size_t i = (size_t)y * S.w + x;
            ui_cell *b = &S.back[i];
            ui_cell *f = &S.front[i];
            int changed = !S.valid || b->cp != f->cp || b->st != f->st;
            if (!changed) { run = 0; continue; }
            if (!run) { out_goto(x, y); run = 1; }
            if ((int)b->st != cur_style) {
                out_str(k_sgr[b->st]);
                cur_style = (int)b->st;
            }
            out_cp(b->cp);
            *f = *b;
        }
    }

    out_str("\033[0m");
    if (show_cursor) {
        out_goto(cursor_x, cursor_y);
        out_str("\033[?25h");
    }
    S.valid = 1;

    if (S.out_len) {
        fwrite(S.out, 1, S.out_len, stdout);
        fflush(stdout);
    }
}

/* ================================================================ widgets */

/* Box drawing, with an ASCII fallback for terminals that cannot manage it. */
static int g_unicode = 1;
static unsigned int bx(unsigned int uni, unsigned int ascii)
{
    return g_unicode ? uni : ascii;
}

static void draw_hline(int y, int x0, int x1, ui_style st)
{
    fill_row(y, x0, x1, bx(0x2500, '-'), st);
}

/* A one-row loss history plot using the eighth-block characters. */
static void draw_sparkline(int x, int y, int w, const float *v, int n,
                           ui_style st)
{
    static const unsigned int blocks[8] = {
        0x2581, 0x2582, 0x2583, 0x2584, 0x2585, 0x2586, 0x2587, 0x2588
    };
    float lo = 1e30f, hi = -1e30f;
    int i;

    if (n <= 0 || w <= 0) return;
    for (i = 0; i < n; ++i) {
        if (v[i] < lo) lo = v[i];
        if (v[i] > hi) hi = v[i];
    }
    if (hi - lo < 1e-6f) hi = lo + 1e-6f;

    for (i = 0; i < w; ++i) {
        int   src = n <= w ? i - (w - n) : (int)((double)i / w * n);
        float t;
        int   level;
        if (src < 0) { put_cp(x + i, y, ' ', st); continue; }
        if (src >= n) src = n - 1;
        t = (v[src] - lo) / (hi - lo);
        level = (int)(t * 7.0f + 0.5f);
        if (level < 0) level = 0;
        if (level > 7) level = 7;
        put_cp(x + i, y, g_unicode ? blocks[level]
                                   : (unsigned int)(level > 3 ? '#' : '.'), st);
    }
}

static void draw_progress(int x, int y, int w, double frac, ui_style on,
                          ui_style off)
{
    int i, filled;
    if (w <= 0) return;
    if (frac < 0.0) frac = 0.0;
    if (frac > 1.0) frac = 1.0;
    filled = (int)(frac * w + 0.5);
    for (i = 0; i < w; ++i)
        put_cp(x + i, y, bx(i < filled ? 0x2501 : 0x2500, i < filled ? '=' : '-'),
               i < filled ? on : off);
}

/* ============================================================== ui state */

typedef struct {
    char  input[UI_INPUT_MAX];
    int   len;
    int   cursor;

    char  history[UI_HISTORY][UI_INPUT_MAX];
    int   hist_count;
    int   hist_pos;      /* UI_HISTORY when not browsing */

    long  learn_scroll;  /* absolute index of the top visible line */
    int   learn_follow;
    long  chat_scroll;
    int   chat_follow;

    int   focus;         /* 0 = chat input, 1 = learning pane */
    int   spin;
    float spark[UI_SPARK];
    int   spark_n;
    long  spark_last_step;
    double last_frame;
    char  toast[160];
    double toast_until;
} ui_state;

static ui_state U;

static void toast(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(U.toast, sizeof U.toast, fmt, ap);
    va_end(ap);
    U.toast_until = oai_time_now() + 2.5;
}

/* ============================================================== rendering */

static ui_style style_for_line(oai_line_kind k)
{
    switch (k) {
    case OAI_LINE_STEP:    return ST_STEP;
    case OAI_LINE_SAMPLE:  return ST_SAMPLE;
    case OAI_LINE_INSIGHT: return ST_INSIGHT;
    case OAI_LINE_EVENT:   return ST_ACCENT;
    case OAI_LINE_WARN:    return ST_BAD;
    case OAI_LINE_USER:    return ST_USER;
    case OAI_LINE_AGENT:   return ST_AGENT;
    default:               return ST_DIM;
    }
}

static const char *state_text(oai_train_state s)
{
    switch (s) {
    case OAI_TRAIN_RUNNING:  return "training";
    case OAI_TRAIN_PAUSED:   return "paused";
    case OAI_TRAIN_STOPPING: return "stopping";
    case OAI_TRAIN_FINISHED: return "stopped";
    default:                 return "idle";
    }
}

/* Draws a stream into a pane, newest at the bottom. */
static void draw_stream(oai_stream *s, int x0, int y0, int x1, int y1,
                        long scroll, int follow, int prefix_speaker,
                        double now)
{
    int  rows = y1 - y0 + 1;
    long total = oai_stream_total(s);
    long first = follow ? total - rows : scroll;
    long i;
    int  y = y0;

    if (first < 0) first = 0;
    for (i = first; i < first + rows; ++i, ++y) {
        oai_line ln;
        ui_style st;
        int x = x0;
        if (!oai_stream_get(s, i, &ln)) continue;

        st = style_for_line(ln.kind);
        /* Lines that arrived in the last moment glow briefly, which makes the
         * feed read as motion rather than a jumping block of text. */
        if (now - ln.time < 0.45 && ln.kind != OAI_LINE_USER)
            st = ST_FRESH;

        if (prefix_speaker) {
            /* Only the first line of a message is labelled; the rest of a
             * wrapped message is indented under it. */
            if (ln.cont)
                x += put_str(x, y, "     ", ST_DIM, x1 - x + 1);
            else if (ln.kind == OAI_LINE_USER)
                x += put_str(x, y, "you  ", ST_USER, x1 - x + 1);
            else if (ln.kind == OAI_LINE_AGENT)
                x += put_str(x, y, "oai  ", ST_ACCENT, x1 - x + 1);
            else
                x += put_str(x, y, "     ", ST_DIM, x1 - x + 1);
        }

        put_str(x, y, ln.text, st, x1 - x + 1);
    }
}

static void draw_frame(oai_app *app, int *cursor_x, int *cursor_y)
{
    static const char *const spinner[] = { "|", "/", "-", "\\" };
    oai_train_stats st;
    oai_gpu_info    gi;
    char  buf[512];
    int   W = S.w, H = S.h;
    int   split, body_top, body_bot, chat_bot, i;
    double now = oai_time_now();

    oai_trainer_get_stats(&app->trainer, &st);
    oai_gpu_get_info(&gi);

    ui_clear_back();

    split    = W * 55 / 100;
    if (split < 34) split = 34;
    if (split > W - 30) split = W - 30;
    body_top = 5;
    body_bot = H - 3;
    chat_bot = body_bot - 2;

    /* ---- title bar ------------------------------------------------- */
    fill_row(0, 0, W - 1, ' ', ST_TITLE);
    put_str(1, 0, "Oai " OAI_VERSION_STRING
                  "  ~  an agent that learns to write, in C", ST_TITLE, W - 2);
    snprintf(buf, sizeof buf, "%s %s  step %ld",
             st.state == OAI_TRAIN_RUNNING ? spinner[(U.spin / 4) % 4] : " ",
             state_text(st.state), st.step);
    put_str(W - 1 - (int)strlen(buf), 0, buf, ST_TITLE, (int)strlen(buf));

    /* ---- stats ------------------------------------------------------ */
    if (st.step > 0) {
        snprintf(buf, sizeof buf,
                 " loss %.4f   avg %.4f   best %.4f   perplexity %.1f"
                 "   %.0f steps/s   %.0fs",
                 st.loss, st.loss_avg, st.loss_best, expf(st.loss_avg),
                 st.steps_per_sec, st.elapsed);
    } else {
        snprintf(buf, sizeof buf,
                 " no training yet -- press Ctrl+T, or type \"train\" in the "
                 "chat box");
    }
    put_str(0, 1, buf, st.step > 0 ? ST_DEFAULT : ST_DIM, W);

    if (gi.active)
        snprintf(buf, sizeof buf,
                 " %s   %d/%d compute units   budget %.0f%%   %s",
                 gi.device_name, gi.compute_units_used, gi.compute_units_total,
                 gi.budget * 100.0f,
                 gi.partitioned ? "partitioned" : "duty cycled");
    else
        snprintf(buf, sizeof buf, " CPU backend   %s", gi.status);
    put_str(0, 2, buf, ST_DIM, W);

    /* ---- sparkline and progress ------------------------------------- */
    {
        int spark_w = W / 2;
        if (spark_w > UI_SPARK) spark_w = UI_SPARK;
        put_str(1, 3, "loss ", ST_DIM, 6);
        draw_sparkline(6, 3, spark_w - 6, U.spark, U.spark_n,
                       st.loss_avg <= st.loss_best + 1e-6f ? ST_GOOD : ST_ACCENT);
        if (st.max_steps > 0) {
            int px = spark_w + 2;
            int pw = W - px - 12;
            double frac = st.max_steps ? (double)st.step / (double)st.max_steps
                                       : 0.0;
            if (pw > 4) {
                draw_progress(px, 3, pw, frac, ST_GOOD, ST_DIM);
                snprintf(buf, sizeof buf, " %3.0f%%", frac * 100.0);
                put_str(px + pw, 3, buf, ST_DIM, 8);
            }
        } else {
            snprintf(buf, sizeof buf, "  %s", st.note);
            put_str(spark_w + 2, 3, buf, ST_DIM, W - spark_w - 3);
        }
    }

    /* The chat and the trainer wrap their text to whatever these say. */
    oai_atomic_store(&app->chat_width, W - split - 8);
    oai_atomic_store(&app->feed_width, split - 2);

    /* ---- panes ------------------------------------------------------ */
    draw_hline(4, 0, W - 1, ST_BORDER);
    put_cp(split, 4, bx(0x252C, '+'), ST_BORDER);
    for (i = body_top; i <= body_bot; ++i)
        put_cp(split, i, bx(0x2502, '|'),
               U.focus == 1 ? ST_BORDER : ST_BORDER);

    put_str(2, 4, U.learn_follow ? " LEARNING " : " LEARNING (scrolled) ",
            U.focus == 1 ? ST_BORDER_FOCUS : ST_HEADER, split - 3);
    put_str(split + 2, 4, " CHAT ", U.focus == 0 ? ST_BORDER_FOCUS : ST_HEADER,
            W - split - 3);

    draw_stream(app->learning, 1, body_top, split - 1, body_bot,
                U.learn_scroll, U.learn_follow, 0, now);
    draw_stream(app->chat, split + 2, body_top, W - 2, chat_bot,
                U.chat_scroll, U.chat_follow, 1, now);

    /* ---- input box -------------------------------------------------- */
    draw_hline(chat_bot + 1, split + 1, W - 1, ST_BORDER);
    put_cp(split, chat_bot + 1, bx(0x251C, '+'), ST_BORDER);
    {
        int ix = split + 2;
        int avail = W - ix - 2;
        int scroll = 0;
        if (U.cursor > avail - 1) scroll = U.cursor - (avail - 1);
        put_str(ix, body_bot, "> ", U.focus == 0 ? ST_ACCENT : ST_DIM, 2);
        put_str(ix + 2, body_bot, U.input + scroll, ST_DEFAULT, avail - 2);
        if (U.len == 0)
            put_str(ix + 2, body_bot,
                    "talk to it, or type help", ST_DIM, avail - 2);
        *cursor_x = ix + 2 + (U.cursor - scroll);
        *cursor_y = body_bot;
    }

    /* ---- footer ----------------------------------------------------- */
    draw_hline(H - 2, 0, W - 1, ST_BORDER);
    put_cp(split, H - 2, bx(0x2534, '+'), ST_BORDER);
    if (now < U.toast_until && U.toast[0]) {
        put_str(1, H - 1, U.toast, ST_GOOD, W - 2);
    } else {
        put_str(1, H - 1,
                "^T train   ^X stop   ^P pause   Tab focus   "
                "PgUp/PgDn scroll   ^L redraw   ^C quit   (or type: help)",
                ST_DIM, W - 2);
    }
}

/* ================================================================= input */

static void input_clear(void)
{
    U.input[0] = '\0';
    U.len = 0;
    U.cursor = 0;
    U.hist_pos = UI_HISTORY;
}

static void input_insert(int ch)
{
    if (U.len >= UI_INPUT_MAX - 1) return;
    memmove(U.input + U.cursor + 1, U.input + U.cursor,
            (size_t)(U.len - U.cursor + 1));
    U.input[U.cursor] = (char)ch;
    U.cursor++;
    U.len++;
}

static void input_backspace(void)
{
    if (U.cursor <= 0) return;
    memmove(U.input + U.cursor - 1, U.input + U.cursor,
            (size_t)(U.len - U.cursor + 1));
    U.cursor--;
    U.len--;
}

static void input_delete(void)
{
    if (U.cursor >= U.len) return;
    memmove(U.input + U.cursor, U.input + U.cursor + 1,
            (size_t)(U.len - U.cursor));
    U.len--;
}

static void history_push(const char *text)
{
    int i;
    if (!text[0]) return;
    if (U.hist_count == UI_HISTORY) {
        for (i = 1; i < UI_HISTORY; ++i)
            memcpy(U.history[i - 1], U.history[i], UI_INPUT_MAX);
        U.hist_count--;
    }
    snprintf(U.history[U.hist_count], UI_INPUT_MAX, "%s", text);
    U.hist_count++;
    U.hist_pos = U.hist_count;
}

static void history_browse(int delta)
{
    int pos = U.hist_pos + delta;
    if (U.hist_count == 0) return;
    if (pos < 0) pos = 0;
    if (pos >= U.hist_count) {
        U.hist_pos = U.hist_count;
        input_clear();
        return;
    }
    U.hist_pos = pos;
    /* Source and destination are both inside U, so this is a memmove rather
     * than an snprintf. */
    memmove(U.input, U.history[pos], sizeof U.input);
    U.input[sizeof U.input - 1] = '\0';
    U.len = (int)strlen(U.input);
    U.cursor = U.len;
}

static void scroll_learning(oai_app *app, int delta, int page)
{
    long total = oai_stream_total(app->learning);
    int  rows  = S.h - 8;
    long top;
    if (rows < 1) rows = 1;
    top = U.learn_follow ? total - rows : U.learn_scroll;
    top += (long)delta * (page ? rows : 1);
    if (top > total - rows) { U.learn_follow = 1; return; }
    if (top < 0) top = 0;
    U.learn_scroll = top;
    U.learn_follow = 0;
}

/* Control-key shortcuts.
 *
 * These are deliberately not plain letters. An earlier version treated t, s
 * and p as hotkeys whenever the input line was empty, which meant typing
 * "status" fired stop and then train before the third character arrived.
 * Control combinations cannot collide with anything you might want to type,
 * so they work no matter what the input line holds. */
#define KEY_CTRL(c) ((c) & 0x1f)

/* Returns 1 when the key was consumed. */
static int handle_shortcut(oai_app *app, int key, int *running)
{
    switch (key) {
    case OAI_KEY_PGUP: scroll_learning(app, -1, 1); return 1;
    case OAI_KEY_PGDN: scroll_learning(app,  1, 1); return 1;

    case KEY_CTRL('t'):
        oai_chat_submit(app, "train");
        toast("training started -- Ctrl+X cancels it");
        return 1;
    case KEY_CTRL('x'):
        oai_chat_submit(app, "stop");
        toast("cancelling -- a checkpoint is written first");
        return 1;
    case KEY_CTRL('p'):
        oai_chat_submit(app, "pause");
        toast("pause toggled");
        return 1;
    case KEY_CTRL('l'):
        S.valid = 0;                     /* force a full repaint */
        toast("redrawn");
        return 1;
    case KEY_CTRL('c'):
    case KEY_CTRL('d'):
        *running = 0;
        return 1;
    default:
        break;
    }
    return 0;
}

/* Single letters act as shortcuts only in the learning pane, where there is no
 * text entry to interfere with. */
static int handle_pane_key(oai_app *app, int key, int *running)
{
    switch (key) {
    case OAI_KEY_UP:   scroll_learning(app, -1, 0); return 1;
    case OAI_KEY_DOWN: scroll_learning(app,  1, 0); return 1;
    case OAI_KEY_END:  U.learn_follow = 1;          return 1;
    case OAI_KEY_HOME: U.learn_scroll = 0; U.learn_follow = 0; return 1;
    case 't': case 'T': oai_chat_submit(app, "train");
                        toast("training started"); return 1;
    case 's': case 'S': oai_chat_submit(app, "stop");
                        toast("cancelling -- a checkpoint is written first");
                        return 1;
    case 'p': case 'P': oai_chat_submit(app, "pause"); return 1;
    case 'g':           U.learn_scroll = 0; U.learn_follow = 0; return 1;
    case 'q': case 'Q': *running = 0; return 1;
    default: break;
    }
    return 0;
}

static void submit_input(oai_app *app)
{
    char line[UI_INPUT_MAX];
    if (U.len == 0) return;
    snprintf(line, sizeof line, "%s", U.input);
    history_push(line);
    input_clear();
    U.chat_follow = 1;
    oai_chat_submit(app, line);
}

/* ================================================================== loop */

int oai_ui_run(oai_app *app)
{
    int cols, rows;
    int running = 1;
    double next_frame;

    memset(&U, 0, sizeof U);
    U.learn_follow = 1;
    U.chat_follow  = 1;
    U.hist_pos = UI_HISTORY;

    {
        const char *term = getenv("TERM");
        const char *lang = getenv("LANG");
        if (term && strcmp(term, "dumb") == 0) g_unicode = 0;
        if (lang && !strstr(lang, "UTF") && !strstr(lang, "utf")) g_unicode = 0;
#ifdef OAI_WINDOWS
        g_unicode = 1;   /* the console is switched to UTF-8 in oai_term_raw */
#endif
    }

    if (oai_term_raw() != 0) {
        fprintf(stderr,
                "oai: this terminal cannot be put into raw mode; "
                "falling back to --no-ui\n");
        return oai_ui_run_plain(app);
    }
    oai_term_size(&cols, &rows);
    if (ui_screen_resize(cols, rows) != 0) {
        oai_term_restore();
        fprintf(stderr, "oai: out of memory allocating the screen\n");
        return 1;
    }
    fputs("\033[?1049h", stdout);   /* alternate screen buffer */
    fputs("\033[2J", stdout);
    fflush(stdout);

    oai_chat_greet(app);
    if (app->cfg.autostart) {
        oai_trainer_start(&app->trainer);
        toast("training started");
    }

    next_frame = oai_time_now();

    while (running && !oai_atomic_load(&app->quit)) {
        int key;
        int cx = 0, cy = 0;
        double now;

        /* --- input ---------------------------------------------------- */
        while ((key = oai_term_getkey()) != OAI_KEY_NONE) {
            if (handle_shortcut(app, key, &running)) continue;

            if (key == '\t') { U.focus = !U.focus; continue; }

            if (U.focus == 1 && handle_pane_key(app, key, &running)) continue;

            switch (key) {
            case OAI_KEY_ENTER:     submit_input(app);   break;
            case OAI_KEY_BACKSPACE:
            case 8:                 input_backspace();   break;
            case OAI_KEY_DELETE:    input_delete();      break;
            case OAI_KEY_LEFT:      if (U.cursor > 0) U.cursor--;     break;
            case OAI_KEY_RIGHT:     if (U.cursor < U.len) U.cursor++; break;
            case OAI_KEY_HOME:      U.cursor = 0;        break;
            case OAI_KEY_END:       U.cursor = U.len;    break;
            case OAI_KEY_UP:        history_browse(-1);  break;
            case OAI_KEY_DOWN:      history_browse(1);   break;
            case OAI_KEY_ESC:       input_clear();       break;
            default:
                /* Everything printable is text. No exceptions -- that is the
                 * whole point of putting the shortcuts on control keys. */
                if (key >= 32 && key < 127) input_insert(key);
                break;
            }
        }

        /* --- resize --------------------------------------------------- */
        oai_term_size(&cols, &rows);
        if (cols != S.w || rows != S.h) {
            if (ui_screen_resize(cols, rows) != 0) break;
            fputs("\033[2J", stdout);
        }

        /* --- sparkline history ---------------------------------------- */
        {
            oai_train_stats st;
            oai_trainer_get_stats(&app->trainer, &st);
            if (st.step != U.spark_last_step && st.loss_avg > 0.0f) {
                if (U.spark_n < UI_SPARK) {
                    U.spark[U.spark_n++] = st.loss_avg;
                } else {
                    memmove(U.spark, U.spark + 1,
                            sizeof(float) * (UI_SPARK - 1));
                    U.spark[UI_SPARK - 1] = st.loss_avg;
                }
                U.spark_last_step = st.step;
            }
        }

        /* --- draw ----------------------------------------------------- */
        U.spin++;
        draw_frame(app, &cx, &cy);
        ui_present(cx, cy, U.focus == 0);

        /* --- pace ----------------------------------------------------- */
        next_frame += 1.0 / UI_FPS;
        now = oai_time_now();
        if (next_frame > now) oai_sleep_ms((next_frame - now) * 1000.0);
        else next_frame = now;   /* we fell behind; do not spiral */
    }

    /* Cancel any run in flight and let it checkpoint before we exit. */
    if (oai_trainer_is_running(&app->trainer)) {
        oai_trainer_stop(&app->trainer);
        oai_trainer_wait(&app->trainer);
    }

    fputs("\033[0m\033[?25h\033[?1049l", stdout);
    fflush(stdout);
    oai_term_restore();
    ui_screen_free();
    return 0;
}

/* ============================================================ plain mode */

/* Plain mode has to do two things at once -- stream the learning feed and read
 * commands -- without a UI event loop to hang them off. A reader thread parks
 * on stdin and hands finished lines to the main loop, which prints the feed at
 * a steady rate. That keeps `oai --no-ui --train --steps 2000 > log.txt`
 * working when stdin is a closed pipe, and keeps an interactive shell session
 * responsive at the same time. */
typedef struct {
    oai_mutex     *lock;
    char           pending[UI_INPUT_MAX];
    oai_atomic_i32 has_line;
    oai_atomic_i32 eof;
    oai_atomic_i32 stop;
} plain_reader;

static void plain_reader_thread(void *arg)
{
    plain_reader *r = (plain_reader *)arg;
    char buf[UI_INPUT_MAX];

    while (!oai_atomic_load(&r->stop)) {
        if (!fgets(buf, sizeof buf, stdin)) {
            oai_atomic_store(&r->eof, 1);
            return;
        }
        {
            size_t n = strlen(buf);
            while (n && (buf[n-1] == '\n' || buf[n-1] == '\r')) buf[--n] = '\0';
        }
        /* Wait for the main loop to consume the previous line before
         * overwriting it; commands are rare, so a poll is plenty. */
        while (oai_atomic_load(&r->has_line) && !oai_atomic_load(&r->stop))
            oai_sleep_ms(5.0);

        oai_mutex_lock(r->lock);
        snprintf(r->pending, sizeof r->pending, "%s", buf);
        oai_mutex_unlock(r->lock);
        oai_atomic_store(&r->has_line, 1);
    }
}

/* Prints every line of `s` that has not been printed yet. */
static long drain_stream(oai_stream *s, long shown, const char *prefix)
{
    long total = oai_stream_total(s);
    long oldest = total - oai_stream_capacity(s);
    if (shown < oldest) shown = oldest;
    for (; shown < total; ++shown) {
        oai_line ln;
        if (!oai_stream_get(s, shown, &ln)) continue;
        if (prefix)
            printf("%s%s\n",
                   ln.kind == OAI_LINE_USER ? "you > " : "oai  > ", ln.text);
        else
            printf("%s\n", ln.text);
    }
    return shown;
}

int oai_ui_run_plain(oai_app *app)
{
    plain_reader r;
    oai_thread  *reader;
    long learn_shown = 0, chat_shown = 0;

    printf("Oai %s -- plain mode. Type help for commands, exit to quit.\n",
           OAI_VERSION_STRING);
    oai_chat_greet(app);
    chat_shown = drain_stream(app->chat, chat_shown, "chat");
    learn_shown = drain_stream(app->learning, learn_shown, NULL);
    fflush(stdout);

    if (app->cfg.autostart) oai_trainer_start(&app->trainer);

    memset(&r, 0, sizeof r);
    r.lock = oai_mutex_new();
    if (!r.lock) return 1;
    reader = oai_thread_start(plain_reader_thread, &r);

    for (;;) {
        int training = oai_trainer_is_running(&app->trainer);

        learn_shown = drain_stream(app->learning, learn_shown, NULL);
        chat_shown  = drain_stream(app->chat, chat_shown, "chat");
        fflush(stdout);

        if (oai_atomic_load(&app->quit)) break;

        if (oai_atomic_load(&r.has_line)) {
            char line[UI_INPUT_MAX];
            oai_mutex_lock(r.lock);
            snprintf(line, sizeof line, "%s", r.pending);
            oai_mutex_unlock(r.lock);
            oai_atomic_store(&r.has_line, 0);
            if (line[0]) oai_chat_submit(app, line);
            continue;
        }

        /* Nothing left to read and nothing left to do: we are finished. */
        if (oai_atomic_load(&r.eof) && !training) break;

        oai_sleep_ms(50.0);
    }

    if (oai_trainer_is_running(&app->trainer)) {
        oai_trainer_stop(&app->trainer);
        oai_trainer_wait(&app->trainer);
    }
    learn_shown = drain_stream(app->learning, learn_shown, NULL);
    chat_shown  = drain_stream(app->chat, chat_shown, "chat");
    fflush(stdout);

    oai_atomic_store(&r.stop, 1);
    if (oai_atomic_load(&r.eof)) {
        oai_thread_join(reader);   /* it has already returned */
    }
    /* Otherwise the reader is parked inside fgets and cannot be woken
     * portably; it is harmless and the process is about to exit. */
    oai_mutex_free(r.lock);
    return 0;
}
