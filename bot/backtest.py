"""Backtest the same-day rules on years of daily history.

Usage: python -m bot.backtest [--years 3]
Writes results/backtest.json and results/backtest.md
"""
import argparse
import datetime as dt
import json
import os

from bot import strategy as S


def stats(rets):
    if not rets:
        return {"trades": 0}
    wins = [r for r in rets if r > 0]
    eq, peak, mdd, streak, worst = 1.0, 1.0, 0.0, 0, 0
    for r in rets:
        eq *= 1 + r * 0.1  # assume 10% of account per trade
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
        streak = streak + 1 if r <= 0 else 0
        worst = max(worst, streak)
    return {
        "trades": len(rets),
        "win_rate": round(len(wins) / len(rets), 3),
        "avg_return_pct": round(100 * sum(rets) / len(rets), 3),
        "best_pct": round(100 * max(rets), 2),
        "worst_pct": round(100 * min(rets), 2),
        "account_growth_pct_10pct_sizing": round(100 * (eq - 1), 2),
        "max_drawdown_pct": round(100 * mdd, 2),
        "worst_losing_streak": worst,
    }


def run_stocks(bars_by_sym, purchases=None):
    from bot.insider import recent_buying
    results = {k: [] for k in S.STOCK_RULES}
    if purchases is not None:
        for k in ("insider_cluster_1day", "insider_cluster_5day"):
            results[k] = []
        for k in S.STOCK_RULES:
            results[f"{k} + insiders bought (30d)"] = []
            results[f"{k} + no insider buying"] = []
    ir = S.INSIDER_RULE
    for sym, bars in bars_by_sym.items():
        for i in range(21, len(bars)):
            hist, today, prev = bars[:i], bars[i], bars[i - 1]
            a = S.atr(hist)
            if not a or prev["c"] < 5:
                continue
            gap = today["o"] / prev["c"] - 1
            bought30 = purchases is not None and recent_buying(purchases, sym, today["t"], S.INSIDER_BOOST_LOOKBACK)[0] > 0
            for name in S.stock_signals(gap):
                rule = S.STOCK_RULES[name]
                stop, target = S.levels(today["o"], a, rule)
                r, _ = S.simulate_day(today, today["o"], stop, target, rule["side"])
                results[name].append(r)
                if purchases is not None:
                    results[f"{name} + {'insiders bought (30d)' if bought30 else 'no insider buying'}"].append(r)
            if purchases is not None:
                n, _ = recent_buying(purchases, sym, today["t"], ir["lookback_days"])
                if n >= ir["min_buyers"]:
                    stop, target = S.levels(today["o"], a, ir)
                    results["insider_cluster_1day"].append(S.simulate_day(today, today["o"], stop, target, "long")[0])
                    # 5-day hold uses a wider target/stop (1x / 2x daily move), still worst-case.
                    if i + 5 <= len(bars):
                        results["insider_cluster_5day"].append(
                            S.simulate_multi(bars[i:i + 5], today["o"], today["o"] - a, today["o"] + 2 * a)[0])
    return results


def run_crypto(bars_by_sym):
    results = {k: [] for k in S.CRYPTO_RULES}
    for sym, bars in bars_by_sym.items():
        for i in range(21, len(bars)):
            hist, today, prev, prev2 = bars[:i], bars[i], bars[i - 1], bars[i - 2]
            a = S.atr(hist)
            if not a:
                continue
            prev_ret = prev["c"] / prev2["c"] - 1
            for name in S.crypto_signals(prev_ret):
                rule = S.CRYPTO_RULES[name]
                stop, target = S.levels(today["o"], a, rule)
                r, _ = S.simulate_day(today, today["o"], stop, target, rule["side"])
                results[name].append(r)
    return results


def report(stock_res, crypto_res, years, out_dir="results"):
    os.makedirs(out_dir, exist_ok=True)
    data = {
        "generated_utc": dt.datetime.utcnow().isoformat(timespec="seconds"),
        "years": years,
        "method": "daily bars; entry at open; worst case if stop and target both touched; 0.05% cost per side",
        "stocks": {k: stats(v) for k, v in stock_res.items()},
        "crypto": {k: stats(v) for k, v in crypto_res.items()},
    }
    with open(os.path.join(out_dir, "backtest.json"), "w") as f:
        json.dump(data, f, indent=2)
    lines = [f"# Backtest ({years}y, generated {data['generated_utc']} UTC)", "", data["method"], "",
             "| market | strategy | trades | win rate | avg/trade | acct growth | max drawdown | worst streak |",
             "|---|---|---|---|---|---|---|---|"]
    for mkt in ("stocks", "crypto"):
        for k, s in data[mkt].items():
            if not s.get("trades"):
                lines.append(f"| {mkt} | {k} | 0 | – | – | – | – | – |")
                continue
            lines.append(f"| {mkt} | {k} | {s['trades']} | {s['win_rate']:.0%} | {s['avg_return_pct']:+.2f}% | "
                         f"{s['account_growth_pct_10pct_sizing']:+.1f}% | {s['max_drawdown_pct']:.1f}% | {s['worst_losing_streak']} |")
    with open(os.path.join(out_dir, "backtest.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=3)
    args = ap.parse_args()
    from bot.alpaca import Alpaca
    api = Alpaca()
    start = (dt.date.today() - dt.timedelta(days=365 * args.years)).isoformat()
    # SIP history is allowed on the free plan for data older than 15 minutes.
    stock_bars = api.stock_daily_bars(S.STOCKS, start, feed="sip")
    crypto_bars = api.crypto_daily_bars(S.CRYPTO, start)
    purchases = None
    try:
        from bot import insider
        cache = insider.update(S.STOCKS, start, insider.load_cache())
        insider.save_cache(cache)
        purchases = insider.purchases_by_symbol(cache)
        print("insider purchase filings:", sum(len(v) for v in purchases.values()))
    except Exception as e:
        print("insider data unavailable:", e)
    data = report(run_stocks(stock_bars, purchases), run_crypto(crypto_bars), args.years)
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
