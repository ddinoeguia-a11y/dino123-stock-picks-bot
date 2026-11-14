"""Same-day trading rules shared by the live scanner and the backtester.

Every rule is explicit so it can be tested on history. Prices in, decision out.
"""

COST_PER_SIDE = 0.0005  # 0.05% slippage/fees per side, applied in backtests

# Liquid universe used for backtests (and as a fallback scan list).
STOCKS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "NFLX", "AVGO",
    "JPM", "BAC", "XOM", "CVX", "WMT", "COST", "UNH", "LLY", "V", "MA",
    "INTC", "MU", "QCOM", "CRM", "ORCL", "ADBE", "SHOP", "UBER", "ABNB", "PYPL",
    "COIN", "MSTR", "PLTR", "SMCI", "MARA", "RIOT", "SOFI", "HOOD", "RIVN", "LCID",
    "NIO", "SNAP", "ROKU", "DKNG", "AFRM", "UPST", "CVNA", "ARM", "TSM", "BABA",
    "SPY", "QQQ", "IWM",
]
CRYPTO = ["BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "DOGE/USD", "AVAX/USD", "LINK/USD", "LTC/USD"]

# Rule parameters (tuned only on history, never on the day being traded).
STOCK_RULES = {
    "gap_and_go":  {"min_gap": 0.03, "side": "long",  "stop_atr": 0.5, "target_atr": 1.0},
    "gap_fade":    {"min_gap": 0.06, "side": "short", "stop_atr": 0.5, "target_atr": 1.0},
    "gapdown_bounce": {"min_gap": -0.05, "side": "long", "stop_atr": 0.5, "target_atr": 1.0},
}
CRYPTO_RULES = {
    "momentum":  {"min_prev_ret": 0.05,  "side": "long",  "stop_atr": 0.5, "target_atr": 1.0},
    "reversal":  {"min_prev_ret": -0.07, "side": "long",  "stop_atr": 0.5, "target_atr": 1.0},
}


def atr(bars, n=14):
    """Average true range over the last n bars (bars oldest->newest)."""
    if len(bars) < n + 1:
        return None
    trs = []
    for prev, b in zip(bars[-n - 1:-1], bars[-n:]):
        trs.append(max(b["h"] - b["l"], abs(b["h"] - prev["c"]), abs(b["l"] - prev["c"])))
    return sum(trs) / n


def avg_volume(bars, n=20):
    vs = [b["v"] for b in bars[-n:]]
    return sum(vs) / len(vs) if vs else 0


def stock_signals(gap):
    """Which stock rules fire for a given opening gap (fraction)."""
    hits = []
    if gap >= STOCK_RULES["gap_fade"]["min_gap"]:
        hits.append("gap_fade")
    if gap >= STOCK_RULES["gap_and_go"]["min_gap"]:
        hits.append("gap_and_go")
    if gap <= STOCK_RULES["gapdown_bounce"]["min_gap"]:
        hits.append("gapdown_bounce")
    return hits


def crypto_signals(prev_ret):
    hits = []
    if prev_ret >= CRYPTO_RULES["momentum"]["min_prev_ret"]:
        hits.append("momentum")
    if prev_ret <= CRYPTO_RULES["reversal"]["min_prev_ret"]:
        hits.append("reversal")
    return hits


def levels(entry, a, rule):
    """Entry/stop/target for a rule given ATR a."""
    if rule["side"] == "long":
        return entry - rule["stop_atr"] * a, entry + rule["target_atr"] * a
    return entry + rule["stop_atr"] * a, entry - rule["target_atr"] * a


INSIDER_RULE = {"min_buyers": 2, "lookback_days": 7, "side": "long", "stop_atr": 0.5, "target_atr": 1.0}
INSIDER_BOOST_LOOKBACK = 30  # "insiders bought in the last 30 days" flag for other strategies


def simulate_multi(bars, entry, stop, target):
    """Long trade held up to len(bars) days; worst case if stop and target hit the same day."""
    for b in bars:
        if b["l"] <= stop:
            return stop / entry - 1 - 2 * COST_PER_SIDE, "stop"
        if b["h"] >= target:
            return target / entry - 1 - 2 * COST_PER_SIDE, "target"
    return bars[-1]["c"] / entry - 1 - 2 * COST_PER_SIDE, "close"


def simulate_day(bar, entry, stop, target, side):
    """Outcome of one same-day trade using a daily bar.

    Daily bars don't show whether the high or low came first, so we assume the
    WORST case: if both stop and target were touched, count it as a stop-out.
    Returns net return as a fraction (after costs) and an outcome label.
    """
    if side == "long":
        if bar["l"] <= stop:
            gross, out = stop / entry - 1, "stop"
        elif bar["h"] >= target:
            gross, out = target / entry - 1, "target"
        else:
            gross, out = bar["c"] / entry - 1, "close"
    else:
        if bar["h"] >= stop:
            gross, out = 1 - stop / entry, "stop"
        elif bar["l"] <= target:
            gross, out = 1 - target / entry, "target"
        else:
            gross, out = 1 - bar["c"] / entry, "close"
    return gross - 2 * COST_PER_SIDE, out
