/* test_data.c -- corpus loading, batching and the line streams.
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#include "oai_data.h"
#include "oai_log.h"
#include "test_util.h"

int main(void)
{
    printf("test_data\n");

    TEST("a missing corpus falls back to the built-in text");
    {
        oai_dataset ds;
        CHECK(oai_dataset_load(&ds, "definitely-not-a-file.txt", 0.1f) == 0,
              "load should succeed via the fallback");
        CHECK(ds.len > 256, "fallback corpus too short: %lu",
              (unsigned long)ds.len);
        CHECK(ds.vocab.size > 5, "fallback vocab too small");
        CHECK(strstr(ds.source, "built-in") != NULL, "source: %s", ds.source);
        oai_dataset_free(&ds);
    }

    TEST("a real corpus loads, splits and stays in range");
    {
        oai_dataset ds;
        const char *path = "test-corpus.tmp";
        const char *sentence = "the quick brown fox jumps over the lazy dog.\n";
        size_t expected = strlen(sentence) * 200;
        FILE *f = fopen(path, "wb");
        int i;
        for (i = 0; i < 200; ++i) fputs(sentence, f);
        fclose(f);

        CHECK(oai_dataset_load(&ds, path, 0.1f) == 0, "load");
        CHECK(ds.len == expected, "length %lu, expected %lu",
              (unsigned long)ds.len, (unsigned long)expected);
        CHECK(ds.train_len + ds.val_len == ds.len, "split does not cover");
        CHECK(ds.val_len > 0, "no validation split");

        for (i = 0; i < (int)ds.len; ++i)
            CHECK(ds.ids[i] < ds.vocab.size, "id out of range at %d", i);

        TEST("batches stay inside the vocabulary and the split");
        {
            oai_rng r;
            int ctx[8 * 6], y[8], b, t, split;
            oai_rng_seed(&r, 5);
            for (split = 0; split < 2; ++split) {
                oai_dataset_batch(&ds, &r, 8, 6, split, ctx, y);
                for (b = 0; b < 8; ++b) {
                    for (t = 0; t < 6; ++t)
                        CHECK(ctx[b * 6 + t] >= 0
                              && ctx[b * 6 + t] < ds.vocab.size,
                              "context id out of range");
                    CHECK(y[b] >= 0 && y[b] < ds.vocab.size,
                          "target out of range");
                }
            }
        }

        oai_dataset_free(&ds);
        remove(path);
    }

    TEST("symbols render readably");
    {
        oai_vocab v;
        char buf[16];
        oai_vocab_build(&v, "a b\n\t", 5);
        CHECK(strcmp(oai_vocab_describe(&v, oai_vocab_id(&v, ' '), buf,
                                        sizeof buf), "' '") == 0, "space");
        CHECK(strcmp(oai_vocab_describe(&v, oai_vocab_id(&v, '\n'), buf,
                                        sizeof buf), "\\n") == 0, "newline");
        CHECK(strcmp(oai_vocab_describe(&v, oai_vocab_id(&v, 'a'), buf,
                                        sizeof buf), "a") == 0, "letter");
    }

    TEST("the stream keeps the newest lines and drops the oldest");
    {
        oai_stream *s = oai_stream_new(16);
        oai_line ln;
        int i;
        for (i = 0; i < 100; ++i) oai_stream_push(s, OAI_LINE_INFO, "line %d", i);
        CHECK(oai_stream_total(s) == 100, "total %ld", oai_stream_total(s));
        CHECK(oai_stream_get(s, 99, &ln) == 1, "newest line missing");
        CHECK(strcmp(ln.text, "line 99") == 0, "newest line is \"%s\"", ln.text);
        CHECK(oai_stream_get(s, 0, &ln) == 0, "oldest line should have gone");
        CHECK(oai_stream_get(s, 84, &ln) == 1, "line 84 should still be held");
        CHECK(oai_stream_get(s, 1000, &ln) == 0, "future line returned");
        oai_stream_clear(s);
        CHECK(oai_stream_total(s) == 0, "clear");
        oai_stream_free(s);
    }

    TEST("long text is wrapped rather than truncated");
    {
        oai_stream *s = oai_stream_new(64);
        oai_line ln;
        long i, total;
        oai_stream_push_wrapped(s, OAI_LINE_SAMPLE,
            "one two three four five six seven eight nine ten eleven twelve "
            "thirteen fourteen fifteen sixteen", 20);
        total = oai_stream_total(s);
        CHECK(total > 3, "expected several wrapped lines, got %ld", total);
        for (i = 0; i < total; ++i) {
            CHECK(oai_stream_get(s, i, &ln) == 1, "line %ld", i);
            CHECK((int)strlen(ln.text) <= 20,
                  "line %ld is %d columns: \"%s\"", i, (int)strlen(ln.text),
                  ln.text);
        }
        oai_stream_free(s);
    }

    TEST("newlines split into separate lines and control bytes are tamed");
    {
        oai_stream *s = oai_stream_new(64);
        oai_line ln;
        oai_stream_push_wrapped(s, OAI_LINE_SAMPLE, "alpha\nbeta\x01gamma", 40);
        CHECK(oai_stream_total(s) == 2, "expected 2 lines, got %ld",
              oai_stream_total(s));
        oai_stream_get(s, 0, &ln);
        CHECK(strcmp(ln.text, "alpha") == 0, "first line \"%s\"", ln.text);
        oai_stream_get(s, 1, &ln);
        CHECK(strcmp(ln.text, "beta.gamma") == 0, "second line \"%s\"", ln.text);
        oai_stream_free(s);
    }

    TEST_MAIN_END();
}
