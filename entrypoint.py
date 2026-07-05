#!/usr/bin/env python3
import os, threading, time, sys

def run_app():
    import app as webapp
    webapp.main()

def run_bot():
    import telegram_bot as tb
    tb.main()

def main():
    # Start Web UI
    t1 = threading.Thread(target=run_app, daemon=True)
    t1.start()

    # Start Telegram bot if token is set
    if os.getenv("TG_BOT_TOKEN"):
        t2 = threading.Thread(target=run_bot, daemon=True)
        t2.start()
    else:
        print("TG_BOT_TOKEN not set; Telegram bot disabled. Web UI only.")

    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        sys.exit(0)

if __name__ == "__main__":
    main()