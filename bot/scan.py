"""Live scan: find today's same-day setups in stocks and crypto.

Usage: python -m bot.scan
Writes results/latest_scan.json (overwritten each run) and appends to results/scan_log.jsonl
Only strategies whose backtest win rate and average return are positive are marked "proven".
"""
import datetime as dt
import json
import os

from bot import strategy as S


def load_backtest(path="results/backtest.json"):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def proven(bt, market, name):
    s = (bt or {}).get(market, {}).get(name) or {}
    return bool(s.get("trades", 0) >= 30 and s.get("avg_return_pct", 0) > 0)


def build_stock_ideas(snaps, daily, bt):
    ideas = []
    for sym, snap in snaps.items():
        bars = daily.get(sym) or []
        prev = snap.get("prevDailyBar") or {}
        day = snap.get("dailyBar") or {}
        last = (snap.get("latestTrade") or {}).get("p") or day.get("c")
        if not prev.get("c") or not last or last < 5:
            continue
        a = S.atr(bars)
        if not a:
            continue
        open_ = day.get("o") or last
        gap = open_ / prev["c"] - 1
        rvol = (day.get("v") or 0) / max(S.avg_volume(bars), 1)
        for name in S.stock_signals(gap):
            rule = S.STOCK_RULES[name]
            # Only take gap-and-go if price is holding above the open, fade if below.
            if name == "gap_and_go" and last < open_:
                continue
            if name == "gap_fade" and last > open_:
                continue
            stop, target = S.levels(last, a, rule)
            st = (bt or {}).get("stocks", {}).get(name, {})
            ideas.append({
                "market": "stock", "symbol": sym, "strategy": name, "side": rule["side"],
                "price": round(last, 2), "entry_zone": [round(last * 0.998, 2), round(last * 1.002, 2)],
                "stop": round(stop, 2), "target": round(target, 2),
                "gap_pct": round(100 * gap, 2), "rel_volume": round(rvol, 2),
                "volatility": "volatile" if a / last > 0.04 else "steady",
                "backtest_win_rate": st.get("win_rate"), "backtest_avg_pct": st.get("avg_return_pct"),
                "proven": proven(bt, "stocks", name),
                "score": round(abs(gap) * 100 * max(rvol, 0.1), 2),
            })
    return ideas


def build_crypto_ideas(snaps, daily, bt):
    ideas = []
    for sym, snap in snaps.items():
        bars = daily.get(sym) or []
        if len(bars) < 3:
            continue
        a = S.atr(bars)
        last = (snap.get("latestTrade") or {}).get("p") or bars[-1]["c"]
        prev_ret = bars[-1]["c"] / bars[-2]["c"] - 1
        for name in S.crypto_signals(prev_ret):
            rule = S.CRYPTO_RULES[name]
            stop, target = S.levels(last, a, rule)
            st = (bt or {}).get("crypto", {}).get(name, {})
            ideas.append({
                "market": "crypto", "symbol": sym, "strategy": name, "side": rule["side"],
                "price": round(last, 4), "stop": round(stop, 4), "target": round(target, 4),
                "prev_day_pct": round(100 * prev_ret, 2),
                "volatility": "volatile" if a / last > 0.05 else "steady",
                "backtest_win_rate": st.get("win_rate"), "backtest_avg_pct": st.get("avg_return_pct"),
                "proven": proven(bt, "crypto", name),
                "score": round(abs(prev_ret) * 100, 2),
            })
    return ideas


def main(out_dir="results"):
    from bot.alpaca import Alpaca
    api = Alpaca()
    bt = load_backtest(os.path.join(out_dir, "backtest.json"))
    start = (dt.date.today() - dt.timedelta(days=60)).isoformat()

    # Candidate stocks: today's movers + most active + our core list.
    cands = set(S.STOCKS)
    try:
        mv = api.movers(top=25)
        cands |= {x["symbol"] for x in mv.get("gainers", []) + mv.get("losers", [])}
        cands |= {x["symbol"] for x in api.most_actives(top=25).get("most_actives", [])}
    except Exception as e:  # screener can be unavailable pre-market
        print("screener unavailable:", e)
    cands = sorted(s for s in cands if s.isalpha())
    snaps = api.stock_snapshots(cands)
    daily = api.stock_daily_bars(cands, start)
    stock_ideas = build_stock_ideas(snaps, daily, bt)

    c_snaps = api.crypto_snapshots(S.CRYPTO)
    c_daily = api.crypto_daily_bars(S.CRYPTO, start)
    crypto_ideas = build_crypto_ideas(c_snaps, c_daily, bt)

    # Insider buying (SEC Form 4) for the stocks that produced ideas.
    try:
        from bot import insider
        syms = sorted({x["symbol"] for x in stock_ideas})
        since = (dt.date.today() - dt.timedelta(days=S.INSIDER_BOOST_LOOKBACK)).isoformat()
        cache = insider.update(syms, since, insider.load_cache())
        insider.save_cache(cache)
        purchases = insider.purchases_by_symbol(cache)
        today = dt.date.today().isoformat()
        for x in stock_ideas:
            n7, v7 = insider.recent_buying(purchases, x["symbol"], today, 7)
            n30, v30 = insider.recent_buying(purchases, x["symbol"], today, S.INSIDER_BOOST_LOOKBACK)
            x["insider_buyers_7d"], x["insider_buyers_30d"], x["insider_dollars_30d"] = n7, n30, v30
    except Exception as e:
        print("insider data unavailable:", e)

    # Insider buying ranks buy-side setups higher (promising in backtest, small sample).
    for x in stock_ideas:
        x["insider_boost"] = bool(x.get("insider_buyers_30d")) and x["side"] == "long"
    for x in stock_ideas + crypto_ideas:
        x["label"] = "proven" if x["proven"] else "not proven"
    rank = lambda xs: sorted(xs, key=lambda x: (not x["proven"], not x.get("insider_boost"), -x["score"]))
    result = {
        "generated_utc": dt.datetime.utcnow().isoformat(timespec="seconds"),
        "note": "Paper tracking only. Ideas, not orders.",
        "stocks": rank(stock_ideas)[:10],
        "crypto": rank(crypto_ideas)[:5],
        "watch_prices": {s: round((snaps.get(s, {}).get("latestTrade") or {}).get("p") or 0, 4) for s in cands},
        "crypto_prices": {s: (c_snaps.get(s, {}).get("latestTrade") or {}).get("p") for s in S.CRYPTO},
    }
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "latest_scan.json"), "w") as f:
        json.dump(result, f, indent=2)
    with open(os.path.join(out_dir, "scan_log.jsonl"), "a") as f:
        f.write(json.dumps({k: result[k] for k in ("generated_utc", "stocks", "crypto")}) + "\n")
    print(json.dumps({k: result[k] for k in ("generated_utc", "stocks", "crypto")}, indent=2))


if __name__ == "__main__":
    main()
