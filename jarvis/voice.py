"""Speech output (free Microsoft neural voices via edge-tts) and microphone input."""
import asyncio
import os
import re
import tempfile
import threading
import time

from . import config


def clean_for_speech(text):
    text = re.sub(r"```.*?```", " I've put the code on screen. ", text, flags=re.S)
    text = re.sub(r"https?://\S+", "the link on screen", text)
    text = re.sub(r"[*_#`>|~]", "", text)
    return re.sub(r"\s+", " ", text).strip()


class Voice:
    def __init__(self):
        self.muted = False
        self._stop = threading.Event()
        self._mixer_ok = False
        try:
            import pygame

            pygame.mixer.init()
            self._pygame = pygame
            self._mixer_ok = True
        except Exception as err:  # noqa: BLE001
            print(f"[voice] pygame mixer unavailable ({err}); using offline voice")

    def stop(self):
        self._stop.set()
        if self._mixer_ok:
            self._pygame.mixer.music.stop()

    def speak(self, text):
        text = clean_for_speech(text)
        if self.muted or not text:
            return
        self._stop.clear()
        try:
            self._speak_edge(text)
        except Exception as err:  # noqa: BLE001
            print(f"[voice] edge-tts failed ({err}); using offline voice")
            try:
                self._speak_offline(text)
            except Exception as err2:  # noqa: BLE001
                print(f"[voice] offline voice failed too ({err2})")

    def _speak_edge(self, text):
        import edge_tts

        if not self._mixer_ok:
            raise RuntimeError("no audio mixer")
        fd, path = tempfile.mkstemp(suffix=".mp3", prefix="jarvis_")
        os.close(fd)
        try:
            communicate = edge_tts.Communicate(
                text, config.VOICE, rate=config.VOICE_RATE, pitch=config.VOICE_PITCH
            )
            asyncio.run(communicate.save(path))
            music = self._pygame.mixer.music
            music.load(path)
            music.play()
            while music.get_busy() and not self._stop.is_set():
                time.sleep(0.05)
            music.stop()
            music.unload()
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def _speak_offline(self, text):
        import pyttsx3

        engine = pyttsx3.init()
        engine.setProperty("rate", 180)
        engine.say(text)
        engine.runAndWait()


class Listener:
    """Microphone → text using the free Google Web Speech recogniser."""

    def __init__(self):
        import speech_recognition as sr

        self.sr = sr
        self.recognizer = sr.Recognizer()
        self.recognizer.dynamic_energy_threshold = True
        self.recognizer.pause_threshold = 0.8
        self.lock = threading.Lock()
        self._calibrated = False

    def listen(self, timeout=6, phrase_limit=15):
        """Return recognised text, or None on silence / not understood."""
        sr = self.sr
        with self.lock, sr.Microphone(device_index=config.MIC_INDEX) as source:
            if not self._calibrated:
                self.recognizer.adjust_for_ambient_noise(source, duration=0.8)
                self._calibrated = True
            try:
                audio = self.recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_limit)
            except sr.WaitTimeoutError:
                return None
        try:
            return self.recognizer.recognize_google(audio, language=config.LANGUAGE)
        except sr.UnknownValueError:
            return None
