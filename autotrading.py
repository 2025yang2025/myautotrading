import json
import os
import re
import pandas as pd
import requests
import ta
import yfinance as yf


def get_all_tw_stocks():
  """動態抓取台灣上市與上櫃全股票代碼 (排除 ETF、權證、特種股)"""
  print("🌐 正在取得全台股上市與上櫃股票清單...")
  stock_list = []

  # 1. 上市股票 (TWSE)
  try:
    url_twse = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
    res = requests.get(url_twse, timeout=10)
    df_twse = pd.read_html(res.text)[0]
    df_twse.columns = df_twse.iloc[0]
    df_twse = df_twse.iloc[1:]

    for row in df_twse["有價證券代號及名稱"].dropna():
      match = re.match(r"^(\d{4})\s+(.+)", str(row))
      if match:
        stock_list.append(f"{match.group(1)}.TW")
  except Exception as e:
    print(f"⚠️ 抓取上市股票清單失敗: {e}")

  # 2. 上櫃股票 (TPEx)
  try:
    url_tpex = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4"
    res = requests.get(url_tpex, timeout=10)
    df_tpex = pd.read_html(res.text)[0]
    df_tpex.columns = df_tpex.iloc[0]
    df_tpex = df_tpex.iloc[1:]

    for row in df_tpex["有價證券代號及名稱"].dropna():
      match = re.match(r"^(\d{4})\s+(.+)", str(row))
      if match:
        stock_list.append(f"{match.group(1)}.TWO")
  except Exception as e:
    print(f"⚠️ 抓取上櫃股票清單失敗: {e}")

  stock_list = sorted(list(set(stock_list)))
  print(f"✅ 成功取得 {len(stock_list)} 檔台股標的！")
  return stock_list


def calculate_indicators(df):
  """計算 MACD (12,26,9) 與 KD (9,3,3)"""
  if df is None or len(df) < 30:
    return None

  try:
    stoch = ta.momentum.StochasticOscillator(
        high=df["High"], low=df["Low"], close=df["Close"], window=9, smooth_window=3
    )
    df["k"] = stoch.stoch()
    df["d"] = stoch.stoch_signal()

    macd = ta.trend.MACD(
        close=df["Close"], window_slow=26, window_fast=12, window_sign=9
    )
    df["macd_hist"] = macd.macd_diff()

    return df
  except Exception:
    return None


def get_stock_mtf_data(ticker_str):
  """抓取單一股票多週期 K 線，並過濾月均量低於 1,000 張標的"""
  try:
    # 1. 抓取日線做第一道快速過濾
    df_1d = yf.download(
        ticker_str, period="1y", interval="1d", progress=False
    )
    if df_1d.empty or len(df_1d) < 20:
      return None

    if isinstance(df_1d.columns, pd.MultiIndex):
      df_1d.columns = df_1d.columns.get_level_values(0)

    # 過濾：近 20 個交易日日均量低於 1,000 張 (1,000,000 股) 直接跳過
    monthly_avg_volume = df_1d["Volume"].iloc[-20:].mean()
    if monthly_avg_volume < 1000000:
      return None

    # 2. 下載其餘週期
    df_30m = yf.download(
        ticker_str, period="1mo", interval="30m", progress=False
    )
    if df_30m.empty or len(df_30m) < 30:
      return None

    df_60m = yf.download(
        ticker_str, period="2mo", interval="60m", progress=False
    )
    df_1w = yf.download(
        ticker_str, period="2y", interval="1wk", progress=False
    )
    df_1m = yf.download(
        ticker_str, period="5y", interval="1mo", progress=False
    )

    for df in [df_30m, df_60m, df_1w, df_1m]:
      if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    return {
        "30m": calculate_indicators(df_30m),
        "60m": calculate_indicators(df_60m),
        "1d": calculate_indicators(df_1d),
        "1w": calculate_indicators(df_1w),
        "1m": calculate_indicators(df_1m),
    }
  except Exception:
    return None


def check_entry_condition(mtf):
  """判斷進場條件：月/週 MACD>0 & KD>20；日/60m/30m MACD趨向0軸往上 & KD>20"""
  if not mtf or any(v is None for v in mtf.values()):
    return False

  try:
    # 1. 月線 (1M): MACD柱狀體 > 0 & K > 20
    m_hist = mtf["1m"]["macd_hist"].iloc[-1]
    m_k = mtf["1m"]["k"].iloc[-1]
    cond_1m = (m_hist > 0) and (m_k > 20)

    # 2. 週線 (1W): MACD柱狀體 > 0 & K > 20
    w_hist = mtf["1w"]["macd_hist"].iloc[-1]
    w_k = mtf["1w"]["k"].iloc[-1]
    cond_1w = (w_hist > 0) and (w_k > 20)

    # 3. 日線 (1D): MACD柱狀體趨向0軸往上 (今日 > 前一日) & K > 20
    d_hist = mtf["1d"]["macd_hist"].iloc[-1]
    d_hist_prev = mtf["1d"]["macd_hist"].iloc[-2]
    d_k = mtf["1d"]["k"].iloc[-1]
    cond_1d = (d_hist > d_hist_prev) and (d_k > 20)

    # 4. 60分線 (60m): MACD柱狀體趨向0軸往上 & K > 20
    h1_hist = mtf["60m"]["macd_hist"].iloc[-1]
    h1_hist_prev = mtf["60m"]["macd_hist"].iloc[-2]
    h1_k = mtf["60m"]["k"].iloc[-1]
    cond_60m = (h1_hist > h1_hist_prev) and (h1_k > 20)

    # 5. 30分線 (30m): MACD柱狀體趨向0軸往上 & K > 20
    m30_hist = mtf["30m"]["macd_hist"].iloc[-1]
    m30_hist_prev = mtf["30m"]["macd_hist"].iloc[-2]
    m30_k = mtf["30m"]["k"].iloc[-1]
    cond_30m = (m30_hist > m30_hist_prev) and (m30_k > 20)

    return cond_1m and cond_1w and cond_1d and cond_60m and cond_30m

  except Exception:
    return False


def run_autotrading():
  """全台股掃描主流程"""
  print("🚀 開始執行全台股多週期自動篩選...")
  all_stocks = get_all_tw_stocks()
  selected_stocks = []

  total_count = len(all_stocks)
  for idx, ticker in enumerate(all_stocks, 1):
    symbol = ticker.split(".")[0]

    if idx % 100 == 0 or idx == total_count:
      print(f"⏳ 處理進度: [{idx}/{total_count}] ({(idx/total_count)*100:.1f}%)")

    mtf_data = get_stock_mtf_data(ticker)
    if check_entry_condition(mtf_data):
      print(f"🎯 標的 {symbol} 符合全週期進場條件！")
      selected_stocks.append(symbol)

  targets_data = {
      "updated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
      "targets": selected_stocks,
  }

  with open("targets.json", "w", encoding="utf-8") as f:
    json.dump(targets_data, f, ensure_ascii=False, indent=2)

  print(f"✅ 全台股掃描完成！共選出 {len(selected_stocks)} 檔標的: {selected_stocks}")
  return selected_stocks


if __name__ == "__main__":
  run_autotrading()
