"""Glue between the HUD, the brain, speech and the microphone."""
import datetime
import queue
import threading
import time

from . import config
from .bridge import bridge
from .brain import Brain
from .tools import memory
from .voice import Listener, Voice


class Jarvis:
    def __init__(self):
        self.brain = Brain()
        self.voice = Voice()
        memory._speak = self.say
        try:
            self.listener = Listener()
        except Exception as err:  # noqa: BLE001
            self.listener = None
            bridge.log(f"Microphone unavailable: {err}", "system")
        self.inbox = queue.Queue()
        self.busy = threading.Event()
        self.wake_enabled = False
        threading.Thread(target=self._worker, daemon=True).start()

    # ------------------------------------------------------------ output
    def say(self, text):
        bridge.set_state("speaking")
        self.voice.speak(text)
        bridge.set_state("idle")

    def greet(self):
        hour = datetime.datetime.now().hour
        part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
        msg = f"Good {part}, {config.USER_TITLE}. All systems are online."
        bridge.log(msg, "jarvis")
        threading.Thread(target=self.say, args=(msg,), daemon=True).start()

    # ------------------------------------------------------------ input
    def submit(self, text):
        text = text.strip()
        if text:
            bridge.log(text, "you")
            self.busy.set()  # set now so the wake-word loop pauses immediately
            self.inbox.put(text)

    def _worker(self):
        while True:
            text = self.inbox.get()
            self.busy.set()
            try:
                bridge.set_state("thinking")
                reply = self.brain.ask(text)
                bridge.log(reply, "jarvis")
                self.say(reply)
            finally:
                bridge.set_state("idle")
                self.busy.clear()

    def stop_speaking(self):
        self.voice.stop()

    def listen_once(self):
        """Push-to-talk: record one phrase and submit it."""
        if not self.listener:
            bridge.log("No microphone available.", "system")
            return

        def run():
            self.voice.stop()
            bridge.set_state("listening")
            try:
                text = self.listener.listen()
            except Exception as err:  # noqa: BLE001
                bridge.log(f"Mic error: {err}", "system")
                text = None
            bridge.set_state("idle")
            if text:
                self.submit(text)
            else:
                bridge.log("Didn't catch that.", "system")

        threading.Thread(target=run, daemon=True).start()

    def set_wake_word(self, enabled):
        if not self.listener:
            bridge.log("No microphone available.", "system")
            return
        was_enabled, self.wake_enabled = self.wake_enabled, enabled
        if enabled and not was_enabled:
            bridge.log(f'Wake word on — say "{config.WAKE_WORD.title()}, ..."', "system")
            threading.Thread(target=self._wake_loop, daemon=True).start()

    def _wake_loop(self):
        word = config.WAKE_WORD
        while self.wake_enabled:
            if self.busy.is_set():  # don't hear ourselves talk
                time.sleep(0.2)
                continue
            try:
                heard = self.listener.listen(timeout=4, phrase_limit=12)
            except Exception as err:  # noqa: BLE001
                bridge.log(f"Mic error: {err}", "system")
                time.sleep(2)
                continue
            if not heard or word not in heard.lower() or not self.wake_enabled:
                continue
            command = heard.lower().split(word, 1)[1].strip(" ,.!?")
            if not command:
                self.say(f"Yes, {config.USER_TITLE}?")
                bridge.set_state("listening")
                command = self.listener.listen(timeout=6) or ""
                bridge.set_state("idle")
            if command:
                self.submit(command)
