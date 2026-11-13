# StockAI

Windows 10/11, Python 3.13, PySide6 research GUI, OHLCV provider selection, indicators, Random Forest, XGBoost, LSTM, GRU, Transformer, probability calibration, walk-forward evaluation, ensemble display and local paper backtest.

## Install
1. Install 64-bit Python 3.13 with the Python launcher.
2. Run install_windows.bat.
3. Run start_windows.bat.
4. Select Mock and a market, then choose a symbol. Mock bars are synthetic and not real market information.
5. Training requires at least 450 daily bars. Auto and CPU do not require CUDA.

For NVIDIA acceleration, replace the default PyTorch package using https://pytorch.org/get-started/locally/ for Windows, Pip, Python and a CUDA version supported by the installed driver. Batch sizing reads visible CUDA GPU and VRAM.

CSV columns: date, open, high, low, close, volume. Yahoo Finance and FinanceDataReader are optional historical sources with variable availability and coverage.

Analysis mode only. The app sends no live orders. Toss REST candles and a WebSocket generator are implemented. Credential dialog, managed reconnect UI, KIS and Kiwoom adapters, exchange calendars, KRX/ATS sessions, FX-aware backtests, fees and training resume are unfinished. The current daily session is hidden. Backtests use next observed open, long/cash exposure, bps cost and fixed slippage assumptions. AI estimates do not guarantee outcomes.

Bars cache under LOCALAPPDATA/StockAI. Models save under USERPROFILE/StockAI/models. Run build_windows.bat on Windows to package dist/StockAI. This Linux session cannot build or verify a Windows executable.


The API settings tab saves Toss credentials in the Windows credential store. The dashboard can scan a selected batch of up to 100 symbols from the broker's market universe, then filters low-priced and low-liquidity listings using editable minimum-price and 20-bar average-turnover thresholds. Defaults are KRW 1,000 / KRW 1 billion for Korean listings and USD 5 / USD 1 million for U.S. listings. The remaining names are ranked using a transparent score with trend, MACD, RSI, volume expansion and volatility. These defaults are configurable and are screening preferences, not an official definition of a penny stock. Increase the list start position to scan the next batch. This is technical screening, not a guarantee or learned probability. Each symbol requires a historical candle API request, so scanning large universes takes time and follows the broker's rate limits.
