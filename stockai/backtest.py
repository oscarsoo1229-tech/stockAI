import numpy as np
import pandas as pd


def simulate(bars, probabilities, initial=10000, cost_bps=10, slip_bps=5, fx=None):
    """Signals at close execute at the next observed open; long/cash, no leverage."""
    if initial <= 0 or min(cost_bps, slip_bps) < 0 or cost_bps + slip_bps >= 10000:
        raise ValueError("초기자금과 비용을 확인하세요.")
    bars = bars.loc[probabilities.index.min():].copy()
    factor = 1
    if fx is not None:
        # FX must be available at the prior bar's close, before the simulated fill.
        factor = fx.reindex(bars.index).ffill().shift()
        if factor.isna().any() or (factor <= 0).any():
            raise ValueError("백테스트 시작 전 확정 환율을 포함해 주세요.")
    opened, closed = bars.open*factor, bars.close*factor
    decisions = probabilities.reindex(bars.index).ffill().shift()
    cash, shares, entry = float(initial), 0.0, 0.0
    cost = (cost_bps + slip_bps)/10000
    trades, equity = [], []
    for stamp in bars.index:
        p, price = decisions.loc[stamp], opened.loc[stamp]
        if p >= .55 and shares == 0:
            entry = cash
            shares, cash = cash/(price*(1+cost)), 0.0
            trades.append((stamp, "BUY", float(price), None))
        elif p <= .45 and shares > 0:
            cash, shares = shares*price*(1-cost), 0.0
            trades.append((stamp, "SELL", float(price), cash/entry-1))
        equity.append(cash + shares*closed.loc[stamp])
    curve = pd.Series(equity, index=bars.index)
    returns = curve.pct_change().dropna()
    exits = [item[3] for item in trades if item[1] == "SELL"]
    metrics = {
        "net_return": curve.iloc[-1]/initial-1,
        "cagr": (curve.iloc[-1]/initial)**(252/max(1, len(curve)-1))-1,
        "sharpe": returns.mean()/returns.std()*np.sqrt(252) if len(returns)>1 and returns.std()>0 else None,
        "mdd": (curve/curve.cummax()-1).min(),
        "win_rate": np.mean(np.array(exits)>0) if exits else None,
        "fills": len(trades),
    }
    return curve, trades, metrics


class PaperAccount:
    def __init__(self, initial=10000):
        self.cash, self.shares = float(initial), 0
        self.fills = []

    def order(self, side, quantity, price):
        if side not in ("BUY", "SELL") or not np.isfinite(price) or price <= 0 or quantity <= 0:
            raise ValueError("주문 입력을 확인하세요.")
        delta = quantity if side == "BUY" else -quantity
        if self.cash - delta*price < 0 or self.shares + delta < 0:
            raise ValueError("가상 잔고 또는 수량 부족")
        self.cash -= delta*price
        self.shares += delta
        self.fills.append((side, quantity, price))
