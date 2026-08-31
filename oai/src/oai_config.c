/* oai_config.c -- defaults, the command line and oai.conf.
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#include "oai.h"
#include "oai_gpu.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void oai_config_defaults(oai_config *cfg)
{
    memset(cfg, 0, sizeof *cfg);
    snprintf(cfg->corpus_path, sizeof cfg->corpus_path, "data/corpus.txt");
    snprintf(cfg->checkpoint_path, sizeof cfg->checkpoint_path,
             "oai-checkpoint.bin");
    cfg->log_path[0] = '\0';

    cfg->context       = 12;
    cfg->embed_dim     = 24;
    cfg->hidden        = 256;

    cfg->batch_size    = 64;
    cfg->learning_rate = 0.003f;
    cfg->weight_decay  = 0.0001f;
    cfg->grad_clip     = 5.0f;
    cfg->max_steps     = 0;      /* until cancelled */
    cfg->seed          = 1337;

    cfg->backend    = OAI_BACKEND_AUTO;
    cfg->gpu_budget = 0.5f;      /* half the GPU, never all of it */
    cfg->gpu_index  = 0;
    cfg->threads    = 0;

    cfg->ui           = 1;
    cfg->sample_every = 100;
    cfg->sample_len   = 180;
    cfg->temperature  = 0.8f;
    cfg->autostart    = 0;
    cfg->quiet        = 0;
}

void oai_config_validate(oai_config *cfg)
{
    if (cfg->context   < 2)    cfg->context = 2;
    if (cfg->context   > 64)   cfg->context = 64;
    if (cfg->embed_dim < 2)    cfg->embed_dim = 2;
    if (cfg->embed_dim > 256)  cfg->embed_dim = 256;
    if (cfg->hidden    < 8)    cfg->hidden = 8;
    if (cfg->hidden    > 4096) cfg->hidden = 4096;
    if (cfg->batch_size < 1)   cfg->batch_size = 1;
    if (cfg->batch_size > 4096) cfg->batch_size = 4096;
    if (cfg->learning_rate <= 0.0f) cfg->learning_rate = 0.003f;
    if (cfg->learning_rate > 1.0f)  cfg->learning_rate = 1.0f;
    if (cfg->weight_decay < 0.0f)   cfg->weight_decay = 0.0f;
    if (cfg->grad_clip < 0.0f)      cfg->grad_clip = 0.0f;
    if (cfg->max_steps < 0)         cfg->max_steps = 0;
    if (cfg->gpu_budget < 0.05f)    cfg->gpu_budget = 0.05f;
    if (cfg->gpu_budget > 1.0f)     cfg->gpu_budget = 1.0f;
    if (cfg->gpu_index < 0)         cfg->gpu_index = 0;
    if (cfg->sample_every < 0)      cfg->sample_every = 0;
    if (cfg->sample_len < 16)       cfg->sample_len = 16;
    if (cfg->sample_len > 512)      cfg->sample_len = 512;
    if (cfg->temperature < 0.05f)   cfg->temperature = 0.05f;
    if (cfg->temperature > 3.0f)    cfg->temperature = 3.0f;
    if (cfg->threads < 0)           cfg->threads = 0;
}

void oai_print_banner(void)
{
    printf(
"   ___    _ \n"
"  / _ \\  /_\\  (_)   Oai %s\n"
" | (_) |/ _ \\  | |   an open-source agent that learns to write, in C\n"
"  \\___//_/ \\_\\ |_|   MIT licensed\n\n", OAI_VERSION_STRING);
}

void oai_print_usage(const char *argv0)
{
    printf(
"Usage: %s [options]\n"
"\n"
"Oai trains a character-level neural language model and shows you what it is\n"
"learning while it does. Training runs on a worker thread and can be cancelled\n"
"at any moment; the weights are always checkpointed on the way out.\n"
"\n"
"Data\n"
"  --corpus <file>       text to learn from (default data/corpus.txt)\n"
"  --checkpoint <file>   where to save and resume from\n"
"                        (default oai-checkpoint.bin)\n"
"  --log <file>          also append the learning feed to this file\n"
"\n"
"Model\n"
"  --context <n>         characters of history per prediction (default 12)\n"
"  --embed <n>           embedding width (default 24)\n"
"  --hidden <n>          hidden units (default 256)\n"
"\n"
"Training\n"
"  --batch <n>           examples per step (default 64)\n"
"  --lr <rate>           learning rate (default 0.003)\n"
"  --weight-decay <w>    decoupled weight decay (default 0.0001)\n"
"  --clip <norm>         gradient-norm clip, 0 disables (default 5)\n"
"  --steps <n>           stop after n steps; 0 means until cancelled\n"
"  --seed <n>            random seed (default 1337)\n"
"  --train               start training immediately on launch\n"
"\n"
"Compute\n"
"  --backend <auto|cpu|gpu>   default auto\n"
"  --gpu-budget <0.05-1.0>    share of the GPU Oai may use (default 0.5)\n"
"  --gpu <index>              which GPU to use (default 0)\n"
"  --threads <n>              CPU worker threads, 0 = auto\n"
"  --list-devices             print the OpenCL devices and exit\n"
"\n"
"Interface\n"
"  --no-ui               plain stdout/stdin instead of the full-screen UI\n"
"  --sample-every <n>    steps between generated samples (default 100)\n"
"  --sample-len <n>      characters per sample (default 180)\n"
"  --temp <t>            sampling temperature (default 0.8)\n"
"  --quiet               suppress the banner\n"
"\n"
"Other\n"
"  --config <file>       read key=value settings from a file\n"
"  --version             print the version and exit\n"
"  --help                print this text\n"
"\n"
"Examples\n"
"  %s --corpus mytext.txt --train\n"
"  %s --gpu-budget 0.25 --backend gpu --train\n"
"  %s --no-ui --steps 2000 --train\n"
"\n", argv0, argv0, argv0, argv0);
}

/* Applies one key=value pair from either the command line or a config file. */
static int apply_setting(oai_config *cfg, const char *key, const char *value)
{
    if (!value) return -1;

#define SET_STR(name, field)                                                  \
    if (strcmp(key, name) == 0) {                                             \
        snprintf(cfg->field, sizeof cfg->field, "%s", value);                 \
        return 0;                                                             \
    }
#define SET_INT(name, field)                                                  \
    if (strcmp(key, name) == 0) { cfg->field = atoi(value); return 0; }
#define SET_FLT(name, field)                                                  \
    if (strcmp(key, name) == 0) { cfg->field = (float)atof(value); return 0; }

    SET_STR("corpus", corpus_path)
    SET_STR("checkpoint", checkpoint_path)
    SET_STR("log", log_path)
    SET_INT("context", context)
    SET_INT("embed", embed_dim)
    SET_INT("hidden", hidden)
    SET_INT("batch", batch_size)
    SET_FLT("lr", learning_rate)
    SET_FLT("weight-decay", weight_decay)
    SET_FLT("clip", grad_clip)
    SET_INT("steps", max_steps)
    SET_INT("seed", seed)
    SET_FLT("gpu-budget", gpu_budget)
    SET_INT("gpu", gpu_index)
    SET_INT("threads", threads)
    SET_INT("sample-every", sample_every)
    SET_INT("sample-len", sample_len)
    SET_FLT("temp", temperature)

#undef SET_STR
#undef SET_INT
#undef SET_FLT

    if (strcmp(key, "backend") == 0) {
        if (strcmp(value, "cpu") == 0)      cfg->backend = OAI_BACKEND_CPU;
        else if (strcmp(value, "gpu") == 0) cfg->backend = OAI_BACKEND_GPU;
        else                                cfg->backend = OAI_BACKEND_AUTO;
        return 0;
    }
    return -1;
}

int oai_config_load_file(oai_config *cfg, const char *path)
{
    FILE *f;
    char  line[1024];

    if (!path || !path[0]) return 0;
    f = fopen(path, "r");
    if (!f) return 0;    /* absent config is fine */

    while (fgets(line, sizeof line, f)) {
        char *key = line, *value, *end;
        while (*key == ' ' || *key == '\t') key++;
        if (*key == '#' || *key == ';' || *key == '\n' || *key == '\0') continue;
        value = strchr(key, '=');
        if (!value) continue;
        *value++ = '\0';
        end = key + strlen(key);
        while (end > key && isspace((unsigned char)end[-1])) *--end = '\0';
        while (*value == ' ' || *value == '\t') value++;
        end = value + strlen(value);
        while (end > value && isspace((unsigned char)end[-1])) *--end = '\0';

        if (strcmp(key, "ui") == 0)        { cfg->ui = atoi(value); continue; }
        if (strcmp(key, "train") == 0)     { cfg->autostart = atoi(value); continue; }
        if (apply_setting(cfg, key, value) != 0)
            fprintf(stderr, "oai: unknown setting \"%s\" in %s\n", key, path);
    }
    fclose(f);
    return 0;
}

int oai_config_parse_args(oai_config *cfg, int argc, char **argv)
{
    int i;
    for (i = 1; i < argc; ++i) {
        const char *a = argv[i];
        const char *next = (i + 1 < argc) ? argv[i + 1] : NULL;

        if (strcmp(a, "--help") == 0 || strcmp(a, "-h") == 0) {
            oai_print_usage(argv[0]);
            return 1;
        }
        if (strcmp(a, "--version") == 0) {
            printf("oai %s\n", OAI_VERSION_STRING);
            return 1;
        }
        if (strcmp(a, "--list-devices") == 0) {
            char buf[2048];
            oai_gpu_list_devices(buf, sizeof buf);
            printf("OpenCL devices visible to Oai:\n%s", buf);
            return 1;
        }
        if (strcmp(a, "--no-ui") == 0)  { cfg->ui = 0; continue; }
        if (strcmp(a, "--train") == 0)  { cfg->autostart = 1; continue; }
        if (strcmp(a, "--quiet") == 0)  { cfg->quiet = 1; continue; }
        if (strcmp(a, "--config") == 0) {
            if (!next) { fprintf(stderr, "oai: --config needs a path\n");
                         return -1; }
            oai_config_load_file(cfg, next);
            i++;
            continue;
        }

        if (a[0] == '-' && a[1] == '-') {
            if (!next) {
                fprintf(stderr, "oai: %s needs a value\n", a);
                return -1;
            }
            if (apply_setting(cfg, a + 2, next) == 0) { i++; continue; }
            fprintf(stderr, "oai: unknown option %s (try --help)\n", a);
            return -1;
        }
        fprintf(stderr, "oai: unexpected argument \"%s\" (try --help)\n", a);
        return -1;
    }
    return 0;
}
