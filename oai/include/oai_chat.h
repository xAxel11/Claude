/* oai_chat.h -- the chat box: commands plus model-generated replies.
 *
 * Anything that starts with a known verb is a command ("train", "stop", "lr
 * 0.02"). Anything else is handed to the model, which continues it -- so the
 * quality of the reply is a direct read-out of how far training has got.
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#ifndef OAI_CHAT_H
#define OAI_CHAT_H

#include "oai_app.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Handles one line from the user, appending both sides to app->chat. */
void oai_chat_submit(oai_app *app, const char *text);
/* The greeting shown when Oai opens. */
void oai_chat_greet(oai_app *app);

#ifdef __cplusplus
}
#endif
#endif /* OAI_CHAT_H */
