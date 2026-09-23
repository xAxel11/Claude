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

    from .gui import JarvisGUI

    JarvisGUI().mainloop()


if __name__ == "__main__":
    main()
