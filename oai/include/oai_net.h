/* oai_net.h -- the neural network Oai trains.
 *
 * A character-level model: an embedding table, one tanh hidden layer over a
 * fixed window of characters, and a softmax over the vocabulary. Small enough
 * to read end to end, big enough to produce recognisable text.
 *
 *   ids[context] -> embed -> concat -> W1,b1 -> tanh -> W2,b2 -> softmax
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#ifndef OAI_NET_H
#define OAI_NET_H

#include <stddef.h>
#include "oai_tensor.h"
#include "oai_data.h"

#ifdef __cplusplus
extern "C" {
#endif

#define OAI_CKPT_MAGIC   0x4941334Fu   /* "O3AI" little-endian */
#define OAI_CKPT_VERSION 1

typedef struct {
    /* shape */
    int vocab_size;
    int context;
    int embed_dim;
    int hidden;
    int input_dim;      /* context * embed_dim */

    /* parameters */
    oai_mat emb;        /* vocab x embed_dim */
    oai_mat w1;         /* input_dim x hidden */
    oai_mat b1;         /* 1 x hidden */
    oai_mat w2;         /* hidden x vocab */
    oai_mat b2;         /* 1 x vocab */

    /* gradients */
    oai_mat d_emb, d_w1, d_b1, d_w2, d_b2;

    /* Adam moments */
    oai_mat m_emb, m_w1, m_b1, m_w2, m_b2;
    oai_mat v_emb, v_w1, v_b1, v_w2, v_b2;
    long    adam_step;

    /* copy of the vocabulary so a checkpoint is self-contained */
    oai_vocab vocab;
} oai_net;

/* Scratch buffers for one batch, allocated once and reused every step. */
typedef struct {
    int    batch;
    int   *ctx;         /* batch x context ids */
    int   *y;           /* batch targets */
    float *x;           /* batch x input_dim */
    float *h;           /* batch x hidden (post-tanh) */
    float *dh;          /* batch x hidden */
    float *dhpre;       /* batch x hidden */
    float *logits;      /* batch x vocab (softmax probabilities after fwd) */
    float *dx;          /* batch x input_dim */
} oai_batch;

int  oai_net_init(oai_net *net, const oai_vocab *vocab, int context,
                  int embed_dim, int hidden, unsigned int seed);
void oai_net_free(oai_net *net);
size_t oai_net_param_count(const oai_net *net);

int  oai_batch_init(oai_batch *b, const oai_net *net, int batch);
void oai_batch_free(oai_batch *b);

/* Runs the forward pass and returns the mean cross-entropy loss in nats.
 * b->logits holds softmax probabilities on return. */
float oai_net_forward(oai_net *net, oai_batch *b);
/* Backward pass; accumulates into the gradient matrices (zeroed first). */
void  oai_net_backward(oai_net *net, oai_batch *b);
/* Global L2 norm of all gradients, used for clipping and for the UI. */
float oai_net_grad_norm(const oai_net *net);
void  oai_net_scale_grads(oai_net *net, float factor);
/* One Adam step with decoupled weight decay. */
void  oai_net_adam_step(oai_net *net, float lr, float weight_decay);

/* Greedy/temperature sampling. `seed_text` primes the context; the generated
 * characters are written to out (NUL-terminated). Returns characters written. */
int  oai_net_sample(oai_net *net, oai_rng *rng, const char *seed_text,
                    float temperature, char *out, int max_len);

/* Fills probs (vocab_size floats) for the next character after `context_ids`. */
void oai_net_next_dist(oai_net *net, const int *context_ids, float *probs);

int  oai_net_save(const oai_net *net, const char *path);
/* Loads into an uninitialised net. Returns 0 on success. */
int  oai_net_load(oai_net *net, const char *path);

#ifdef __cplusplus
}
#endif
#endif /* OAI_NET_H */
