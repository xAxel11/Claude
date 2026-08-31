/* oai_data.h -- corpus loading, the character vocabulary and batch sampling.
 *
 * Oai works at the character level: the vocabulary is built from the bytes the
 * corpus actually contains, so a 40 KB English corpus yields a vocabulary of
 * roughly 70 symbols rather than a full 256-entry byte table.
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#ifndef OAI_DATA_H
#define OAI_DATA_H

#include <stddef.h>
#include "oai_tensor.h"

#ifdef __cplusplus
extern "C" {
#endif

#define OAI_MAX_VOCAB 256

typedef struct {
    int  size;                       /* number of distinct symbols */
    int  to_id[OAI_MAX_VOCAB];       /* byte -> id, or -1 */
    unsigned char to_byte[OAI_MAX_VOCAB]; /* id -> byte */
} oai_vocab;

typedef struct {
    oai_vocab      vocab;
    unsigned char *ids;        /* the whole corpus, one id per character */
    size_t         len;
    size_t         train_len;  /* [0, train_len) is training data */
    size_t         val_len;    /* [train_len, len) is held out */
    char           source[512];
} oai_dataset;

void oai_vocab_build(oai_vocab *v, const char *text, size_t len);
int  oai_vocab_id(const oai_vocab *v, unsigned char byte);
unsigned char oai_vocab_byte(const oai_vocab *v, int id);
/* Renders a symbol for display, escaping newline/tab/space. Returns buf. */
const char *oai_vocab_describe(const oai_vocab *v, int id, char *buf, size_t n);

/* Loads a corpus from disk. Falls back to a small built-in corpus when the
 * file is missing or too short, so a fresh clone can train immediately. */
int  oai_dataset_load(oai_dataset *ds, const char *path, float val_fraction);
void oai_dataset_free(oai_dataset *ds);

/* Fills ctx_out (batch x context ids) and y_out (batch targets) with random
 * windows drawn from the training split, or the validation split when
 * `validation` is non-zero. */
void oai_dataset_batch(const oai_dataset *ds, oai_rng *rng, int batch,
                       int context, int validation,
                       int *ctx_out, int *y_out);

/* The corpus compiled into the binary, used when no file is available. */
const char *oai_builtin_corpus(void);

#ifdef __cplusplus
}
#endif
#endif /* OAI_DATA_H */
