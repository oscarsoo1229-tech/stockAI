from hashlib import sha256
from pathlib import Path
from datetime import datetime, timezone
import os
import sqlite3

import numpy as np
import pandas as pd

OHLCV = ["open", "high", "low", "close", "volume"]
ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "StockAI"
ZONES = {"KOSPI": "Asia/Seoul", "KOSDAQ": "Asia/Seoul",
         "NASDAQ": "America/New_York", "NYSE": "America/New_York"}


def normalize(frame):
    frame = frame.copy()
    frame.index = pd.to_datetime(frame.index, errors="coerce")
    frame = frame.loc[~frame.index.isna(), OHLCV]
    frame = frame.apply(pd.to_numeric, errors="coerce")
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna()
    frame = frame.loc[~frame.index.duplicated(keep="last")].sort_index()
    valid = ((frame[OHLCV[:4]] > 0).all(axis=1) & (frame.volume >= 0)
             & (frame.high >= frame[["open", "close", "low"]].max(axis=1))
             & (frame.low <= frame[["open", "close"]].min(axis=1)))
    frame = frame[valid]
    if frame.empty:
        raise ValueError("유효한 OHLCV 데이터가 없습니다.")
    return frame


def mock(market, symbol):
    rng = np.random.default_rng(int.from_bytes(sha256(f"{market}:{symbol}".encode()).digest()[:8], "little"))
    dates = pd.bdate_range("2019-01-02", periods=1650)
    cycle = np.sin(np.arange(len(dates)) / 17)
    close = (50000 if market.startswith("KOS") else 100) * np.exp(
        np.cumsum(.0003 + .0018 * cycle + rng.normal(0, .012, len(dates))))
    opened = np.r_[close[0], close[:-1]] * np.exp(rng.normal(0, .003, len(dates)))
    return normalize(pd.DataFrame({
        "open": opened, "high": np.maximum(opened, close) * (1 + rng.uniform(0, .015, len(dates))),
        "low": np.minimum(opened, close) * (1 - rng.uniform(0, .015, len(dates))),
        "close": close, "volume": rng.integers(150000, 1500000, len(dates))
    }, index=dates))


def load(source, market, symbol, csv=None):
    if source == "Mock":
        return mock(market, symbol)
    if source == "CSV":
        frame = pd.read_csv(csv)
        frame.columns = frame.columns.str.lower()
        return normalize(frame.set_index("date"))
    if source == "Yahoo Finance":
        import yfinance as yf
        suffix = {"KOSPI": ".KS", "KOSDAQ": ".KQ"}.get(market, "")
        frame = yf.Ticker(symbol + suffix).history(period="5y", auto_adjust=True, raise_errors=True)
        frame.columns = frame.columns.str.lower()
        if frame.index.tz is not None:
            frame.index = frame.index.tz_localize(None).normalize()
        return normalize(frame)
    if source == "FinanceDataReader":
        import FinanceDataReader as fdr
        frame = fdr.DataReader(symbol, start="2020-01-01")
        frame.columns = frame.columns.str.lower()
        return normalize(frame)
    raise ValueError("알 수 없는 데이터 공급자입니다.")


def cache_frame(source, market, symbol, interval, frame=None):
    ROOT.mkdir(parents=True, exist_ok=True)
    key = (source, market, symbol, interval)
    with sqlite3.connect(ROOT / "data.sqlite3", timeout=15) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS bars
            (source TEXT, market TEXT, symbol TEXT, interval TEXT,
             date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
             PRIMARY KEY(source, market, symbol, interval, date))""")
        if frame is not None:
            db.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?,?,?)",
                           [(*key, stamp.isoformat(), *row) for stamp, row in zip(frame.index, frame[OHLCV].to_numpy())])
        rows = db.execute("""SELECT date, open, high, low, close, volume FROM bars
                             WHERE source=? AND market=? AND symbol=? AND interval=? ORDER BY date""", key)
        result = pd.DataFrame(rows.fetchall(), columns=["date", *OHLCV])
    return normalize(result.set_index("date"))


def closed_daily(frame, market):
    # Conservative policy: use only sessions strictly before the current local date.
    today = pd.Timestamp(datetime.now(timezone.utc)).tz_convert(ZONES[market]).date()
    return frame[frame.index.date < today]


def indicators(frame, period=14):
    c, h, l, v = frame.close, frame.high, frame.low, frame.volume
    ema = lambda values, span: values.ewm(span=span, adjust=False).mean()
    sma = c.rolling(period).mean()
    dev = c.rolling(period).std()
    diff = c.diff()
    smooth = lambda values: values.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    gain, loss = smooth(diff.clip(lower=0)), smooth(-diff.clip(upper=0))
    rsi = 100 * gain / (gain + loss).replace(0, np.nan)
    prev = c.shift()
    tr = pd.concat([h-l, (h-prev).abs(), (l-prev).abs()], axis=1).max(axis=1)
    atr = smooth(tr)
    up, down = h.diff(), -l.diff()
    plus = smooth(up.where((up > down) & (up > 0), 0))
    minus = smooth(down.where((down > up) & (down > 0), 0))
    adx = smooth(100 * (plus-minus).abs() / (plus+minus).replace(0, np.nan))
    low, high = l.rolling(period).min(), h.rolling(period).max()
    typical = (h+l+c)/3
    md = typical.rolling(period).apply(lambda x: np.mean(np.abs(x-x.mean())), raw=True)
    volume_mean = v.rolling(period).mean()
    return pd.DataFrame({
        "SMA": sma, "EMA": ema(c, period), "MACD": ema(c, 12)-ema(c, 26), "ADX": adx,
        "Ichimoku Tenkan": (h.rolling(9).max()+l.rolling(9).min())/2,
        "Ichimoku Kijun": (h.rolling(26).max()+l.rolling(26).min())/2,
        "Ichimoku Span B": (h.rolling(52).max()+l.rolling(52).min())/2,
        "RSI": rsi, "Stochastic": 100*(c-low)/(high-low).replace(0, np.nan),
        "ROC": 100*c.pct_change(period), "CCI": (typical-typical.rolling(period).mean())/(.015*md.replace(0, np.nan)),
        "Bollinger Upper": sma+2*dev, "Bollinger Lower": sma-2*dev, "ATR": atr,
        "Historical Volatility": np.log(c).diff().rolling(period).std()*np.sqrt(252),
        "OBV": (np.sign(diff.fillna(0))*v).cumsum(),
        "VWAP": (typical*v).rolling(period).sum()/v.rolling(period).sum().replace(0, np.nan),
        "Volume SMA": volume_mean, "Volume Spike": v/volume_mean.replace(0, np.nan)
    })


def score_factors(frame, tech, index):
    last, previous, close = tech.iloc[index], tech.iloc[index - 1], frame.close.iloc[index]
    volume_ratio = float(last["Volume Spike"])
    return {
        "단기·중기 추세": 2 if last.SMA > last["Ichimoku Kijun"] else -2,
        "MACD 흐름": 1 if last.MACD > 0 and last.MACD >= previous.MACD else -1,
        "상대강도": 1 if 45 <= last.RSI <= 68 else (-1 if last.RSI > 75 or last.RSI < 30 else 0),
        "거래량 변화": 1 if volume_ratio >= 1.5 and close > frame.close.iloc[index - 1] else 0,
        "변동성 위험": -1 if last.ATR / close > .06 else 0,
    }


def signal_label(score):
    return "관심 후보" if score >= 3 else "중립·관망" if score >= 0 else "주의"


def recommendation(frame):
    """Transparent technical score, separate from learned probability estimates."""
    if len(frame) < 55:
        raise ValueError("추세 분석에는 최소 55개 봉이 필요합니다.")
    tech = indicators(frame)
    last = tech.iloc[-1]
    volume_ratio = float(last["Volume Spike"])
    factors = score_factors(frame, tech, len(frame) - 1)
    score = sum(factors.values())
    label = signal_label(score)
    return {"score": score, "label": label, "factors": factors,
            "rsi": float(last.RSI), "volume_ratio": volume_ratio,
            "return_20d": float(frame.close.iloc[-1] / frame.close.iloc[-21] - 1),
            "asof": frame.index[-1].isoformat()}


def features(frame, period=14):
    result = indicators(frame, period)
    for key in ["SMA", "EMA", "Ichimoku Tenkan", "Ichimoku Kijun", "Ichimoku Span B",
                "Bollinger Upper", "Bollinger Lower", "VWAP"]:
        result[key] = result[key]/frame.close-1
    for key in ["MACD", "ATR"]:
        result[key] /= frame.close
    result["OBV"] = np.sign(frame.close.diff())*result["Volume Spike"]
    result["Volume SMA"] = np.log1p(result["Volume SMA"]).diff()
    result["Return"] = frame.close.pct_change()
    result["Range"] = (frame.high-frame.low)/frame.close
    result["Gap"] = frame.open/frame.close.shift()-1
    return result.replace([np.inf, -np.inf], np.nan)
