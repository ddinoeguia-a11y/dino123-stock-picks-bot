"""Place PAPER trades on Alpaca from the latest scan.

Usage:
  python -m bot.trade            # place new entries (if allowed) and flatten near the close
  python -m bot.trade --flatten  # only flatten near the close

Safety:
- Orders only ever go to the PAPER endpoint below. The address is hard-coded, and the
  key must be a paper key (starts with "PK"), so this can never trade a live account.
- Turn trading off any time: GitHub repo Settings -> Secrets and variables -> Actions ->
  Variables -> set AUTO_TRADE to "off".
- Stocks only, "proven" setups only, whole shares, a bracket order on every entry
  (entry limit + take-profit + stop-loss), everything closed before the market closes.
"""
import datetime as dt
import json
import os
import sys

import requests

PAPER = "https://paper-api.alpaca.markets"  # never change this to the live address

# ---- risk settings ----
MAX_POSITIONS = 4            # most stock positions open at once
PCT_EQUITY_PER_TRADE = 0.05  # 5% of account equity per trade
MAX_DOLLARS_PER_TRADE = 5000
MAX_NEW_PER_DAY = 6
NO_NEW_ENTRIES_MIN_BEFORE_CLOSE = 60
FLATTEN_MIN_BEFORE_CLOSE = 20
CANCEL_UNFILLED_AFTER_MIN = 30
TAG = "dsp"                  # prefix on every order this bot places


class Paper:
    def __init__(self):
        key = os.environ.get("ALPACA_KEY_ID", "")
        sec = os.environ.get("ALPACA_SECRET_KEY", "")
        if not key or not sec:
            raise SystemExit("Missing ALPACA_KEY_ID / ALPACA_SECRET_KEY.")
        if not key.startswith("PK"):
            raise SystemExit("Refusing to trade: not a PAPER key (should start with 'PK').")
        self.s = requests.Session()
        self.s.headers.update({"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec})

    def req(self, method, path, **kw):
        r = self.s.request(method, PAPER + path, timeout=30, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.text else None


def log(event, **kw):
    os.makedirs("results", exist_ok=True)
    row = {"utc": dt.datetime.utcnow().isoformat(timespec="seconds"), "event": event, **kw}
    with open("results/trades_log.jsonl", "a") as f:
        f.write(json.dumps(row) + "\n")
    print(json.dumps(row))


def parse_t(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def ours(o):
    return (o.get("client_order_id") or "").startswith(TAG + "-")


def flatten(api):
    for o in api.req("GET", "/v2/orders", params={"status": "open", "limit": 500}) or []:
        if ours(o):
            api.req("DELETE", f"/v2/orders/{o['id']}")
    for p in api.req("GET", "/v2/positions") or []:
        if p.get("asset_class") == "us_equity":
            api.req("DELETE", f"/v2/positions/{p['symbol']}")
            log("close", symbol=p["symbol"], qty=p["qty"], unrealized_pl=p.get("unrealized_pl"),
                reason="end of day")


def cancel_stale_entries(api, now):
    for o in api.req("GET", "/v2/orders", params={"status": "open", "limit": 500}) or []:
        if ours(o) and o.get("status") in ("new", "accepted", "pending_new") and float(o.get("filled_qty") or 0) == 0:
            if (now - parse_t(o["submitted_at"])).total_seconds() > CANCEL_UNFILLED_AFTER_MIN * 60:
                api.req("DELETE", f"/v2/orders/{o['id']}")
                log("cancel", symbol=o["symbol"], reason="entry not filled in time")


def place_entries(api, now):
    try:
        with open("results/latest_scan.json") as f:
            scan = json.load(f)
    except Exception:
        print("no scan file")
        return
    age_min = (now - parse_t(scan["generated_utc"] + "+00:00")).total_seconds() / 60
    if age_min > 20:
        print(f"scan is {age_min:.0f} min old, skipping entries")
        return

    acct = api.req("GET", "/v2/account")
    equity = float(acct["equity"])
    size = min(equity * PCT_EQUITY_PER_TRADE, MAX_DOLLARS_PER_TRADE)
    positions = {p["symbol"] for p in api.req("GET", "/v2/positions") or []}
    open_orders = {o["symbol"] for o in api.req("GET", "/v2/orders", params={"status": "open", "limit": 500}) or []}
    today = now.date().isoformat()
    todays = [o for o in api.req("GET", "/v2/orders", params={"status": "all", "after": today + "T00:00:00Z", "limit": 500}) or []
              if ours(o) and o.get("order_class") == "bracket"]
    traded_today = {o["symbol"] for o in todays}
    slots = MAX_POSITIONS - len(positions | open_orders)
    left_today = MAX_NEW_PER_DAY - len(todays)

    for idea in scan.get("stocks", []):
        if slots <= 0 or left_today <= 0:
            break
        sym = idea["symbol"]
        if not idea.get("proven") or sym in positions or sym in open_orders or sym in traded_today:
            continue
        side = "buy" if idea["side"] == "long" else "sell"
        lo, hi = idea.get("entry_zone") or [idea["price"], idea["price"]]
        limit = hi if side == "buy" else lo
        qty = int(size // limit)
        if qty < 1:
            continue
        try:
            asset = api.req("GET", f"/v2/assets/{sym}")
            if not asset.get("tradable"):
                continue
            if side == "sell" and not (asset.get("shortable") and asset.get("easy_to_borrow")):
                continue
            order = api.req("POST", "/v2/orders", json={
                "symbol": sym, "qty": str(qty), "side": side, "type": "limit",
                "limit_price": str(round(limit, 2)), "time_in_force": "day",
                "order_class": "bracket",
                "take_profit": {"limit_price": str(round(idea["target"], 2))},
                "stop_loss": {"stop_price": str(round(idea["stop"], 2))},
                "client_order_id": f"{TAG}-{today}-{sym}",
            })
            slots -= 1
            left_today -= 1
            log("enter", symbol=sym, side=side, qty=qty, limit=round(limit, 2), target=idea["target"],
                stop=idea["stop"], strategy=idea.get("strategy"), order_id=order["id"])
        except Exception as e:
            log("error", symbol=sym, error=str(e)[:300])


def snapshot(api):
    acct = api.req("GET", "/v2/account")
    pos = api.req("GET", "/v2/positions") or []
    with open("results/paper_account.json", "w") as f:
        json.dump({
            "utc": dt.datetime.utcnow().isoformat(timespec="seconds"),
            "equity": acct["equity"], "cash": acct["cash"], "last_equity": acct["last_equity"],
            "positions": [{k: p.get(k) for k in ("symbol", "qty", "side", "avg_entry_price", "current_price",
                                                 "unrealized_pl", "unrealized_plpc")} for p in pos],
        }, f, indent=2)


def main():
    if os.environ.get("AUTO_TRADE", "on").strip().lower() == "off":
        print("AUTO_TRADE is off; not trading.")
        return
    flatten_only = "--flatten" in sys.argv
    api = Paper()
    clock = api.req("GET", "/v2/clock")
    now = parse_t(clock["timestamp"]).astimezone(dt.timezone.utc)
    if clock["is_open"]:
        to_close = (parse_t(clock["next_close"]) - now).total_seconds() / 60
        if to_close <= FLATTEN_MIN_BEFORE_CLOSE:
            flatten(api)
        elif not flatten_only:
            cancel_stale_entries(api, now)
            if to_close > NO_NEW_ENTRIES_MIN_BEFORE_CLOSE:
                place_entries(api, now)
    else:
        print("market closed")
    snapshot(api)


if __name__ == "__main__":
    main()
