/* oai_data.c -- corpus loading, vocabulary construction and batch sampling.
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#include "oai_data.h"
#include "oai_platform.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Enough text to show the model learning something within a few thousand
 * steps when no corpus file is present. */
static const char *k_builtin_corpus =
"oai is a small agent written in c. it learns to predict the next character\n"
"in a stream of text. every step it reads a window of characters and guesses\n"
"what comes next. when the guess is wrong the error flows backwards through\n"
"the network and the weights move a little. that is all learning is here.\n"
"the model has three parts: an embedding table that turns each character into\n"
"a vector, a hidden layer that mixes the window together, and an output layer\n"
"that scores every character in the vocabulary. the loss is cross entropy.\n"
"a low loss means the model is surprised less often by the text it reads.\n"
"training runs on a worker thread. the interface stays responsive because the\n"
"trainer checks a cancel flag between batches, so you can stop it at any time\n"
"and the weights are written to a checkpoint before the thread returns.\n"
"if a graphics card is present oai will use part of it, never all of it. the\n"
"budget is a fraction you choose. the trainer works for a slice of each cycle\n"
"and then yields the device, so the rest of the machine keeps running well.\n"
"you can talk to the model in the chat box while it trains. ask it to sample\n"
"text, ask for the loss, ask what it has learned, or tell it to stop.\n"
"the code is plain c99 with no dependencies beyond the standard library.\n"
"it builds with gcc, clang or msvc, and the python script packages it into a\n"
"single executable you can hand to somebody else.\n";

/* ============================================================== vocabulary */

void oai_vocab_build(oai_vocab *v, const char *text, size_t len)
{
    size_t i;
    int seen[OAI_MAX_VOCAB];
    int b;

    memset(seen, 0, sizeof seen);
    for (i = 0; i < len; ++i) seen[(unsigned char)text[i]] = 1;

    v->size = 0;
    for (b = 0; b < OAI_MAX_VOCAB; ++b) v->to_id[b] = -1;
    /* Walk byte values in order so the mapping is stable across runs. */
    for (b = 0; b < OAI_MAX_VOCAB; ++b) {
        if (!seen[b]) continue;
        v->to_id[b] = v->size;
        v->to_byte[v->size] = (unsigned char)b;
        v->size++;
    }
    if (v->size == 0) {  /* empty corpus: keep a usable one-symbol vocabulary */
        v->to_id[(int)'\n'] = 0;
        v->to_byte[0] = '\n';
        v->size = 1;
    }
}

int oai_vocab_id(const oai_vocab *v, unsigned char byte)
{
    return v->to_id[byte];
}

unsigned char oai_vocab_byte(const oai_vocab *v, int id)
{
    if (id < 0 || id >= v->size) return '?';
    return v->to_byte[id];
}

const char *oai_vocab_describe(const oai_vocab *v, int id, char *buf, size_t n)
{
    unsigned char c = oai_vocab_byte(v, id);
    if (n < 8) { if (n) buf[0] = '\0'; return buf; }
    switch (c) {
    case '\n': snprintf(buf, n, "\\n"); break;
    case '\t': snprintf(buf, n, "\\t"); break;
    case '\r': snprintf(buf, n, "\\r"); break;
    case ' ':  snprintf(buf, n, "' '"); break;
    default:
        if (c < 32 || c > 126) snprintf(buf, n, "0x%02x", (unsigned)c);
        else                   snprintf(buf, n, "%c", (char)c);
        break;
    }
    return buf;
}

const char *oai_builtin_corpus(void)
{
    return k_builtin_corpus;
}

/* ================================================================= dataset */

int oai_dataset_load(oai_dataset *ds, const char *path, float val_fraction)
{
    char  *text = NULL;
    size_t len = 0, i;
    int    used_builtin = 0;

    memset(ds, 0, sizeof *ds);

    if (path && path[0]) text = oai_read_file(path, &len);
    if (!text || len < 256) {
        free(text);
        len  = strlen(k_builtin_corpus);
        text = (char *)malloc(len + 1);
        if (!text) return -1;
        memcpy(text, k_builtin_corpus, len + 1);
        used_builtin = 1;
    }

    if (used_builtin)
        snprintf(ds->source, sizeof ds->source, "built-in corpus");
    else
        snprintf(ds->source, sizeof ds->source, "%s", path);

    oai_vocab_build(&ds->vocab, text, len);

    ds->ids = (unsigned char *)malloc(len);
    if (!ds->ids) { free(text); return -1; }
    for (i = 0; i < len; ++i) {
        int id = ds->vocab.to_id[(unsigned char)text[i]];
        ds->ids[i] = (unsigned char)(id < 0 ? 0 : id);
    }
    free(text);

    ds->len = len;
    if (val_fraction < 0.0f) val_fraction = 0.0f;
    if (val_fraction > 0.5f) val_fraction = 0.5f;
    ds->val_len   = (size_t)((double)len * (double)val_fraction);
    ds->train_len = len - ds->val_len;
    if (ds->train_len < 64) { ds->train_len = len; ds->val_len = 0; }
    return 0;
}

void oai_dataset_free(oai_dataset *ds)
{
    if (!ds) return;
    free(ds->ids);
    ds->ids = NULL;
    ds->len = ds->train_len = ds->val_len = 0;
}

void oai_dataset_batch(const oai_dataset *ds, oai_rng *rng, int batch,
                       int context, int validation, int *ctx_out, int *y_out)
{
    size_t lo, hi;
    int    b, t;

    if (validation && ds->val_len > (size_t)(context + 2)) {
        lo = ds->train_len;
        hi = ds->len;
    } else {
        lo = 0;
        hi = ds->train_len;
    }
    if (hi < lo + (size_t)context + 2) { lo = 0; hi = ds->len; }

    for (b = 0; b < batch; ++b) {
        size_t span = hi - lo - (size_t)context - 1;
        size_t start = lo;
        if (span > 0) {
            /* Two draws so the offset covers corpora larger than 32 bits of
             * generator output would reach on its own. */
            unsigned long r = (unsigned long)oai_rng_below(rng, 1 << 15);
            r = (r << 15) ^ (unsigned long)oai_rng_below(rng, 1 << 15);
            start = lo + (size_t)(r % span);
        }
        for (t = 0; t < context; ++t)
            ctx_out[(size_t)b * context + t] = ds->ids[start + (size_t)t];
        y_out[b] = ds->ids[start + (size_t)context];
    }
}
