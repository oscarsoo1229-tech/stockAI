# Broker API setup

## Toss Securities
Official docs: https://developers.tossinvest.com/
REST schema: https://openapi.tossinvest.com/openapi-docs/latest/openapi.json
AsyncAPI: https://openapi.tossinvest.com/openapi-docs/latest/asyncapi.json

The adapter implements Client Credentials token request, bearer token, candle query (up to 200, daily and one-minute), stock universe, accounts, holdings, buying power, orders, FX and orderbook. Streaming uses wss://openapi-ws.tossinvest.com/ws/v1. Handshake requires bearer token. The JSON subscription array declares the complete active subscription set.

Create a developer API client and configure any required allowed IP. Set Windows user variables STOCKAI_TOSS_CLIENT_ID and STOCKAI_TOSS_CLIENT_SECRET, then restart the app. There is no credential dialog in this build. Data queries require valid credentials and broker permission. The GUI reconnect/resync manager is not implemented. An exchange-hours REST endpoint was not found in the verified schema.

## Korea Investment & Securities
Portal: https://apiportal.koreainvestment.com/
Official examples: https://github.com/koreainvestment/open-trading-api
Adapter not implemented.

## Kiwoom
Portal: https://openapi.kiwoom.com/
Official token route is POST https://api.kiwoom.com/oauth2/token, mock domain https://mockapi.kiwoom.com and JSON fields grant_type, appkey, secretkey. Adapter not implemented.

No live order endpoint is called by the app. Confirm API and account eligibility with each broker.
