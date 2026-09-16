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

  # 讀取現有持倉紀錄 (包含昨天或過去保留的標的)
  positions = {}
  if os.path.exists(POSITIONS_FILE):
    try:
      with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
        positions = json.load(f)
      print(f"📖 讀取到歷史持倉共 {len(positions)} 檔標的")
    except Exception as e:
      print(f"⚠️ 讀取 {POSITIONS_FILE} 失敗: {e}")
      positions = {}

  actions_today = []
  closed_positions = []
  updated_positions = {}

  # 建立新標的名稱對照表
  target_names = {t["symbol"]: t.get("name", "") for t in new_targets}

  # ----------------------------------------------------
  # 階段 1：追蹤與更新【既有持倉】 (判斷是否觸發出場)
  # ----------------------------------------------------
  for symbol, pos in positions.items():
    name = pos.get("name") or target_names.get(symbol, "")
    display_title = f"{symbol} {name}".strip()

    ticker_str = f"{symbol}.TW"
    mtf_data = get_stock_mtf_data(ticker_str)
    if not mtf_data:
      ticker_str = f"{symbol}.TWO"
      mtf_data = get_stock_mtf_data(ticker_str)

    # 抓不到最新 K 線時維持原持倉資料
    if not mtf_data or mtf_data["1d"] is None:
      pos["name"] = name
      updated_positions[symbol] = pos
      continue

    current_price = round(mtf_data["1d"]["Close"].iloc[-1], 2)
    entry_price = pos["entry_price"]
    return_pct = (current_price - entry_price) / entry_price * 100

    is_exit, exit_reason = check_exit_condition(mtf_data)

    if is_exit:
      # 觸發平倉出場
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
      # 未觸發出場，繼續續抱並更新現價與報酬率
      pos["name"] = name
      pos["current_price"] = current_price
      pos["return_pct"] = round(return_pct, 2)
      updated_positions[symbol] = pos  # 確保留在持倉名單中

  # ----------------------------------------------------
  # 階段 2：處理【今天全新篩選出來】的標的建倉
  # ----------------------------------------------------
  for target in new_targets:
    symbol = target["symbol"]
    name = target.get("name", "")
    display_title = f"{symbol} {name}".strip()

    # 只針對「目前不在持倉中」的標的進行建倉
    if symbol not in updated_positions and symbol not in positions:
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

  # ----------------------------------------------------
  # 階段 3：寫回 positions.json 與歷史檔
  # ----------------------------------------------------
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

  # ----------------------------------------------------
  # 階段 4：發送 Telegram 整合報表
  # ----------------------------------------------------
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
