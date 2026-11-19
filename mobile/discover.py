"""Rank completed daily bars independently of the user's watchlist."""
import asyncio
import json
import logging
from contextlib import closing
from datetime import datetime
from zoneinfo import ZoneInfo

from stockai.data import ZONES, recommendation
from .feeds import daily_bars, historical_hit_rate

DEMO_STOCKS = {
    "KOSPI": {"005930": "삼성전자", "000660": "SK하이닉스", "005380": "현대차", "000270": "기아",
              "035420": "NAVER", "051910": "LG화학", "012330": "현대모비스", "055550": "신한지주"},
    "KOSDAQ": {"247540": "에코프로비엠", "086520": "에코프로", "058470": "리노공업", "000250": "삼천당제약",
               "039030": "이오테크닉스", "035900": "JYP Ent.", "041510": "에스엠", "403870": "HPSP"},
    "NASDAQ": {"AAPL": "애플", "MSFT": "마이크로소프트", "NVDA": "엔비디아", "GOOGL": "알파벳",
               "META": "메타", "AMZN": "아마존", "TSLA": "테슬라", "AMD": "AMD"},
    "NYSE": {"JPM": "JP모건", "V": "비자", "KO": "코카콜라", "IBM": "IBM", "DIS": "디즈니",
             "XOM": "엑슨모빌", "MCD": "맥도날드", "CAT": "캐터필러"},
}
LOG = logging.getLogger("stockai.discovery")


def create_scan_table(db):
    db.execute("""CREATE TABLE IF NOT EXISTS market_scan (
        mode TEXT, market TEXT, cycle TEXT, universe TEXT, summary TEXT,
        PRIMARY KEY(mode,market))""")


def summaries(db, mode):
    return {market: json.loads(body) for market, body in db.execute(
        "SELECT market, summary FROM market_scan WHERE mode=?", (mode,))}


def candidate(broker, item, step):
    bars = daily_bars(broker, item, step)
    domestic = item["market"].startswith("KOS")
    min_price, min_turnover = (1000, 1e9) if domestic else (5, 1e6)
    if len(bars) < 55 or bars.volume.iloc[-1] <= 0 or bars.close.iloc[-1] < min_price:
        return None
    if broker is not None:
        today = datetime.now(ZoneInfo(ZONES[item["market"]])).date()
        if (today - bars.index[-1].date()).days > 10:
            return None
    turnover = (bars.close.tail(20) * bars.volume.tail(20)).mean()
    if turnover < min_turnover:
        return None
    view = recommendation(bars)
    if view["label"] != "관심 후보":
        return None
    return {**item, "date": view["asof"][:10], "score": view["score"], "factors": view["factors"],
            "volume_ratio": view["volume_ratio"], "return_pct": round(100 * view["return_20d"], 2),
            "rate": historical_hit_rate(bars, view["label"])}


async def scan_markets(database, broker, read_state):
    mode = "live" if broker else "demo"
    while True:
        step = max(0, read_state()["step"]) if not broker else 0
        for market in DEMO_STOCKS:
            cycle = datetime.now(ZoneInfo(ZONES[market])).date().isoformat() if broker else str(step)
            try:
                await scan_batch(database, broker, mode, market, cycle, step)
            except Exception:
                LOG.exception("추천 종목 목록 갱신 실패 · %s", market)
                await asyncio.sleep(2)
        await asyncio.sleep(2 if broker else 1)


async def scan_batch(database, broker, mode, market, cycle, step):
    with closing(database()) as db:
        row = db.execute("SELECT cycle, universe, summary FROM market_scan WHERE mode=? AND market=?",
                         (mode, market)).fetchone()
    if row and row[0] == cycle:
        summary = json.loads(row[2])
        if summary["checked"] >= summary["total"] and not summary.get("unavailable"):
            return
        universe = json.loads(row[1])
    else:
        universe = []
        summary = {"cycle": cycle, "total": 0, "checked": 0, "failed": 0, "rows": []}
    if not universe:
        if broker:
            try:
                stocks = await asyncio.to_thread(broker.request, "/api/v1/stocks/all",
                    {"market": market, "status": "ACTIVE", "commonShare": "true"})
                universe = sorted({s["symbol"]: s.get("name") or s["symbol"] for s in stocks}.items())
                if not universe:
                    raise ValueError("빈 종목 목록")
            except Exception:
                summary["unavailable"] = True
        else:
            universe = sorted(DEMO_STOCKS[market].items())
        summary["total"] = len(universe)
        if universe:
            summary.pop("unavailable", None)
        with closing(database()) as db, db:
            db.execute("INSERT OR REPLACE INTO market_scan VALUES (?,?,?,?,?)",
                       (mode, market, cycle, json.dumps(universe), json.dumps(summary)))
        if not universe:
            await asyncio.sleep(10)
            return
    start = summary["checked"]
    for symbol, name in universe[start:start + 12]:
        item = {"symbol": symbol, "market": market, "query": name}
        try:
            result = await asyncio.to_thread(candidate, broker, item, step)
            if result:
                summary["rows"].append(result)
        except Exception:
            summary["failed"] += 1
        summary["checked"] += 1
        if broker:
            await asyncio.sleep(.18)
    summary["rows"].sort(key=lambda r: (-r["score"], -r["volume_ratio"], r["symbol"]))
    summary["rows"] = summary["rows"][:5]
    with closing(database()) as db, db:
        db.execute("UPDATE market_scan SET summary=? WHERE mode=? AND market=? AND cycle=?",
                   (json.dumps(summary, allow_nan=False), mode, market, cycle))
