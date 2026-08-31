/* oai_log.c -- ring-buffer line streams shared between threads.
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#include "oai_log.h"
#include "oai_platform.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct oai_stream {
    oai_line  *lines;
    int        capacity;
    long       total;      /* lines ever pushed */
    oai_mutex *lock;
    FILE      *mirror;
};

oai_stream *oai_stream_new(int capacity)
{
    oai_stream *s;
    if (capacity < 16) capacity = 16;
    s = (oai_stream *)calloc(1, sizeof *s);
    if (!s) return NULL;
    s->lines = (oai_line *)calloc((size_t)capacity, sizeof(oai_line));
    s->lock  = oai_mutex_new();
    if (!s->lines || !s->lock) {
        oai_stream_free(s);
        return NULL;
    }
    s->capacity = capacity;
    return s;
}

void oai_stream_free(oai_stream *s)
{
    if (!s) return;
    if (s->mirror) fclose(s->mirror);
    oai_mutex_free(s->lock);
    free(s->lines);
    free(s);
}

int oai_stream_mirror_to_file(oai_stream *s, const char *path)
{
    FILE *f = NULL;
    if (!s) return -1;
    if (path && path[0]) {
        f = fopen(path, "a");
        if (!f) return -1;
    }
    oai_mutex_lock(s->lock);
    if (s->mirror) fclose(s->mirror);
    s->mirror = f;
    oai_mutex_unlock(s->lock);
    return f || !path ? 0 : -1;
}

static const char *kind_tag(oai_line_kind k)
{
    switch (k) {
    case OAI_LINE_STEP:    return "step";
    case OAI_LINE_SAMPLE:  return "sample";
    case OAI_LINE_INSIGHT: return "insight";
    case OAI_LINE_EVENT:   return "event";
    case OAI_LINE_WARN:    return "warn";
    case OAI_LINE_USER:    return "you";
    case OAI_LINE_AGENT:   return "oai";
    default:               return "info";
    }
}

static void stream_push_raw(oai_stream *s, oai_line_kind kind, const char *text,
                            int cont)
{
    oai_line *slot;
    if (!s) return;
    oai_mutex_lock(s->lock);
    slot = &s->lines[(size_t)(s->total % s->capacity)];
    slot->kind = kind;
    slot->cont = cont;
    slot->time = oai_time_now();
    snprintf(slot->text, sizeof slot->text, "%s", text);
    s->total++;
    if (s->mirror) {
        fprintf(s->mirror, "[%-7s] %s\n", kind_tag(kind), slot->text);
        fflush(s->mirror);
    }
    oai_mutex_unlock(s->lock);
}

void oai_stream_push(oai_stream *s, oai_line_kind kind, const char *fmt, ...)
{
    char buf[OAI_LINE_MAX];
    va_list ap;
    if (!s) return;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    stream_push_raw(s, kind, buf, 0);
}

void oai_stream_push_wrapped(oai_stream *s, oai_line_kind kind,
                             const char *text, int width)
{
    char line[OAI_LINE_MAX];
    int  used = 0, last_space = -1, emitted = 0;
    const char *p = text;

    if (!s || !text) return;
    if (width < 8) width = 8;
    if (width > OAI_LINE_MAX - 1) width = OAI_LINE_MAX - 1;

    for (; *p; ++p) {
        unsigned char c = (unsigned char)*p;
        if (c == '\n') {
            line[used] = '\0';
            stream_push_raw(s, kind, line, emitted++);
            used = 0;
            last_space = -1;
            continue;
        }
        if (c == '\r') continue;
        if (c == '\t') c = ' ';
        if (c < 32) c = '.';
        if (c == ' ') last_space = used;
        line[used++] = (char)c;

        if (used >= width) {
            int cut = (last_space > width / 3) ? last_space : used;
            int carry = used - cut;
            char tail[OAI_LINE_MAX];
            if (carry > 0 && cut < used) memcpy(tail, line + cut, (size_t)carry);
            line[cut] = '\0';
            stream_push_raw(s, kind, line, emitted++);
            if (cut < used && carry > 0) {
                /* Skip the space we broke on. */
                int skip = (line[cut] == '\0' && last_space == cut) ? 1 : 0;
                memmove(line, tail + skip, (size_t)(carry - skip));
                used = carry - skip;
            } else {
                used = 0;
            }
            last_space = -1;
        }
    }
    if (used > 0) {
        line[used] = '\0';
        stream_push_raw(s, kind, line, emitted++);
    }
}

long oai_stream_total(oai_stream *s)
{
    long t;
    if (!s) return 0;
    oai_mutex_lock(s->lock);
    t = s->total;
    oai_mutex_unlock(s->lock);
    return t;
}

int oai_stream_capacity(oai_stream *s)
{
    return s ? s->capacity : 0;
}

int oai_stream_get(oai_stream *s, long index, oai_line *out)
{
    int ok = 0;
    if (!s || !out) return 0;
    oai_mutex_lock(s->lock);
    if (index >= 0 && index < s->total && index >= s->total - s->capacity) {
        *out = s->lines[(size_t)(index % s->capacity)];
        ok = 1;
    }
    oai_mutex_unlock(s->lock);
    return ok;
}

void oai_stream_clear(oai_stream *s)
{
    if (!s) return;
    oai_mutex_lock(s->lock);
    s->total = 0;
    oai_mutex_unlock(s->lock);
}
