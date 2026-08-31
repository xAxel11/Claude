/* oai_log.h -- thread-safe line streams.
 *
 * Two of these back the interface: one carries the live "what am I learning"
 * feed written by the trainer thread, the other carries the chat transcript.
 * Both are fixed-capacity ring buffers, so a run of any length uses the same
 * memory, and both are safe to write from one thread while the UI reads.
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#ifndef OAI_LOG_H
#define OAI_LOG_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

#define OAI_LINE_MAX 512

/* Determines how the UI colours a line. */
typedef enum {
    OAI_LINE_INFO = 0,
    OAI_LINE_STEP,     /* periodic loss / throughput report */
    OAI_LINE_SAMPLE,   /* text the model generated */
    OAI_LINE_INSIGHT,  /* what the model currently believes */
    OAI_LINE_EVENT,    /* start, stop, checkpoint */
    OAI_LINE_WARN,
    OAI_LINE_USER,     /* chat: something you typed */
    OAI_LINE_AGENT     /* chat: something Oai replied */
} oai_line_kind;

typedef struct {
    oai_line_kind kind;
    double        time;
    int           cont;   /* 1 when this is the tail of a wrapped message, so
                           * the UI can indent it instead of repeating the
                           * "you"/"oai" prefix */
    char          text[OAI_LINE_MAX];
} oai_line;

typedef struct oai_stream oai_stream;

oai_stream *oai_stream_new(int capacity);
void        oai_stream_free(oai_stream *s);

/* Mirrors every subsequent line to `path` as well. Pass NULL to stop. */
int  oai_stream_mirror_to_file(oai_stream *s, const char *path);

void oai_stream_push(oai_stream *s, oai_line_kind kind, const char *fmt, ...);
/* Pushes text verbatim, splitting on newlines and wrapping at `width`. */
void oai_stream_push_wrapped(oai_stream *s, oai_line_kind kind,
                             const char *text, int width);

/* Total lines ever pushed; the ring holds the most recent `capacity` of them. */
long oai_stream_total(oai_stream *s);
int  oai_stream_capacity(oai_stream *s);
/* Copies the line with absolute index `index` into `out`. Returns 1 when the
 * line is still in the ring, 0 when it has scrolled out. */
int  oai_stream_get(oai_stream *s, long index, oai_line *out);
void oai_stream_clear(oai_stream *s);

#ifdef __cplusplus
}
#endif
#endif /* OAI_LOG_H */
