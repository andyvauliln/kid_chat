 ## Telegram AI assistant (kid-safe) — minimal setup + integration test
 
 This repo contains:
 - A minimal Telegram bot (currently **echoes**; placeholder for AI logic).
 - An integration test that **sends a message to the bot and waits for the bot's reply**.
 
### Why Bot API (python-telegram-bot)?
This project uses **Bot API** via `python-telegram-bot`:
- Simple, official bot interface (token-based)
- Easy to run with long polling
 
 
 ### Install
 
 ```bash
 python -m venv .venv
 source .venv/bin/activate
 pip install -U pip
 pip install -e ".[dev]"
 ```
 
 ### Run the bot (echo placeholder)
 
 ```bash
 source .venv/bin/activate
 python -m group_chat_telegram_ai.bot
 ```
 
