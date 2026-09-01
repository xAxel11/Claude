/* oai.h -- Oai core types, version and run configuration.
 *
 * Oai is a small open-source AI agent written in C. It trains a character
 * level neural language model on a text corpus, streams what it is learning
 * to a live terminal UI, answers in a chat box, and can be cancelled at any
 * moment without losing progress.
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#ifndef OAI_H
#define OAI_H

#include <stddef.h>
#include "oai_platform.h"

#define OAI_VERSION_MAJOR 1
#define OAI_VERSION_MINOR 0
#define OAI_VERSION_PATCH 1
#define OAI_VERSION_STRING "1.0.1"

#ifdef __cplusplus
extern "C" {
#endif

/* Which compute backend the trainer should try to use. */
typedef enum {
    OAI_BACKEND_AUTO = 0,  /* GPU when one is usable, otherwise CPU */
    OAI_BACKEND_CPU  = 1,  /* multi-threaded CPU only */
    OAI_BACKEND_GPU  = 2   /* require a GPU; fail loudly if there is none */
} oai_backend;

/* Everything the user can tune from the command line or oai.conf. */
typedef struct {
    /* data */
    char   corpus_path[512];
    char   checkpoint_path[512];
    char   log_path[512];

    /* model */
    int    context;        /* characters of history fed to the model */
    int    embed_dim;      /* size of each character embedding */
    int    hidden;         /* hidden units */

    /* optimisation */
    int    batch_size;
    float  learning_rate;
    float  weight_decay;
    float  grad_clip;
    int    max_steps;      /* 0 = train until cancelled */
    int    seed;

    /* compute */
    oai_backend backend;
    float  gpu_budget;     /* 0.05 .. 1.0 -- share of the GPU Oai may use */
    int    gpu_index;      /* which GPU when several are present */
    int    threads;        /* CPU worker threads; 0 = auto */

    /* interface */
    int    ui;             /* 1 = full-screen UI, 0 = plain stdout */
    int    sample_every;   /* steps between "what it learned" samples */
    int    sample_len;     /* characters per sample */
    float  temperature;    /* sampling temperature */
    int    autostart;      /* begin training as soon as Oai opens */
    int    quiet;
} oai_config;

/* Fills cfg with the defaults documented in README.md. */
void oai_config_defaults(oai_config *cfg);
/* Parses argv. Returns 0 on success, 1 when it printed help, -1 on error. */
int  oai_config_parse_args(oai_config *cfg, int argc, char **argv);
/* Loads key=value lines from a config file. Missing file is not an error. */
int  oai_config_load_file(oai_config *cfg, const char *path);
/* Clamps every field to a sane range and reports what it had to change. */
void oai_config_validate(oai_config *cfg);
void oai_print_usage(const char *argv0);
void oai_print_banner(void);

#ifdef __cplusplus
}
#endif
#endif /* OAI_H */
