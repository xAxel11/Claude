/* oai_app.c -- wiring: streams, backend selection and the trainer.
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#include "oai_app.h"
#include "oai_pool.h"

#include <stdio.h>
#include <string.h>

void oai_app_setup_backend(oai_app *app)
{
    int threads;

    /* The CPU pool is brought up either way: it does the work when there is no
     * GPU, and the shapes the GPU declines are still worth splitting. */
    threads = oai_pool_init(app->cfg.threads > 0 ? app->cfg.threads : -1);
    oai_stream_push(app->learning, OAI_LINE_INFO,
                    "cpu: %d worker thread%s across %d visible cores",
                    threads, threads == 1 ? "" : "s", oai_cpu_count());

    oai_gpu_probe(&app->gpu);

    if (app->cfg.backend == OAI_BACKEND_CPU) {
        oai_stream_push(app->learning, OAI_LINE_INFO,
                        "compute: CPU only (--backend cpu)");
        return;
    }

    if (oai_gpu_init(app->cfg.gpu_index, app->cfg.gpu_budget) == 0) {
        oai_gpu_get_info(&app->gpu);
        oai_stream_push(app->learning, OAI_LINE_EVENT,
            "compute: %s (%s)", app->gpu.device_name, app->gpu.vendor);
        oai_stream_push(app->learning, OAI_LINE_INFO,
            "gpu budget %.0f%% -- %d of %d compute units, up to %lu MB of "
            "%lu MB. %s",
            app->gpu.budget * 100.0f, app->gpu.compute_units_used,
            app->gpu.compute_units_total, app->gpu.budget_mem_mb,
            app->gpu.global_mem_mb,
            app->gpu.partitioned
              ? "Enforced by device fission: the rest of the device is untouched."
              : "Enforced by duty cycling: Oai yields the device between "
                "batches so other work keeps running.");
        return;
    }

    oai_gpu_get_info(&app->gpu);
    if (app->cfg.backend == OAI_BACKEND_GPU) {
        oai_stream_push(app->learning, OAI_LINE_WARN,
            "--backend gpu was requested but no GPU could be used: %s",
            app->gpu.status);
    }
    oai_stream_push(app->learning, OAI_LINE_INFO,
                    "compute: CPU. %s", app->gpu.status);
}

void oai_app_calibrate_backend(oai_app *app)
{
    const oai_net *net = &app->trainer.net;
    int kept;

    if (!oai_gpu_is_active()) return;

    /* The two shapes a forward pass actually runs. */
    oai_gpu_calibrate_shape(app->cfg.batch_size, net->input_dim, net->hidden);
    oai_gpu_calibrate_shape(app->cfg.batch_size, net->hidden, net->vocab_size);

    kept = oai_gpu_calibrate_finish(app->cfg.backend == OAI_BACKEND_GPU);
    oai_gpu_get_info(&app->gpu);

    if (!app->gpu.calibrated) return;

    if (kept && app->gpu.cal_gpu_ms > app->gpu.cal_cpu_ms) {
        /* --backend gpu overrode the measurement. Say so plainly rather than
         * reporting it as a win. */
        oai_stream_push(app->learning, OAI_LINE_WARN,
            "calibration: the GPU measured %.2f ms per step against %.2f ms "
            "on the CPU -- it is the slower of the two here, and is being "
            "used only because --backend gpu asked for it",
            app->gpu.cal_gpu_ms, app->gpu.cal_cpu_ms);
    } else if (kept) {
        oai_stream_push(app->learning, OAI_LINE_INFO,
            "calibration: this model's matmuls take %.2f ms on the GPU "
            "against %.2f ms on the CPU -- using the GPU",
            app->gpu.cal_gpu_ms, app->gpu.cal_cpu_ms);
    } else {
        oai_stream_push(app->learning, OAI_LINE_WARN, "%s", app->gpu.status);
        oai_stream_push(app->learning, OAI_LINE_INFO,
            "a model this small spends most of a GPU call waiting on the bus. "
            "Try --hidden 1024 --batch 256 to give the device something worth "
            "the trip, or --backend gpu to use it anyway.");
    }
}

int oai_app_init(oai_app *app, const oai_config *cfg)
{
    memset(app, 0, sizeof *app);
    app->cfg = *cfg;

    app->learning = oai_stream_new(4096);
    app->chat     = oai_stream_new(1024);
    if (!app->learning || !app->chat) {
        oai_app_free(app);
        return -1;
    }
    if (cfg->log_path[0])
        oai_stream_mirror_to_file(app->learning, cfg->log_path);

    oai_stream_push(app->learning, OAI_LINE_EVENT,
                    "Oai %s starting", OAI_VERSION_STRING);

    oai_app_setup_backend(app);

    if (oai_trainer_init(&app->trainer, &app->cfg, app->learning) != 0) {
        oai_app_free(app);
        return -1;
    }
    app->trainer.feed_width = &app->feed_width;
    oai_app_calibrate_backend(app);
    app->ready = 1;
    return 0;
}

void oai_app_free(oai_app *app)
{
    if (!app) return;
    if (app->ready) oai_trainer_free(&app->trainer);
    oai_gpu_shutdown();
    oai_pool_shutdown();
    oai_stream_free(app->learning);
    oai_stream_free(app->chat);
    app->learning = app->chat = NULL;
    app->ready = 0;
}
