/* test_net.c -- the model: gradients, learning, and checkpoint round trips.
 *
 * The gradient check is the important one here. If the backward pass and the
 * forward pass ever disagree, training still runs and the loss still moves --
 * it just moves toward the wrong thing. Comparing against a finite difference
 * is the only cheap way to catch that.
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#include "oai_net.h"
#include "oai_pool.h"
#include "test_util.h"

static void build_vocab(oai_vocab *v)
{
    const char *text = "the quick brown fox jumps over a lazy dog. \n";
    oai_vocab_build(v, text, strlen(text));
}

static void fill_batch(oai_batch *b, const oai_net *net, oai_rng *r)
{
    int i, t;
    for (i = 0; i < b->batch; ++i) {
        for (t = 0; t < net->context; ++t)
            b->ctx[i * net->context + t] = oai_rng_below(r, net->vocab_size);
        b->y[i] = oai_rng_below(r, net->vocab_size);
    }
}

/* Numerical gradient of the loss with respect to one parameter.
 *
 * The step has to be large enough that the difference in the loss survives
 * single-precision rounding and small enough that the curvature does not
 * dominate. At float32 the usable window is narrow; 3e-3 sits in the middle
 * of it, and leaves a residual noise floor of roughly 1e-4 in the result. */
static double numeric_grad(oai_net *net, oai_batch *b, float *param)
{
    const float eps = 3e-3f;
    float saved = *param;
    double lo, hi;

    *param = saved + eps;
    hi = (double)oai_net_forward(net, b);
    *param = saved - eps;
    lo = (double)oai_net_forward(net, b);
    *param = saved;
    return (hi - lo) / (2.0 * (double)eps);
}

static void check_matrix_grads(oai_net *net, oai_batch *b, oai_rng *r,
                               oai_mat *param, oai_mat *grad, const char *name)
{
    int trial;
    for (trial = 0; trial < 8; ++trial) {
        int idx = oai_rng_below(r, param->rows * param->cols);
        double numeric, analytic, scale;

        /* Recompute the analytic gradient for this exact state each time --
         * the backward pass overwrites b->logits, so it cannot be reused. */
        oai_net_forward(net, b);
        oai_net_backward(net, b);
        analytic = (double)grad->data[idx];

        numeric = numeric_grad(net, b, &param->data[idx]);

        /* Accept on either an absolute or a relative match: below the noise
         * floor the relative test is meaningless, and above it the absolute
         * one is. A genuinely wrong gradient -- a sign error, a transposed
         * matrix, a missing term -- fails both by a wide margin. */
        scale = fabs(numeric) + fabs(analytic);
        CHECK(fabs(numeric - analytic) < 3e-4
              || fabs(numeric - analytic) / (scale + 1e-12) < 0.02,
              "%s[%d]: analytic %g vs numeric %g", name, idx, analytic, numeric);
    }
}

int main(void)
{
    oai_vocab vocab;
    oai_net   net;
    oai_batch b;
    oai_rng   r;

    printf("test_net\n");
    oai_pool_init(1);
    build_vocab(&vocab);
    oai_rng_seed(&r, 7);

    TEST("vocabulary covers exactly the bytes in the text");
    CHECK(vocab.size > 10 && vocab.size < 40, "vocab size %d", vocab.size);
    CHECK(oai_vocab_id(&vocab, (unsigned char)'q') >= 0, "q missing");
    CHECK(oai_vocab_id(&vocab, (unsigned char)'Z') < 0, "Z should be absent");
    CHECK(oai_vocab_byte(&vocab, oai_vocab_id(&vocab, (unsigned char)'x')) == 'x',
          "round trip failed");

    CHECK(oai_net_init(&net, &vocab, 4, 6, 16, 123) == 0, "net init");
    CHECK(oai_batch_init(&b, &net, 5) == 0, "batch init");
    fill_batch(&b, &net, &r);

    TEST("forward produces a valid distribution and a sane loss");
    {
        float loss = oai_net_forward(&net, &b);
        int i, j;
        CHECK(loss > 0.0f && loss < 20.0f, "loss %f", loss);
        /* An untrained model should be near uniform: log(vocab). */
        CHECK_NEAR(loss, log((double)net.vocab_size), 0.5, "initial loss");
        for (i = 0; i < b.batch; ++i) {
            double s = 0.0;
            for (j = 0; j < net.vocab_size; ++j)
                s += b.logits[i * net.vocab_size + j];
            CHECK_NEAR(s, 1.0, 1e-4, "row %d probabilities", i);
        }
    }

    TEST("backward matches finite differences");
    check_matrix_grads(&net, &b, &r, &net.w2,  &net.d_w2,  "w2");
    check_matrix_grads(&net, &b, &r, &net.b2,  &net.d_b2,  "b2");
    check_matrix_grads(&net, &b, &r, &net.w1,  &net.d_w1,  "w1");
    check_matrix_grads(&net, &b, &r, &net.b1,  &net.d_b1,  "b1");
    check_matrix_grads(&net, &b, &r, &net.emb, &net.d_emb, "emb");

    TEST("gradient norm and clipping");
    {
        float before, after;
        oai_net_forward(&net, &b);
        oai_net_backward(&net, &b);
        before = oai_net_grad_norm(&net);
        CHECK(before > 0.0f, "grad norm should be positive");
        oai_net_scale_grads(&net, 0.5f);
        after = oai_net_grad_norm(&net);
        CHECK_NEAR(after, before * 0.5f, before * 1e-3f, "scaled grad norm");
    }

    TEST("repeated steps drive the loss down on a fixed batch");
    {
        float first = oai_net_forward(&net, &b);
        float last = first;
        int i;
        for (i = 0; i < 200; ++i) {
            last = oai_net_forward(&net, &b);
            oai_net_backward(&net, &b);
            oai_net_adam_step(&net, 0.02f, 0.0f);
        }
        CHECK(last < first * 0.5f,
              "loss did not fall: %f -> %f", first, last);
    }

    TEST("sampling stays inside the vocabulary");
    {
        char out[64];
        int n = oai_net_sample(&net, &r, "the ", 0.8f, out, (int)sizeof out);
        int i;
        CHECK(n == (int)sizeof out - 1, "sample length %d", n);
        for (i = 0; i < n; ++i)
            CHECK(oai_vocab_id(&vocab, (unsigned char)out[i]) >= 0,
                  "sample produced a byte outside the vocabulary at %d", i);
    }

    TEST("checkpoints round trip exactly");
    {
        const char *path = "test-checkpoint.tmp";
        oai_net loaded;
        size_t n;
        int i, differences = 0;

        CHECK(oai_net_save(&net, path) == 0, "save");
        CHECK(oai_net_load(&loaded, path) == 0, "load");
        CHECK(loaded.vocab_size == net.vocab_size, "vocab size");
        CHECK(loaded.context == net.context, "context");
        CHECK(loaded.hidden == net.hidden, "hidden");
        CHECK(loaded.adam_step == net.adam_step, "adam step");

        n = (size_t)net.w1.rows * net.w1.cols;
        for (i = 0; i < (int)n; ++i)
            if (net.w1.data[i] != loaded.w1.data[i]) differences++;
        CHECK(differences == 0, "%d weights differ after a round trip",
              differences);

        n = (size_t)net.m_w1.rows * net.m_w1.cols;
        differences = 0;
        for (i = 0; i < (int)n; ++i)
            if (net.m_w1.data[i] != loaded.m_w1.data[i]) differences++;
        CHECK(differences == 0, "optimizer state was not preserved");

        oai_net_free(&loaded);
        remove(path);
    }

    TEST("loading a file that is not a checkpoint fails cleanly");
    {
        oai_net junk;
        FILE *f = fopen("test-junk.tmp", "wb");
        fputs("this is not a checkpoint", f);
        fclose(f);
        CHECK(oai_net_load(&junk, "test-junk.tmp") != 0, "junk was accepted");
        CHECK(oai_net_load(&junk, "no-such-file-at-all.bin") != 0,
              "missing file was accepted");
        remove("test-junk.tmp");
    }

    oai_batch_free(&b);
    oai_net_free(&net);
    oai_pool_shutdown();
    TEST_MAIN_END();
}
