import sys


def main():
    from . import ai

    if not ai.configured():
        print(
            "No AI API key found.\n"
            "Copy .env.example to .env and set GEMINI_API_KEY (free: https://aistudio.google.com/apikey)\n"
            "and/or OPENAI_API_KEY (https://platform.openai.com/api-keys)."
        )
        sys.exit(1)

    if sys.platform == "win32":
        # Use real pixels everywhere so screenshots, mouse moves and the HUD agree on high-DPI screens.
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:  # noqa: BLE001
            ctypes.windll.user32.SetProcessDPIAware()

    from .gui import JarvisGUI

    JarvisGUI().mainloop()


if __name__ == "__main__":
    main()
