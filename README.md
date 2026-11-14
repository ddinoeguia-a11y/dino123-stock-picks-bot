# dino123-stock-picks-bot

Paper-tracking scanner for same-day stock and crypto trade ideas. **It never places trades.**

## How it works
- `bot/backtest.py` tests each same-day rule on ~3 years of daily history (worst-case fills, 0.05% cost per side) → `results/backtest.md`
- `bot/scan.py` runs before the open and every 15 min during market hours, finds setups, and writes `results/latest_scan.json`. Ideas from rules that made money in the backtest are marked `"proven": true`.
- GitHub Actions run both on a schedule (`.github/workflows/`). Claude scheduled tasks read the results, add news/insider context, and send push notifications.

## Setup
Add two repository secrets (Settings → Secrets and variables → Actions):
- `ALPACA_KEY_ID` (paper key, starts with `PK`)
- `ALPACA_SECRET_KEY`

The code refuses to run with a non-paper key.

## Notes
- Schedules are in UTC and set for US Central *standard* time; they drift one hour during daylight saving time.
- Not financial advice.
