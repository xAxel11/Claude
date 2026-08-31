/* main.c -- Oai's entry point.
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#include "oai.h"
#include "oai_app.h"
#include "oai_ui.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef OAI_POSIX
#  include <unistd.h>
#endif

static int stdout_is_a_terminal(void)
{
#ifdef OAI_WINDOWS
    return 1;   /* the console API in oai_term_raw decides for us */
#else
    return isatty(STDOUT_FILENO) && isatty(STDIN_FILENO);
#endif
}

int main(int argc, char **argv)
{
    oai_config cfg;
    oai_app    app;
    int        rc;

    oai_config_defaults(&cfg);
    /* An oai.conf next to the binary is picked up before the command line, so
     * flags always win over the file. */
    oai_config_load_file(&cfg, "oai.conf");

    rc = oai_config_parse_args(&cfg, argc, argv);
    if (rc == 1) return 0;
    if (rc < 0)  return 2;

    oai_config_validate(&cfg);

    if (cfg.ui && !stdout_is_a_terminal()) cfg.ui = 0;
    if (!cfg.quiet && !cfg.ui) oai_print_banner();

    if (oai_app_init(&app, &cfg) != 0) {
        fprintf(stderr, "oai: failed to start\n");
        return 1;
    }

    /* Ctrl+C sets the quit flag instead of killing us, so the trainer gets to
     * checkpoint and the terminal gets restored. */
    oai_install_signal_handler(&app.quit);

    rc = cfg.ui ? oai_ui_run(&app) : oai_ui_run_plain(&app);

    oai_app_free(&app);
    return rc;
}
