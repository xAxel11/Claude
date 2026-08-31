/* oai_net.c -- forward pass, backward pass, Adam and checkpointing.
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#include "oai_net.h"
#include "oai_gpu.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ============================================================ construction */

static int alloc_group(oai_mat *p, oai_mat *g, oai_mat *m, oai_mat *v,
                       int rows, int cols)
{
    if (oai_mat_init(p, rows, cols) != 0) return -1;
    if (oai_mat_init(g, rows, cols) != 0) return -1;
    if (oai_mat_init(m, rows, cols) != 0) return -1;
    if (oai_mat_init(v, rows, cols) != 0) return -1;
    return 0;
}

int oai_net_init(oai_net *net, const oai_vocab *vocab, int context,
                 int embed_dim, int hidden, unsigned int seed)
{
    oai_rng rng;
    memset(net, 0, sizeof *net);

    net->vocab      = *vocab;
    net->vocab_size = vocab->size;
    net->context    = context;
    net->embed_dim  = embed_dim;
    net->hidden     = hidden;
    net->input_dim  = context * embed_dim;
    net->adam_step  = 0;

    if (alloc_group(&net->emb, &net->d_emb, &net->m_emb, &net->v_emb,
                    net->vocab_size, embed_dim) != 0) goto fail;
    if (alloc_group(&net->w1, &net->d_w1, &net->m_w1, &net->v_w1,
                    net->input_dim, hidden) != 0) goto fail;
    if (alloc_group(&net->b1, &net->d_b1, &net->m_b1, &net->v_b1,
                    1, hidden) != 0) goto fail;
    if (alloc_group(&net->w2, &net->d_w2, &net->m_w2, &net->v_w2,
                    hidden, net->vocab_size) != 0) goto fail;
    if (alloc_group(&net->b2, &net->d_b2, &net->m_b2, &net->v_b2,
                    1, net->vocab_size) != 0) goto fail;

    oai_rng_seed(&rng, seed);
    oai_mat_randomize(&net->emb, &rng, 0.30f);
    oai_mat_randomize(&net->w1,  &rng, 1.0f / sqrtf((float)net->input_dim));
    oai_mat_randomize(&net->w2,  &rng, 1.0f / sqrtf((float)hidden));
    return 0;

fail:
    oai_net_free(net);
    return -1;
}

void oai_net_free(oai_net *net)
{
    if (!net) return;
    oai_mat_free(&net->emb); oai_mat_free(&net->d_emb);
    oai_mat_free(&net->m_emb); oai_mat_free(&net->v_emb);
    oai_mat_free(&net->w1);  oai_mat_free(&net->d_w1);
    oai_mat_free(&net->m_w1);  oai_mat_free(&net->v_w1);
    oai_mat_free(&net->b1);  oai_mat_free(&net->d_b1);
    oai_mat_free(&net->m_b1);  oai_mat_free(&net->v_b1);
    oai_mat_free(&net->w2);  oai_mat_free(&net->d_w2);
    oai_mat_free(&net->m_w2);  oai_mat_free(&net->v_w2);
    oai_mat_free(&net->b2);  oai_mat_free(&net->d_b2);
    oai_mat_free(&net->m_b2);  oai_mat_free(&net->v_b2);
}

size_t oai_net_param_count(const oai_net *net)
{
    return (size_t)net->vocab_size * net->embed_dim
         + (size_t)net->input_dim  * net->hidden + (size_t)net->hidden
         + (size_t)net->hidden     * net->vocab_size + (size_t)net->vocab_size;
}

int oai_batch_init(oai_batch *b, const oai_net *net, int batch)
{
    memset(b, 0, sizeof *b);
    b->batch  = batch;
    b->ctx    = (int   *)calloc((size_t)batch * net->context, sizeof(int));
    b->y      = (int   *)calloc((size_t)batch, sizeof(int));
    b->x      = (float *)calloc((size_t)batch * net->input_dim, sizeof(float));
    b->h      = (float *)calloc((size_t)batch * net->hidden, sizeof(float));
    b->dh     = (float *)calloc((size_t)batch * net->hidden, sizeof(float));
    b->dhpre  = (float *)calloc((size_t)batch * net->hidden, sizeof(float));
    b->logits = (float *)calloc((size_t)batch * net->vocab_size, sizeof(float));
    b->dx     = (float *)calloc((size_t)batch * net->input_dim, sizeof(float));
    if (!b->ctx || !b->y || !b->x || !b->h || !b->dh || !b->dhpre
        || !b->logits || !b->dx) {
        oai_batch_free(b);
        return -1;
    }
    return 0;
}

void oai_batch_free(oai_batch *b)
{
    if (!b) return;
    free(b->ctx); free(b->y); free(b->x); free(b->h);
    free(b->dh); free(b->dhpre); free(b->logits); free(b->dx);
    memset(b, 0, sizeof *b);
}

/* ================================================================ forward */

/* Gathers the embedding rows for every context position into b->x. */
static void gather_embeddings(const oai_net *net, oai_batch *b)
{
    int i, t;
    for (i = 0; i < b->batch; ++i) {
        float *row = b->x + (size_t)i * net->input_dim;
        for (t = 0; t < net->context; ++t) {
            int id = b->ctx[(size_t)i * net->context + t];
            if (id < 0 || id >= net->vocab_size) id = 0;
            memcpy(row + (size_t)t * net->embed_dim,
                   net->emb.data + (size_t)id * net->embed_dim,
                   (size_t)net->embed_dim * sizeof(float));
        }
    }
}

float oai_net_forward(oai_net *net, oai_batch *b)
{
    int    i;
    double loss = 0.0;

    gather_embeddings(net, b);

    oai_gpu_or_cpu_matmul(b->x, net->w1.data, b->h,
                          b->batch, net->input_dim, net->hidden);
    oai_add_bias(b->h, net->b1.data, b->batch, net->hidden);
    oai_tanh_inplace(b->h, (size_t)b->batch * net->hidden);

    oai_gpu_or_cpu_matmul(b->h, net->w2.data, b->logits,
                          b->batch, net->hidden, net->vocab_size);
    oai_add_bias(b->logits, net->b2.data, b->batch, net->vocab_size);
    oai_softmax_rows(b->logits, b->batch, net->vocab_size);

    for (i = 0; i < b->batch; ++i) {
        float p = b->logits[(size_t)i * net->vocab_size + b->y[i]];
        if (p < 1e-9f) p = 1e-9f;
        loss += -log((double)p);
    }
    return (float)(loss / (double)b->batch);
}

/* =============================================================== backward */

void oai_net_backward(oai_net *net, oai_batch *b)
{
    int   i, t;
    float inv = 1.0f / (float)b->batch;

    oai_mat_zero(&net->d_emb);
    oai_mat_zero(&net->d_w1);
    oai_mat_zero(&net->d_b1);
    oai_mat_zero(&net->d_w2);
    oai_mat_zero(&net->d_b2);

    /* dlogits = (softmax - onehot) / batch, written over b->logits. */
    for (i = 0; i < b->batch; ++i) {
        float *row = b->logits + (size_t)i * net->vocab_size;
        int j;
        row[b->y[i]] -= 1.0f;
        for (j = 0; j < net->vocab_size; ++j) row[j] *= inv;
    }

    /* Output layer. */
    oai_matmul_tn_acc(b->h, b->logits, net->d_w2.data,
                      net->hidden, b->batch, net->vocab_size);
    oai_sum_rows(b->logits, net->d_b2.data, b->batch, net->vocab_size);

    /* Back through the hidden activation. */
    oai_matmul_nt(b->logits, net->w2.data, b->dh,
                  b->batch, net->vocab_size, net->hidden);
    oai_tanh_backward(b->h, b->dh, b->dhpre,
                      (size_t)b->batch * net->hidden);

    /* Hidden layer. */
    oai_matmul_tn_acc(b->x, b->dhpre, net->d_w1.data,
                      net->input_dim, b->batch, net->hidden);
    oai_sum_rows(b->dhpre, net->d_b1.data, b->batch, net->hidden);

    /* Back into the embedding table: scatter-add each window slice. */
    oai_matmul_nt(b->dhpre, net->w1.data, b->dx,
                  b->batch, net->hidden, net->input_dim);
    for (i = 0; i < b->batch; ++i) {
        const float *row = b->dx + (size_t)i * net->input_dim;
        for (t = 0; t < net->context; ++t) {
            int id = b->ctx[(size_t)i * net->context + t];
            float *dst;
            int e;
            if (id < 0 || id >= net->vocab_size) id = 0;
            dst = net->d_emb.data + (size_t)id * net->embed_dim;
            for (e = 0; e < net->embed_dim; ++e)
                dst[e] += row[(size_t)t * net->embed_dim + e];
        }
    }
}

float oai_net_grad_norm(const oai_net *net)
{
    const oai_mat *g[5];
    double sum = 0.0;
    int k;
    g[0] = &net->d_emb; g[1] = &net->d_w1; g[2] = &net->d_b1;
    g[3] = &net->d_w2;  g[4] = &net->d_b2;
    for (k = 0; k < 5; ++k) {
        size_t n = (size_t)g[k]->rows * (size_t)g[k]->cols, i;
        for (i = 0; i < n; ++i) sum += (double)g[k]->data[i] * g[k]->data[i];
    }
    return (float)sqrt(sum);
}

void oai_net_scale_grads(oai_net *net, float factor)
{
    oai_mat *g[5];
    int k;
    g[0] = &net->d_emb; g[1] = &net->d_w1; g[2] = &net->d_b1;
    g[3] = &net->d_w2;  g[4] = &net->d_b2;
    for (k = 0; k < 5; ++k)
        oai_scale(g[k]->data, (size_t)g[k]->rows * (size_t)g[k]->cols, factor);
}

/* =================================================================== adam */

static void adam_apply(float *p, const float *g, float *m, float *v, size_t n,
                       float lr, float b1, float b2, float eps,
                       float bias1, float bias2, float wd)
{
    size_t i;
    for (i = 0; i < n; ++i) {
        float grad = g[i];
        float mhat, vhat;
        m[i] = b1 * m[i] + (1.0f - b1) * grad;
        v[i] = b2 * v[i] + (1.0f - b2) * grad * grad;
        mhat = m[i] / bias1;
        vhat = v[i] / bias2;
        if (wd != 0.0f) p[i] -= lr * wd * p[i];   /* decoupled weight decay */
        p[i] -= lr * mhat / (sqrtf(vhat) + eps);
    }
}

void oai_net_adam_step(oai_net *net, float lr, float weight_decay)
{
    const float b1 = 0.9f, b2 = 0.999f, eps = 1e-8f;
    float bias1, bias2;
    oai_mat *p[5], *g[5], *m[5], *v[5];
    int k;

    net->adam_step++;
    bias1 = 1.0f - powf(b1, (float)net->adam_step);
    bias2 = 1.0f - powf(b2, (float)net->adam_step);
    if (bias1 < 1e-8f) bias1 = 1e-8f;
    if (bias2 < 1e-8f) bias2 = 1e-8f;

    p[0]=&net->emb; g[0]=&net->d_emb; m[0]=&net->m_emb; v[0]=&net->v_emb;
    p[1]=&net->w1;  g[1]=&net->d_w1;  m[1]=&net->m_w1;  v[1]=&net->v_w1;
    p[2]=&net->b1;  g[2]=&net->d_b1;  m[2]=&net->m_b1;  v[2]=&net->v_b1;
    p[3]=&net->w2;  g[3]=&net->d_w2;  m[3]=&net->m_w2;  v[3]=&net->v_w2;
    p[4]=&net->b2;  g[4]=&net->d_b2;  m[4]=&net->m_b2;  v[4]=&net->v_b2;

    for (k = 0; k < 5; ++k) {
        size_t n = (size_t)p[k]->rows * (size_t)p[k]->cols;
        /* Biases are left out of weight decay, as is conventional. */
        float wd = (p[k]->rows == 1) ? 0.0f : weight_decay;
        adam_apply(p[k]->data, g[k]->data, m[k]->data, v[k]->data, n,
                   lr, b1, b2, eps, bias1, bias2, wd);
    }
}

/* =============================================================== sampling */

void oai_net_next_dist(oai_net *net, const int *context_ids, float *probs)
{
    float *x = (float *)malloc((size_t)net->input_dim * sizeof(float));
    float *h = (float *)malloc((size_t)net->hidden * sizeof(float));
    int t;
    if (!x || !h) { free(x); free(h); return; }

    for (t = 0; t < net->context; ++t) {
        int id = context_ids[t];
        if (id < 0 || id >= net->vocab_size) id = 0;
        memcpy(x + (size_t)t * net->embed_dim,
               net->emb.data + (size_t)id * net->embed_dim,
               (size_t)net->embed_dim * sizeof(float));
    }
    oai_matmul(x, net->w1.data, h, 1, net->input_dim, net->hidden);
    oai_add_bias(h, net->b1.data, 1, net->hidden);
    oai_tanh_inplace(h, (size_t)net->hidden);
    oai_matmul(h, net->w2.data, probs, 1, net->hidden, net->vocab_size);
    oai_add_bias(probs, net->b2.data, 1, net->vocab_size);
    oai_softmax_rows(probs, 1, net->vocab_size);

    free(x);
    free(h);
}

int oai_net_sample(oai_net *net, oai_rng *rng, const char *seed_text,
                   float temperature, char *out, int max_len)
{
    int   *ctx = (int *)malloc((size_t)net->context * sizeof(int));
    float *probs = (float *)malloc((size_t)net->vocab_size * sizeof(float));
    float *logits = (float *)malloc((size_t)net->vocab_size * sizeof(float));
    int    written = 0, t, space_id;

    if (!ctx || !probs || !logits) {
        free(ctx); free(probs); free(logits);
        if (max_len > 0) out[0] = '\0';
        return 0;
    }
    if (temperature < 0.05f) temperature = 0.05f;

    space_id = oai_vocab_id(&net->vocab, (unsigned char)' ');
    if (space_id < 0) space_id = 0;
    for (t = 0; t < net->context; ++t) ctx[t] = space_id;

    if (seed_text) {
        size_t slen = strlen(seed_text), i;
        for (i = 0; i < slen; ++i) {
            int id = oai_vocab_id(&net->vocab, (unsigned char)seed_text[i]);
            if (id < 0) continue;
            memmove(ctx, ctx + 1, (size_t)(net->context - 1) * sizeof(int));
            ctx[net->context - 1] = id;
        }
    }

    while (written < max_len - 1) {
        int   j, pick = 0;
        float sum = 0.0f, r;

        oai_net_next_dist(net, ctx, probs);

        /* Re-apply temperature through the log of the distribution. */
        for (j = 0; j < net->vocab_size; ++j) {
            float p = probs[j] < 1e-9f ? 1e-9f : probs[j];
            logits[j] = logf(p) / temperature;
        }
        oai_softmax_rows(logits, 1, net->vocab_size);

        r = oai_rng_uniform(rng);
        for (j = 0; j < net->vocab_size; ++j) {
            sum += logits[j];
            if (r <= sum) { pick = j; break; }
            pick = j;
        }

        out[written++] = (char)oai_vocab_byte(&net->vocab, pick);
        memmove(ctx, ctx + 1, (size_t)(net->context - 1) * sizeof(int));
        ctx[net->context - 1] = pick;
    }
    out[written] = '\0';

    free(ctx);
    free(probs);
    free(logits);
    return written;
}

/* =========================================================== checkpointing */

static int write_mat(FILE *f, const oai_mat *m)
{
    size_t n = (size_t)m->rows * (size_t)m->cols;
    if (fwrite(&m->rows, sizeof(int), 1, f) != 1) return -1;
    if (fwrite(&m->cols, sizeof(int), 1, f) != 1) return -1;
    if (n && fwrite(m->data, sizeof(float), n, f) != n) return -1;
    return 0;
}

static int read_mat(FILE *f, oai_mat *m)
{
    int rows, cols;
    size_t n;
    if (fread(&rows, sizeof(int), 1, f) != 1) return -1;
    if (fread(&cols, sizeof(int), 1, f) != 1) return -1;
    if (rows != m->rows || cols != m->cols) return -1;
    n = (size_t)rows * (size_t)cols;
    if (n && fread(m->data, sizeof(float), n, f) != n) return -1;
    return 0;
}

int oai_net_save(const oai_net *net, const char *path)
{
    FILE *f;
    unsigned int magic = OAI_CKPT_MAGIC, version = OAI_CKPT_VERSION;
    int header[4];
    long step;

    if (!path || !path[0]) return -1;
    f = fopen(path, "wb");
    if (!f) return -1;

    header[0] = net->vocab_size;
    header[1] = net->context;
    header[2] = net->embed_dim;
    header[3] = net->hidden;
    step = net->adam_step;

    if (fwrite(&magic, sizeof magic, 1, f) != 1) goto fail;
    if (fwrite(&version, sizeof version, 1, f) != 1) goto fail;
    if (fwrite(header, sizeof(int), 4, f) != 4) goto fail;
    if (fwrite(&step, sizeof step, 1, f) != 1) goto fail;
    if (fwrite(net->vocab.to_byte, 1, (size_t)net->vocab_size, f)
        != (size_t)net->vocab_size) goto fail;

    if (write_mat(f, &net->emb) != 0) goto fail;
    if (write_mat(f, &net->w1)  != 0) goto fail;
    if (write_mat(f, &net->b1)  != 0) goto fail;
    if (write_mat(f, &net->w2)  != 0) goto fail;
    if (write_mat(f, &net->b2)  != 0) goto fail;
    if (write_mat(f, &net->m_emb) != 0) goto fail;
    if (write_mat(f, &net->m_w1)  != 0) goto fail;
    if (write_mat(f, &net->m_b1)  != 0) goto fail;
    if (write_mat(f, &net->m_w2)  != 0) goto fail;
    if (write_mat(f, &net->m_b2)  != 0) goto fail;
    if (write_mat(f, &net->v_emb) != 0) goto fail;
    if (write_mat(f, &net->v_w1)  != 0) goto fail;
    if (write_mat(f, &net->v_b1)  != 0) goto fail;
    if (write_mat(f, &net->v_w2)  != 0) goto fail;
    if (write_mat(f, &net->v_b2)  != 0) goto fail;

    fclose(f);
    return 0;
fail:
    fclose(f);
    return -1;
}

int oai_net_load(oai_net *net, const char *path)
{
    FILE *f;
    unsigned int magic = 0, version = 0;
    int header[4];
    long step = 0;
    oai_vocab vocab;
    int i;

    if (!path || !path[0]) return -1;
    f = fopen(path, "rb");
    if (!f) return -1;

    if (fread(&magic, sizeof magic, 1, f) != 1 || magic != OAI_CKPT_MAGIC)
        goto fail_closed;
    if (fread(&version, sizeof version, 1, f) != 1
        || version != OAI_CKPT_VERSION) goto fail_closed;
    if (fread(header, sizeof(int), 4, f) != 4) goto fail_closed;
    if (fread(&step, sizeof step, 1, f) != 1) goto fail_closed;
    if (header[0] <= 0 || header[0] > OAI_MAX_VOCAB) goto fail_closed;

    memset(&vocab, 0, sizeof vocab);
    vocab.size = header[0];
    for (i = 0; i < OAI_MAX_VOCAB; ++i) vocab.to_id[i] = -1;
    if (fread(vocab.to_byte, 1, (size_t)vocab.size, f) != (size_t)vocab.size)
        goto fail_closed;
    for (i = 0; i < vocab.size; ++i) vocab.to_id[vocab.to_byte[i]] = i;

    if (oai_net_init(net, &vocab, header[1], header[2], header[3], 1u) != 0)
        goto fail_closed;
    net->adam_step = step;

    if (read_mat(f, &net->emb) != 0) goto fail;
    if (read_mat(f, &net->w1)  != 0) goto fail;
    if (read_mat(f, &net->b1)  != 0) goto fail;
    if (read_mat(f, &net->w2)  != 0) goto fail;
    if (read_mat(f, &net->b2)  != 0) goto fail;
    if (read_mat(f, &net->m_emb) != 0) goto fail;
    if (read_mat(f, &net->m_w1)  != 0) goto fail;
    if (read_mat(f, &net->m_b1)  != 0) goto fail;
    if (read_mat(f, &net->m_w2)  != 0) goto fail;
    if (read_mat(f, &net->m_b2)  != 0) goto fail;
    if (read_mat(f, &net->v_emb) != 0) goto fail;
    if (read_mat(f, &net->v_w1)  != 0) goto fail;
    if (read_mat(f, &net->v_b1)  != 0) goto fail;
    if (read_mat(f, &net->v_w2)  != 0) goto fail;
    if (read_mat(f, &net->v_b2)  != 0) goto fail;

    fclose(f);
    return 0;
fail:
    oai_net_free(net);
fail_closed:
    fclose(f);
    return -1;
}
