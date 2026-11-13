# AI training and evaluation

Load daily OHLCV first, then select model, epochs, batch and device in the training tab. Mock data is synthetic and cannot represent market performance.

Features include returns and technical indicators over 60 bars. Models estimate return and direction for 1, 5, 20 trading days. Trees use the final row, neural networks use the sequence. Calibration uses a separate validation interval. Three chronological walk-forward folds purge labels crossing the next boundary.

Reported metrics include classification accuracy, balanced accuracy, precision, recall, F1, ROC-AUC, Brier score, return and price MAE/RMSE, directional accuracy. Scores come from held-out chronological folds. Overlapping-label embargo is not implemented. Optuna tunes neural learning rate and batch on validation loss. Training runs on a separate Qt thread. Resume checkpoints are not exposed.

Backtest uses held-out predictions, next observed open fills, closing valuation, long/cash exposure, user bps cost and fixed 5bp slippage. Dividends, security-specific fees/taxes, FX and leverage are excluded.


The market scan uses an independent rule score, not model-generated probability. It exposes trend alignment, MACD, RSI band, volume expansion and volatility penalty for every scanned symbol. Scores rank research candidates only. A market-wide scan uses one candle request per symbol and can take time. Use batches and respect the broker's current API limits.

The market scanner can exclude listings below the selected price and 20-bar average turnover thresholds before ranking. Defaults are KRW 1,000 and KRW 1 billion for Korean listings, USD 5 and USD 1 million for U.S. listings. Both values can be changed in the GUI. They are user-selected liquidity screens, not official classifications.
