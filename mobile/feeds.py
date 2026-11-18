import html
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx
import pandas as pd

from stockai.brokers import TossBroker
from stockai.data import ZONES, closed_daily, mock, normalize, recommendation


def historical_hit_rate(bars, label):
    """Evaluate the same directional signal over non-overlapping five-bar outcomes."""
    if label == "중립·관망":
        return None
    horizon = 5
    hits = samples = 0
    end = 55
    while end + horizon <= len(bars):
        if recommendation(bars.iloc[:end])["label"] != label:
            end += 1
            continue
        change = bars.close.iloc[end + horizon - 1] - bars.close.iloc[end - 1]
        hits += int(change > 0 if label == "관심 후보" else change < 0)
        samples += 1
        end += horizon
    return {"samples": samples, "hits": hits, "horizon": horizon,
            "percent": round(100 * hits / samples) if samples >= 10 else None}


def market_snapshot(broker, item, demo_step=0, record=None):
    market, symbol = item["market"], item["symbol"]
    if broker is None:
        sequence = mock(market, symbol)
        end = min(200 + demo_step, len(sequence))
        bars = sequence.iloc[end - 200:end]
    else:
        candles, _ = broker.candles(symbol, "1d", 200)
        if not candles:
            raise ValueError("일봉 응답이 비어 있습니다.")
        bars = pd.DataFrame(candles).rename(columns={
            "openPrice": "open", "highPrice": "high", "lowPrice": "low",
            "closePrice": "close",
        })
        bars["timestamp"] = pd.to_datetime(bars["timestamp"], utc=True)
        bars["timestamp"] = bars["timestamp"].dt.tz_convert(ZONES[market])
        bars = closed_daily(normalize(bars.set_index("timestamp")), market)
    view = recommendation(bars)
    signal = {"관심 후보": "매수 관심", "중립·관망": "관망", "주의": "매도 주의"}[view["label"]]
    volume_ratio = view["volume_ratio"]
    rsi = view["rsi"]
    return {
        "signal": signal, "score": view["score"], "asof": view["asof"],
        "hit_rate": record(bars, view) if record else historical_hit_rate(bars, view["label"]),
        "factors": view["factors"], "return_20d": view["return_20d"],
        "rsi": rsi if pd.notna(rsi) else None,
        "volume_ratio": volume_ratio if pd.notna(volume_ratio) else None,
        "bars": [{"date": day.date().isoformat(), "open": float(row.open), "high": float(row.high),
                  "low": float(row.low), "close": float(row.close), "volume": float(row.volume)}
                 for day, row in bars.tail(60).iterrows()],
    }


def safe_link(link):
    parts = urlsplit(link)
    return link if parts.scheme == "https" and parts.hostname else ""


def news(client, item):
    response = client.get("https://openapi.naver.com/v1/search/news.json",
        params={"query": item["query"], "display": 10, "sort": "date"},
        headers={"X-Naver-Client-Id": os.environ["NAVER_CLIENT_ID"],
                 "X-Naver-Client-Secret": os.environ["NAVER_CLIENT_SECRET"]})
    response.raise_for_status()
    return [{"title": html.unescape(re.sub(r"<[^>]+>", "", row["title"])),
             "url": link, "date": row["pubDate"]}
            for row in response.json()["items"]
            if (link := safe_link(row.get("originallink", "")) or safe_link(row["link"]))]


def dart(client, item):
    now = datetime.now(timezone(timedelta(hours=9)))
    response = client.get("https://opendart.fss.or.kr/api/list.json", params={
        "crtfc_key": os.environ["DART_API_KEY"], "corp_code": item["dart_code"],
        "bgn_de": (now - timedelta(days=14)).strftime("%Y%m%d"), "page_count": 20,
    })
    response.raise_for_status()
    body = response.json()
    if body["status"] == "013":
        return []
    if body["status"] != "000":
        raise ValueError("DART 응답 오류 " + body["status"])
    return [{"title": row["report_nm"], "date": row["rcept_dt"],
             "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + row["rcept_no"]}
            for row in body["list"]]


def sec(client, item):
    cik = str(int(item["sec_cik"]))
    response = client.get(f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json",
                          headers={"User-Agent": os.environ["SEC_USER_AGENT"]})
    response.raise_for_status()
    filings = response.json()["filings"]["recent"]
    return [{"title": f"SEC {form}", "date": date,
             "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{number.replace('-', '')}/{document}"}
            for form, date, number, document in list(zip(
                filings["form"], filings["filingDate"], filings["accessionNumber"], filings["primaryDocument"]
            ))[:20] if document]
