"""Adapters. Toss REST paths and payloads follow its published OpenAPI schema."""
import asyncio
import json
import random
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx


class BrokerError(RuntimeError):
    pass


class TossBroker:
    name = "Toss Securities"

    def __init__(self, client_id, client_secret):
        self.client_id, self.client_secret = client_id, client_secret
        self.client = httpx.Client(base_url="https://openapi.tossinvest.com", timeout=20)
        self.token, self.expiry = None, 0
        self.last_ok, self.last_status = None, "Disconnected"

    def authenticate(self):
        try:
            response = self.client.post("/oauth2/token", data={
                "grant_type": "client_credentials", "client_id": self.client_id,
                "client_secret": self.client_secret})
            response.raise_for_status()
            body = response.json()
            self.token = body["access_token"]
            self.expiry = time.time()+int(body.get("expires_in", 3600))-60
            self.client.headers["Authorization"] = f"Bearer {self.token}"
            self.last_ok = datetime.now(timezone.utc).isoformat()
            self.last_status = "Connected"
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            self.last_status = "Authentication/network error"
            raise BrokerError(self._safe_error(exc)) from exc

    @staticmethod
    def _safe_error(exc):
        if isinstance(exc, httpx.HTTPStatusError):
            return f"HTTP {exc.response.status_code}: API request rejected"
        if isinstance(exc, httpx.TimeoutException):
            return "API request timed out"
        if isinstance(exc, httpx.HTTPError):
            return "Network connection failed"
        return "Unexpected API response"

    def request(self, path, params=None, account=None, attempts=4):
        if not self.token or time.time() >= self.expiry:
            self.authenticate()
        headers = {"X-Tossinvest-Account": str(account)} if account else {}
        for attempt in range(attempts):
            try:
                response = self.client.get(path, params=params, headers=headers)
                if response.status_code == 401 and attempt == 0:
                    self.token = None
                    self.authenticate()
                    continue
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt+1 < attempts:
                        retry = response.headers.get("Retry-After")
                        delay = min(float(retry), 30) if retry and retry.isdigit() else min(2**attempt, 16)+random.random()
                        time.sleep(delay)
                        continue
                response.raise_for_status()
                self.last_ok = datetime.now(timezone.utc).isoformat()
                self.last_status = "Connected"
                body = response.json()
                if body.get("error"):
                    raise BrokerError(body["error"].get("message", "API request failed"))
                return body.get("result", body)
            except httpx.HTTPError as exc:
                self.last_status = "API/network error"
                if attempt+1 < attempts and isinstance(exc, httpx.TransportError):
                    time.sleep(min(2**attempt, 16))
                    continue
                raise BrokerError(self._safe_error(exc)) from exc
        raise BrokerError("Rate limit retry limit reached")

    def candles(self, symbol, interval="1d", count=200, before=None):
        count = max(1, min(200, int(count)))
        params = {"symbol": symbol, "interval": interval, "count": count, "adjusted": "true"}
        if before:
            params["before"] = before
        result = self.request("/api/v1/candles", params)
        return result["candles"], result.get("nextBefore")

    def stocks(self, market):
        return self.request("/api/v1/stocks/all", {"market": market})

    def accounts(self):
        return self.request("/api/v1/accounts")

    def holdings(self, account):
        return self.request("/api/v1/holdings", account=account)

    def buying_power(self, account, currency):
        return self.request("/api/v1/buying-power", {"currency": currency}, account)

    def orders(self, account, status="OPEN"):
        return self.request("/api/v1/orders", {"status": status}, account)

    def exchange_rate(self):
        return self.request("/api/v1/exchange-rate", {"baseCurrency": "USD", "quoteCurrency": "KRW"})

    def orderbook(self, symbol):
        return self.request("/api/v1/orderbook", {"symbol": symbol})

    def market_hours(self, market):
        return {"supported": False, "message": f"{market} 거래시간 전용 API는 공식 REST 스키마에서 확인되지 않음"}

    async def stream(self, token, subscriptions):
        import websockets
        uri = "wss://openapi-ws.tossinvest.com/ws/v1"
        async with websockets.connect(uri, additional_headers={"Authorization": f"Bearer {token}"}) as ws:
            await ws.send(json.dumps(subscriptions))
            async for message in ws:
                yield json.loads(message)

    def close(self):
        self.client.close()


def extract_symbols(payload):
    """Find official stock-universe entries without depending on envelope nesting."""
    found = []
    stack = [payload]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            symbol = item.get("symbol")
            if isinstance(symbol, str) and symbol:
                found.append((symbol, str(item.get("name", item.get("stockName", "")))))
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    deduplicated = {}
    for symbol, name in found:
        deduplicated[symbol] = name
    return list(deduplicated.items())
