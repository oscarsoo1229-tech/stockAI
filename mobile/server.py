"""Single-user StockAI PWA and scheduled watchlist monitoring."""
import asyncio
import json
import logging
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from stockai.brokers import TossBroker
from .feeds import dart, market_snapshot, news, sec
from .samples import create_table, export_csv, record_and_score

LOG = logging.getLogger("stockai.mobile")
DEMO = os.getenv("STOCKAI_MODE", "demo") == "demo"
MODE = "demo" if DEMO else "live"
DB = Path(os.getenv("STOCKAI_DB", "mobile-demo.sqlite3" if DEMO else "mobile-live.sqlite3"))
AUTH = HTTPBasic()
PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
CONTACT = os.getenv("VAPID_CONTACT", "")


def authenticate(credentials: HTTPBasicCredentials = Depends(AUTH)):
    password = os.getenv("STOCKAI_PASSWORD", "")
    if not password:
        raise HTTPException(503, "서버의 STOCKAI_PASSWORD를 설정하세요.")
    if credentials.username != "stockai" or not secrets.compare_digest(
        credentials.password.encode(), password.encode()
    ):
        raise HTTPException(401, "로그인 정보를 확인하세요.", headers={"WWW-Authenticate": "Basic"})


def same_origin(request: Request):
    if urlsplit(request.headers.get("origin", "")).netloc != request.headers.get("host"):
        raise HTTPException(403, "같은 사이트에서 요청하세요.")


def database():
    return sqlite3.connect(DB, timeout=15)


def read_state():
    with closing(database()) as db:
        return json.loads(db.execute("SELECT body FROM state WHERE id=1").fetchone()[0])


def save_state(state):
    with closing(database()) as db, db:
        db.execute("UPDATE state SET body=? WHERE id=1", (json.dumps(state, ensure_ascii=False),))


def record_samples(item, bars, signal):
    with closing(database()) as db, db:
        return record_and_score(db, MODE, item, bars, signal)


def push(event):
    if not (PUBLIC_KEY and PRIVATE_KEY and CONTACT):
        return 0
    from pywebpush import WebPushException, webpush
    delivered = 0
    with closing(database()) as db, db:
        for endpoint, body in db.execute("SELECT endpoint, body FROM subscriptions").fetchall():
            try:
                webpush(subscription_info=json.loads(body), data=json.dumps(event, ensure_ascii=False),
                        vapid_private_key=PRIVATE_KEY, vapid_claims={"sub": CONTACT},
                        ttl=300, timeout=8)
                delivered += 1
            except WebPushException as exc:
                if exc.response is not None and exc.response.status_code in (404, 410):
                    db.execute("DELETE FROM subscriptions WHERE endpoint=?", (endpoint,))
                else:
                    LOG.warning("푸시 발송 실패")
            except Exception:
                LOG.warning("푸시 발송 설정 또는 네트워크 오류")
    return delivered


def refresh(state):
    broker = None if DEMO else TossBroker(os.environ["STOCKAI_TOSS_CLIENT_ID"],
                                           os.environ["STOCKAI_TOSS_CLIENT_SECRET"])
    events = []
    state["step"] += 1
    try:
        with httpx.Client(timeout=8, trust_env=not DEMO) as client:
            for key, item in state["watch"].items():
                previous = state["results"].get(key, {})
                current = {**previous, "errors": {}}
                try:
                    snapshot = market_snapshot(broker, item, state["step"],
                                               record=lambda bars, signal: record_samples(item, bars, signal))
                    current["market"] = snapshot
                    old_signal = previous.get("market", {}).get("signal")
                    if old_signal and old_signal != snapshot["signal"] and snapshot["signal"] != "관망":
                        rate = snapshot["hit_rate"]
                        hit_text = (f" · 5봉 과거 적중률 {rate['percent']}% ({rate['hits']}/{rate['samples']}건)"
                                    if rate and rate["percent"] is not None else " · 적중률 표본 부족")
                        events.append({"title": item["symbol"] + " · " + snapshot["signal"],
                                       "body": f"기술 점수 {snapshot['score']:+d} · 기준 {snapshot['asof'][:10]}" + hit_text})
                except Exception:
                    current["errors"]["market"] = "시세 업데이트 실패"
                sources = []
                if DEMO:
                    batch = state["step"] // 5
                    sources.append(("소식", lambda *_: [{
                        "title": f"모의 소식 · {item['query']} 거래량 관찰 #{batch}",
                        "date": str(current.get("market", {}).get("asof", ""))[:10],
                        "url": f"#demo-{batch}",
                    }]))
                elif os.getenv("NAVER_CLIENT_ID") and os.getenv("NAVER_CLIENT_SECRET"):
                    sources.append(("소식", news))
                if not DEMO and item["dart_code"] and os.getenv("DART_API_KEY"):
                    sources.append(("공시", dart))
                if not DEMO and item["sec_cik"] and os.getenv("SEC_USER_AGENT"):
                    sources.append(("공시", sec))
                for category, feed in sources:
                    try:
                        articles = feed(client, item)
                        seen_key = category + "_seen"
                        seen = previous.get(seen_key, [])
                        if seen_key in previous:
                            fresh = [article for article in articles if article["url"] not in seen]
                            if fresh:
                                events.append({"title": f"{item['symbol']} · 새 {category} {len(fresh)}건",
                                               "body": fresh[0]["title"][:180]})
                        current[category] = articles
                        current[seen_key] = list(dict.fromkeys(
                            [article["url"] for article in articles] + seen
                        ))[:100]
                    except Exception:
                        current["errors"][category] = f"{category} 업데이트 실패"
                state["results"][key] = current
    finally:
        if broker:
            broker.close()
    if DEMO:
        for event in events:
            event["title"] = "[모의] " + event["title"]
    state["events"] = (events[::-1] + state["events"])[:40]
    save_state(state)
    for event in events:
        push(event)
    return state


lock = asyncio.Lock()


async def update():
    async with lock:
        return await asyncio.to_thread(refresh, read_state())


async def monitor():
    seconds = 8 if DEMO else max(60, int(os.getenv("STOCKAI_REFRESH_SECONDS", "300")))
    failures = 0
    while True:
        try:
            state = await update()
            errors = {key: result["errors"] for key, result in state["results"].items()
                      if result.get("errors")}
            app.state.monitor_status = {
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "status": "일부 조회 실패" if errors else "정상",
                "errors": errors,
            }
            if errors:
                LOG.warning("일부 데이터 조회 실패 · 다음 갱신에서 다시 시도")
            failures = 0
        except Exception:
            failures += 1
            app.state.monitor_status = {
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "status": "갱신 실패 · 자동 재시도 대기",
                "consecutive_failures": failures,
            }
            LOG.warning("시장 갱신 실패 · 연속 %d회", failures)
        await asyncio.sleep(seconds + min(seconds * (2 ** min(failures, 5) - 1), 900))


@asynccontextmanager
async def lifespan(_app):
    if not DEMO and not all(os.getenv(key) for key in
                            ("STOCKAI_TOSS_CLIENT_ID", "STOCKAI_TOSS_CLIENT_SECRET")):
        raise RuntimeError("실제 시세 모드에는 토스 API 키 두 개가 필요합니다.")
    with closing(database()) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, body TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS subscriptions (endpoint TEXT PRIMARY KEY, body TEXT)")
        create_table(db)
        default = {"watch": {"KOSPI:005930": {"market": "KOSPI", "symbol": "005930",
                   "query": "삼성전자", "dart_code": "00126380", "sec_cik": ""}},
                   "results": {}, "events": [], "step": -1}
        db.execute("INSERT OR IGNORE INTO state VALUES (1, ?)", (json.dumps(default),))
    _app.state.monitor_status = {"status": "첫 갱신 대기"}
    task = asyncio.create_task(monitor())
    _app.state.monitor_task = task
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
WEB = Path(__file__).with_name("web")

private = APIRouter(dependencies=[Depends(authenticate)])


@app.api_route("/healthz", methods=["GET", "HEAD"])
def health():
    if app.state.monitor_task.done():
        return Response(status_code=503)
    try:
        with closing(sqlite3.connect(DB, timeout=1)) as db, db:
            # Verify the persistent database can take a write transaction.
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT id FROM state WHERE id=1").fetchone() is None:
                return Response(status_code=503)
    except sqlite3.Error:
        return Response(status_code=503)
    return Response(status_code=204)


@private.get("/api/diagnostics")
def diagnostics():
    return app.state.monitor_status


@private.get("/")
def home():
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})


@private.get("/api/state")
def dashboard():
    state = read_state()
    return {**state, "demo": DEMO, "monitor": app.state.monitor_status,
            "push_key": PUBLIC_KEY if PRIVATE_KEY and CONTACT else "",
            "news_enabled": DEMO or bool(os.getenv("NAVER_CLIENT_ID") and os.getenv("NAVER_CLIENT_SECRET"))}


@private.get("/api/samples.csv")
def samples_download():
    with closing(database()) as db:
        return Response(export_csv(db, MODE), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="stockai-samples.csv"',
                                 "Cache-Control": "no-store"})


class Watch(BaseModel):
    market: str
    symbol: str = Field(pattern=r"^[A-Z0-9.\-]{1,12}$")
    query: str = Field(min_length=1, max_length=80)
    dart_code: str = Field(default="", pattern=r"^(?:[0-9]{8})?$")
    sec_cik: str = Field(default="", pattern=r"^(?:[0-9]{1,10})?$")


@private.post("/api/watch", dependencies=[Depends(same_origin)])
async def add_watch(item: Watch):
    if item.market not in {"KOSPI", "KOSDAQ", "NASDAQ", "NYSE"}:
        raise HTTPException(422, "시장을 확인하세요.")
    key = f"{item.market}:{item.symbol}"
    async with lock:
        state = read_state()
        if key not in state["watch"] and len(state["watch"]) >= 20:
            raise HTTPException(422, "관심 종목은 최대 20개입니다.")
        state["watch"][key] = item.model_dump()
        state["results"].pop(key, None)
        save_state(state)
    return {"ok": True}


@private.delete("/api/watch/{key}", dependencies=[Depends(same_origin)])
async def delete_watch(key: str):
    async with lock:
        state = read_state()
        state["watch"].pop(key, None)
        state["results"].pop(key, None)
        save_state(state)
    return {"ok": True}


@private.post("/api/refresh", dependencies=[Depends(same_origin)])
async def refresh_now():
    if not DEMO:
        raise HTTPException(403, "실제 시세는 설정된 주기로 갱신합니다.")
    await update()
    return {"ok": True}


class PushKeys(BaseModel):
    p256dh: str = Field(pattern=r"^[A-Za-z0-9_-]{87}$")
    auth: str = Field(pattern=r"^[A-Za-z0-9_-]{22}$")


class Subscription(BaseModel):
    endpoint: str = Field(max_length=2000)
    keys: PushKeys


@private.post("/api/push", dependencies=[Depends(same_origin)])
def subscribe(subscription: Subscription):
    parsed = urlsplit(subscription.endpoint)
    host = parsed.hostname or ""
    allowed = host.endswith(".push.apple.com") or host in {
        "web.push.apple.com", "fcm.googleapis.com", "updates.push.services.mozilla.com",
    }
    if not allowed or parsed.scheme != "https" or parsed.netloc != host:
        raise HTTPException(422, "브라우저의 푸시 주소를 확인하세요.")
    with closing(database()) as db, db:
        db.execute("INSERT OR REPLACE INTO subscriptions VALUES (?, ?)",
                   (subscription.endpoint, subscription.model_dump_json()))
    return {"ok": True}


@private.post("/api/push/test", dependencies=[Depends(same_origin)])
def test_push():
    count = push({"title": "StockAI 알림 확인", "body": "푸시 연결을 확인했습니다."})
    if not count:
        raise HTTPException(503, "푸시 서버 설정과 알림 권한을 확인하세요.")
    return {"accepted": count}


app.include_router(private)
app.mount("/", StaticFiles(directory=WEB), name="web")
