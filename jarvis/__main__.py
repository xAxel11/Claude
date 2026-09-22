import sys

from . import config


def main():
    if not config.GEMINI_API_KEY or config.GEMINI_API_KEY.startswith("paste-"):
        print(
            "No Gemini API key found.\n"
            "Copy .env.example to .env and set GEMINI_API_KEY "
            "(free key: https://aistudio.google.com/apikey)."
        )
        sys.exit(1)

    from .gui import JarvisGUI

    JarvisGUI().mainloop()


if __name__ == "__main__":
    main()
