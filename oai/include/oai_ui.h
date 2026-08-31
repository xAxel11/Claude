/* oai_ui.h -- the full-screen terminal interface.
 *
 * The screen is a grid of cells held in two buffers. Each frame is drawn into
 * the back buffer and then diffed against the front buffer, so only the cells
 * that actually changed are written to the terminal. That is what keeps the
 * scrolling feed smooth instead of flickering, and it costs almost nothing
 * when the screen is mostly static.
 *
 * Layout:
 *
 *   +----------------------------------------------------------------+
 *   | Oai 1.0.0                                    training  step 420 |
 *   | loss / perplexity / throughput / device / budget                |
 *   | loss sparkline .......................... progress              |
 *   +------------------------------+---------------------------------+
 *   | LEARNING                     | CHAT                            |
 *   |  live feed of losses,        |  you: train                     |
 *   |  samples and insights        |  oai: Training. Watch the...    |
 *   |                              +---------------------------------+
 *   |                              | > _                             |
 *   +------------------------------+---------------------------------+
 *   | [t] train  [s] stop  [p] pause  [Tab] focus  [q] quit           |
 *   +----------------------------------------------------------------+
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#ifndef OAI_UI_H
#define OAI_UI_H

#include "oai_app.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Runs the interface until the user quits. Returns the process exit code. */
int oai_ui_run(oai_app *app);

/* The headless alternative used by --no-ui, pipes and CI: prints the learning
 * feed to stdout and reads commands from stdin a line at a time. */
int oai_ui_run_plain(oai_app *app);

#ifdef __cplusplus
}
#endif
#endif /* OAI_UI_H */
