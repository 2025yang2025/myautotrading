import os
from autotrading import run_autotrading
from strategy import run_strategy


def main():
  bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
  chat_id = os.getenv("TELEGRAM_CHAT_ID")

  # 1. 執行全台股自動篩選
  targets = run_autotrading()

  # 2. 執行持倉與出場判斷，發送 Telegram 訊息
  run_strategy(targets, bot_token, chat_id)


if __name__ == "__main__":
  main()
