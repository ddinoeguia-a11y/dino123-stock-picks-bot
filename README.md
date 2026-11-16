# dino123-stock-picks-bot

Scanner for same-day stock and crypto trade ideas. `bot/trade.py` places **paper** trades on Alpaca for proven stock setups (hard-coded to the paper endpoint; refuses non-paper keys).

## How it works
- `bot/backtest.py` tests each same-day rule on ~3 years of daily history (worst-case fills, 0.05% cost per side) → `results/backtest.md`
- `bot/scan.py` runs before the open and every 15 min during market hours, finds setups, and writes `results/latest_scan.json`. Ideas from rules that made money in the backtest are marked `"proven": true`.
- GitHub Actions run both on a schedule (`.github/workflows/`). Claude scheduled tasks read the results, add news/insider context, and send push notifications.

## Paper trading
- After each scan, `bot/trade.py` places bracket orders (limit entry, take-profit, stop-loss) for up to 4 proven stock setups at once, 5% of equity each (max $5,000), max 6 new trades a day.
- No new entries in the last hour; unfilled entries are cancelled after 30 min; everything is closed in the last 20 min before the close (`.github/workflows/flatten.yml` adds extra end-of-day runs).
- Crypto is not traded yet.
- Log: `results/trades_log.jsonl`; account snapshot: `results/paper_account.json`.
- **Off switch:** Settings → Secrets and variables → Actions → Variables → add `AUTO_TRADE` = `off`.

## Setup
Add two repository secrets (Settings → Secrets and variables → Actions):
- `ALPACA_KEY_ID` (paper key, starts with `PK`)
- `ALPACA_SECRET_KEY`

The code refuses to run with a non-paper key.

## Notes
- Schedules are in UTC and set for US Central *standard* time; they drift one hour during daylight saving time.
- Not financial advice.
