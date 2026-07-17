#!/usr/bin/env python3
"""
多币种 EMA/MACD 金叉/死叉检测 + Server酱推送
数据源: CoinGecko (国内可直接访问，免费，无需 API Key)
EMA计算与币安一致 (adjust=True, span=N)
"""

import json
import requests
import pandas as pd
import time
from datetime import datetime, timedelta
from pathlib import Path

SERVERCHAN_KEY = "SCT380416TEk1Zj7oZbExZg5dAZJB3Ri3O"

CONFIGS = {
    "xau": {
        "coin_id": "tether-gold",
        "vs_currency": "usd",
        "intervals": [
            {
                "interval": "1h",
                "cg_days": 7,
                "short_ema": 13,
                "long_ema": 36,
                "macd_fast": 16,
                "macd_slow": 24,
                "macd_signal": 9,
            },
            {
                "interval": "4h",
                "cg_days": 30,
                "short_ema": 18,
                "long_ema": 48,
                "macd_fast": 18,
                "macd_slow": 28,
                "macd_signal": 10,
            },
        ],
    },
    "btc": {
        "coin_id": "bitcoin",
        "vs_currency": "usd",
        "intervals": [
            {
                "interval": "1h",
                "cg_days": 7,
                "short_ema": 20,
                "long_ema": 60,
                "macd_fast": None,
                "macd_slow": None,
                "macd_signal": None,
            },
        ],
    },
    "eth": {
        "coin_id": "ethereum",
        "vs_currency": "usd",
        "intervals": [
            {
                "interval": "1h",
                "cg_days": 7,
                "short_ema": 20,
                "long_ema": 60,
                "macd_fast": None,
                "macd_slow": None,
                "macd_signal": None,
            },
        ],
    },
    "hype": {
        "coin_id": "hyperliquid",
        "vs_currency": "usd",
        "intervals": [
            {
                "interval": "1h",
                "cg_days": 7,
                "short_ema": 20,
                "long_ema": 60,
                "macd_fast": None,
                "macd_slow": None,
                "macd_signal": None,
            },
        ],
    },
}

STATE_FILE = Path("btc_ema_state.json")
COOLDOWN_HOURS = 4


def fetch_coingecko_ohlc(coin_id="bitcoin", vs_currency="usd", days=180):
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc"
    params = {"vs_currency": vs_currency, "days": days}
    print(f"[INFO] 从 CoinGecko 获取 {coin_id} 近 {days} 天 OHLC 数据...")

    max_retries = 5
    retry_delay = 10
    
    for attempt in range(max_retries):
        try:
            time.sleep(3)
            
            resp = requests.get(url, params=params, timeout=30)
            
            if resp.status_code == 429:
                retry_after = resp.headers.get('Retry-After')
                if retry_after:
                    wait_time = int(retry_after)
                    print(f"[WARN] 请求被限流，服务端要求等待 {wait_time} 秒 ({attempt+1}/{max_retries})")
                else:
                    wait_time = retry_delay
                    print(f"[WARN] 请求被限流，等待 {wait_time} 秒后重试 ({attempt+1}/{max_retries})")
                
                time.sleep(wait_time)
                retry_delay = min(retry_delay * 2, 60)
                continue
            
            resp.raise_for_status()
            data = resp.json()

            if not data or len(data) < 2:
                print(f"[ERROR] {coin_id} 数据不足")
                return None

            df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close"])
            df["date"] = pd.to_datetime(df["timestamp"], unit="ms")
            df["close"] = df["close"].astype(float)
            return df[["date", "close"]]
            
        except requests.exceptions.RequestException as e:
            print(f"[WARN] 请求失败: {e}, 等待 {retry_delay} 秒后重试 ({attempt+1}/{max_retries})")
            time.sleep(retry_delay)
            retry_delay *= 2

    print(f"[ERROR] {coin_id} 多次重试失败")
    return None


def calc_indicator(df, short_ema, long_ema, macd_fast=None, macd_slow=None, macd_signal=None):
    df = df.copy()
    
    df["ema_short"] = df["close"].ewm(span=short_ema, adjust=True).mean()
    df["ema_long"] = df["close"].ewm(span=long_ema, adjust=True).mean()

    df["golden_cross"] = (
        (df["ema_short"] > df["ema_long"]) &
        (df["ema_short"].shift(1) <= df["ema_long"].shift(1))
    )
    df["death_cross"] = (
        (df["ema_short"] < df["ema_long"]) &
        (df["ema_short"].shift(1) >= df["ema_long"].shift(1))
    )

    if macd_fast and macd_slow and macd_signal:
        df["macd"] = df["close"].ewm(span=macd_fast, adjust=True).mean() - df["close"].ewm(span=macd_slow, adjust=True).mean()
        df["macd_signal"] = df["macd"].ewm(span=macd_signal, adjust=True).mean()
        df["macd_hist"] = df["macd"] - df["macd_signal"]

        df["macd_golden"] = (
            (df["macd"] > df["macd_signal"]) &
            (df["macd"].shift(1) <= df["macd_signal"].shift(1))
        )
        df["macd_death"] = (
            (df["macd"] < df["macd_signal"]) &
            (df["macd"].shift(1) >= df["macd_signal"].shift(1))
        )

    return df


def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def should_alert(signal_key):
    state = load_state()
    now = datetime.now()

    if signal_key not in state:
        state[signal_key] = now.isoformat()
        save_state(state)
        return True

    last = datetime.fromisoformat(state[signal_key])
    if now - last >= timedelta(hours=COOLDOWN_HOURS):
        state[signal_key] = now.isoformat()
        save_state(state)
        return True

    print(f"[SKIP] {signal_key} 冷却中，上次提醒: {last.strftime('%Y-%m-%d %H:%M')}")
    return False


def send_serverchan(title, body):
    if not SERVERCHAN_KEY:
        print("[ERROR] SERVERCHAN_KEY 未配置")
        return False

    url = f"https://sctapi.ftqq.com/{SERVERCHAN_KEY}.send"

    try:
        resp = requests.post(url, data={"title": title, "desp": body}, timeout=15)
        result = resp.json()
        if result.get("code") == 0:
            print("[OK] Server酱推送成功")
            return True
        else:
            print(f"[FAIL] Server酱: {result}")
            return False
    except Exception as e:
        print(f"[ERROR] Server酱请求失败: {e}")
        return False


def process_coin(coin_key):
    config = CONFIGS.get(coin_key)
    if not config:
        print(f"[ERROR] 未知币种: {coin_key}")
        return

    coin_id = config["coin_id"]
    vs_currency = config["vs_currency"]

    for interval_config in config["intervals"]:
        interval = interval_config["interval"]
        cg_days = interval_config["cg_days"]
        short_ema = interval_config["short_ema"]
        long_ema = interval_config["long_ema"]
        macd_fast = interval_config["macd_fast"]
        macd_slow = interval_config["macd_slow"]
        macd_signal = interval_config["macd_signal"]

        try:
            df = fetch_coingecko_ohlc(coin_id, vs_currency, days=cg_days)
            if df is None:
                print(f"[ERROR] {coin_key} {interval} 数据获取失败")
                continue
            df = calc_indicator(df, short_ema, long_ema, macd_fast, macd_slow, macd_signal)

            latest = df.iloc[-1]
            price = latest["close"]
            trend = "多头" if latest["ema_short"] > latest["ema_long"] else "空头"

            print(f"\n[{coin_key.upper()} {interval}] 价格: ${price:,.2f} | EMA{short_ema}: ${latest['ema_short']:,.2f} | EMA{long_ema}: ${latest['ema_long']:,.2f} | 趋势: {trend}")

            signals = []

            if latest["golden_cross"]:
                signal_key = f"{coin_key}_{interval}_golden_cross"
                if should_alert(signal_key):
                    signals.append(f"� EMA{short_ema}上穿EMA{long_ema}")

            if latest["death_cross"]:
                signal_key = f"{coin_key}_{interval}_death_cross"
                if should_alert(signal_key):
                    signals.append(f"⚠️ EMA{short_ema}下穿EMA{long_ema}")

            if macd_fast and latest.get("macd_golden"):
                signal_key = f"{coin_key}_{interval}_macd_golden"
                if should_alert(signal_key):
                    signals.append(f"📈 MACD金叉")

            if macd_fast and latest.get("macd_death"):
                signal_key = f"{coin_key}_{interval}_macd_death"
                if should_alert(signal_key):
                    signals.append(f"📉 MACD死叉")

            if signals:
                title = f"{' '.join(signals)} | {coin_key.upper()} {interval}"
                body = (
                    f"**币种:** {coin_key.upper()}\n\n"
                    f"**周期:** {interval}\n\n"
                    f"**当前价格:** ${price:,.2f}\n\n"
                    f"**EMA{short_ema}:** ${latest['ema_short']:,.2f}\n\n"
                    f"**EMA{long_ema}:** ${latest['ema_long']:,.2f}\n\n"
                    f"**趋势:** {trend}\n\n"
                )
                if macd_fast:
                    body += (
                        f"**MACD:** {latest['macd']:.2f}\n\n"
                        f"**MACD Signal:** {latest['macd_signal']:.2f}\n\n"
                        f"**MACD Hist:** {latest['macd_hist']:.2f}\n\n"
                    )
                body += f"**检测时间:** {datetime.now():%Y-%m-%d %H:%M:%S}"
                print(f"[推送] {title}")
                send_serverchan(title, body)
            else:
                print(f"[未推送] {coin_key.upper()} {interval} 无交叉信号")

        except Exception as e:
            print(f"[ERROR] {coin_key} {interval} 处理失败: {e}")


def main():
    print(f"\n{'='*50}")
    print(f"多币种 EMA/MACD 检测 | {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"{'='*50}")

    for coin_key in CONFIGS:
        process_coin(coin_key)


if __name__ == "__main__":
    main()