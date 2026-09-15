import json
import os
from datetime import datetime
from autotrading import get_stock_mtf_data
import pandas as pd
import requests

POSITIONS_FILE = "positions.json"
HISTORY_FILE = "history.csv"


def send_telegram(message, bot_token, chat_id):
  if not bot_token or not chat_id:
    print("⚠️ Telegram Token 或 Chat ID 未設定，跳過發送。")
    return
  url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
  payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
  try:
    requests.post(url, json=payload, timeout=10)
  except Exception as e:
    print(f"Telegram 發送失敗: {e}")


def check_exit_condition(mtf):
  """判斷出場條件：月/週 MACD<0 & KD<70；日/60m/30m MACD趨向0軸往下 & KD<70"""
  if not mtf or any(v is None for v in mtf.values()):
    return False, ""

  try:
    m_hist = mtf["1m"]["macd_hist"].iloc[-1]
    m_k = mtf["1m"]["k"].iloc[-1]
    cond_1m = (m_hist < 0) and (m_k < 70)

    w_hist = mtf["1w"]["macd_hist"].iloc[-1]
    w_k = mtf["1w"]["k"].iloc[-1]
    cond_1w = (w_hist < 0) and (w_k < 70)

    d_hist = mtf["1d"]["macd_hist"].iloc[-1]
    d_hist_prev = mtf["1d"]["macd_hist"].iloc[-2]
    d_k = mtf["1d"]["k"].iloc[-1]
    cond_1d = (d_hist < d_hist_prev) and (d_k < 70)

    h1_hist = mtf["60m"]["macd_hist"].iloc[-1]
    h1_hist_prev = mtf["60m"]["macd_hist"].iloc[-2]
    h1_k = mtf["60m"]["k"].iloc[-1]
    cond_60m = (h1_hist < h1_hist_prev) and (h1_k < 70)

    m30_hist = mtf["30m"]["macd_hist"].iloc[-1]
    m30_hist_prev = mtf["30m"]["macd_hist"].iloc[-2]
    m30_k = mtf["30m"]["k"].iloc[-1]
    cond_30m = (m30_hist < m30_hist_prev) and (m30_k < 70)

    if cond_1m and cond_1w and cond_1d and cond_60m and cond_30m:
      return True, "📉 觸發多週期出場條件 (MACD向下 & KD<70)"

    return False, ""
  except Exception:
    return False, ""


def run_strategy(new_targets, bot_token, chat_id):
  """持倉管理與平倉追蹤主邏輯"""
  print("📈 開始執行【持倉管理與出場判斷】...")
  today_str = datetime.now().strftime("%Y-%m-%d")

  if os.path.exists(POSITIONS_FILE):
    with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
      positions = json.load(f)
  else:
    positions = {}

  actions_today = []
  closed_positions = []
  updated_positions = {}

  # 建立搜尋字典以利補充名稱
  target_names = {t["symbol"]: t.get("name", "") for t in new_targets}

  # 1. 檢查既有持倉出場條件
  for symbol, pos in positions.items():
    name = pos.get("name") or target_names.get(symbol, "")
    display_title = f"{symbol} {name}".strip()

    ticker_str = f"{symbol}.TW"
    mtf_data = get_stock_mtf_data(ticker_str)
    if not mtf_data:
      ticker_str = f"{symbol}.TWO"
      mtf_data = get_stock_mtf_data(ticker_str)

    if not mtf_data or mtf_data["1d"] is None:
      pos["name"] = name
      updated_positions[symbol] = pos
      continue

    current_price = round(mtf_data["1d"]["Close"].iloc[-1], 2)
    entry_price = pos["entry_price"]
    return_pct = (current_price - entry_price) / entry_price * 100

    is_exit, exit_reason = check_exit_condition(mtf_data)

    if is_exit:
      closed_positions.append({
          "symbol": symbol,
          "name": name,
          "entry_date": pos["entry_date"],
          "exit_date": today_str,
          "entry_price": entry_price,
          "exit_price": current_price,
          "return_pct": round(return_pct, 2),
          "reason": exit_reason,
      })
      actions_today.append(
          f"🔴 *【出場】* `{display_title}` @ {current_price} ({exit_reason},"
          f" 獲利: {return_pct:+.2f}%)"
      )
    else:
      pos["name"] = name
      pos["current_price"] = current_price
      pos["return_pct"] = round(return_pct, 2)
      updated_positions[symbol] = pos

  # 2. 處理新篩選標的建倉
  for target in new_targets:
    symbol = target["symbol"]
    name = target.get("name", "")
    display_title = f"{symbol} {name}".strip()

    if symbol not in updated_positions:
      ticker_str = f"{symbol}.TW"
      mtf_data = get_stock_mtf_data(ticker_str)
      if not mtf_data:
        ticker_str = f"{symbol}.TWO"
        mtf_data = get_stock_mtf_data(ticker_str)

      if mtf_data and mtf_data["1d"] is not None:
        price = round(mtf_data["1d"]["Close"].iloc[-1], 2)
        updated_positions[symbol] = {
            "name": name,
            "entry_date": today_str,
            "entry_price": price,
            "current_price": price,
            "return_pct": 0.0,
        }
        actions_today.append(f"🟢 *【建倉】* `{display_title}` @ {price}")

  # 3. 儲存檔案
  with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
    json.dump(updated_positions, f, ensure_ascii=False, indent=2)

  if closed_positions:
    df_new = pd.DataFrame(closed_positions)
    if os.path.exists(HISTORY_FILE):
      df_old = pd.read_csv(HISTORY_FILE)
      df_all = pd.concat([df_old, df_new], ignore_index=True)
    else:
      df_all = df_new
    df_all.to_csv(HISTORY_FILE, index=False, encoding="utf-8-sig")

  # 4. Telegram 推播
  report = [f"📊 *【台股自動化策略總報表】* ({today_str})\n"]
  if actions_today:
    report.append("*今日異動事項：*")
    report.extend(actions_today)
    report.append("")

  report.append(f"*當前持倉個數：* {len(updated_positions)} 檔")
  for sym, p in updated_positions.items():
    p_name = p.get("name", "")
    disp = f"{sym} {p_name}".strip()
    report.append(
        f"• `{disp}` | 進場: {p['entry_price']} | 現價: {p['current_price']} |"
        f" 報酬: {p['return_pct']:+.2f}%"
    )

  send_telegram("\n".join(report), bot_token, chat_id)
