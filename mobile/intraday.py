"""Chart data and local-exchange-clock aggregation for the mobile dashboard."""
from collections import OrderedDict
from datetime import datetime
from functools import lru_cache
from hashlib import sha256
import math
from threading import Lock
import time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from stockai.data import ZONES, normalize


@lru_cache(maxsize=512)
def demo_minutes(market, symbol, start, count):
    # Adjacent requests reproduce exactly the same closed ticks. The current
    # minute uses only ticks through the requested second.
    seed = int.from_bytes(sha256(f"{market}:{symbol}".encode()).digest()[:4], "big")
    ticks = np.arange(start, start + count, dtype=np.int64)
    phase = ticks + seed
    price = ((50000 if market.startswith("KOS") else 100) *
             (1 + .025*np.sin(phase/2381) + .006*np.sin(phase/104)
              + .002*np.sin(phase/27) + .0004*np.sin(phase/2.7)))
    price = np.round(price, 0 if market.startswith("KOS") else 2)
    volume = 1 + ((phase * 2654435761) % 103)
    offsets = np.arange(0, count, 60)
    ends = np.minimum(offsets + 59, count - 1)
    return list(zip(ticks[offsets].tolist(), price[offsets].tolist(),
                    np.maximum.reduceat(price, offsets).tolist(),
                    np.minimum.reduceat(price, offsets).tolist(), price[ends].tolist(),
                    np.add.reduceat(volume, offsets).tolist()))


def demo_rows(market, symbol, now, minutes):
    minute = now - now % 60
    # Cache complete hours separately from the changing last minute.
    start = minute - 60 * (61 * minutes)
    hour = minute - minute % 3600
    rows = []
    for stamp in range(start - start % 3600, hour, 3600):
        rows.extend(demo_minutes(market, symbol, stamp, 3600))
    if minute > hour:
        rows.extend(demo_minutes(market, symbol, hour, minute - hour))
    rows.extend(demo_minutes(market, symbol, minute, now % 60 + 1))
    return rows


def combine(rows, minutes, market):
    zone = ZoneInfo(ZONES[market])
    bars = []
    for stamp, op, high, low, close, volume in rows:
        local = datetime.fromtimestamp(stamp, zone)
        since_midnight = local.hour * 60 + local.minute
        start = stamp - (since_midnight % minutes) * 60 - local.second
        if not bars or bars[-1]["start"] != start:
            bars.append(dict(start=start, date=datetime.fromtimestamp(start, zone).strftime("%Y-%m-%d %H:%M"),
                             open=op, high=high, low=low, close=close, volume=volume))
        else:
            bar = bars[-1]
            bar.update(high=max(bar["high"], high), low=min(bar["low"], low),
                       close=close, volume=bar["volume"] + volume)
    return bars


def minute_rows(candles):
    if not candles:
        return []
    frame = pd.DataFrame(candles).rename(columns={
        "openPrice": "open", "highPrice": "high", "lowPrice": "low", "closePrice": "close"})
    frame.index = pd.to_datetime(frame["timestamp"], utc=True)
    frame = normalize(frame)
    return [(int(stamp.timestamp()), *values) for stamp, *values in frame.itertuples()]


class MinuteFeed:
    """Share a broker session and minute history across devices and timeframes."""
    def __init__(self, broker):
        self.broker = broker
        self.cache = OrderedDict()
        self.lock = Lock()
        self.retry_at = 0

    def read(self, market, symbol, minutes):
        key = market, symbol
        now = int(time.time())
        if self.broker is None:
            bars = combine(demo_rows(market, symbol, now, minutes), minutes, market)
            return {"bars": bars[-60:], "asof": now, "warning": ""}
        with self.lock:
            if key not in self.cache:
                if len(self.cache) >= 20:
                    self.cache.popitem(last=False)
                self.cache[key] = {"rows": {}, "next": None, "asof": 0, "updated": -math.inf}
            entry = self.cache[key]
            self.cache.move_to_end(key)
            warning = ""
            if time.monotonic() < self.retry_at:
                warning = "토스 시세 조회 지연 · 재시도 대기"
            elif time.monotonic() - entry["updated"] >= 1:
                try:
                    fresh, cursor = self.broker.candles(symbol, "1m", 200)
                    latest = minute_rows(fresh)
                    rows = entry["rows"]
                    if not latest:
                        raise ValueError("empty feed")
                    # Restart history if the newest page no longer overlaps it.
                    if not rows or latest[0][0] > max(rows):
                        rows.clear()
                        entry["next"] = cursor
                    rows.update((row[0], row) for row in latest)
                    entry["asof"] = int(time.time())
                    if len(rows) < 61 * minutes and entry["next"]:
                        before = entry["next"]
                        older, cursor = self.broker.candles(symbol, "1m", 200, before=before)
                        history = minute_rows(older)
                        for row in history:
                            rows.setdefault(row[0], row)
                        entry["next"] = cursor if history and cursor != before else None
                    if len(rows) > 4000:
                        for stamp in sorted(rows)[:-3800]:
                            del rows[stamp]
                        # Even the largest selectable interval fits this window.
                        entry["next"] = None
                    entry["updated"] = time.monotonic()
                except Exception:
                    self.retry_at = time.monotonic() + 8
                    warning = "토스 시세 조회 지연 · 재시도 대기"
            rows = [row for _, row in sorted(entry["rows"].items())]
            bars = combine(rows, minutes, market)
            if bars and rows[0][0] > bars[0]["start"]:
                bars.pop(0)  # the earliest aggregate may be clipped by pagination
            return {"bars": bars[-60:], "asof": entry["asof"], "warning": warning}
