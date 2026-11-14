"""Minimal read-only Alpaca market-data client.

The API key is read from environment variables (set from GitHub Secrets):
  ALPACA_KEY_ID, ALPACA_SECRET_KEY
This module never places orders.
"""
import os
import time

import requests

DATA = "https://data.alpaca.markets"


class Alpaca:
    def __init__(self, key_id=None, secret=None):
        self.key_id = key_id or os.environ.get("ALPACA_KEY_ID", "")
        self.secret = secret or os.environ.get("ALPACA_SECRET_KEY", "")
        if not self.key_id or not self.secret:
            raise SystemExit("Missing ALPACA_KEY_ID / ALPACA_SECRET_KEY (add them as GitHub Secrets).")
        if not self.key_id.startswith("PK"):
            raise SystemExit("Refusing to run: key does not look like a PAPER key (should start with 'PK').")
        self.s = requests.Session()
        self.s.headers.update({"APCA-API-KEY-ID": self.key_id, "APCA-API-SECRET-KEY": self.secret})

    def _get(self, path, params=None):
        for attempt in range(4):
            r = self.s.get(DATA + path, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(2 + attempt * 3)
                continue
            r.raise_for_status()
            return r.json()
        r.raise_for_status()

    # ---------- stocks ----------
    def stock_daily_bars(self, symbols, start, end=None, feed="iex"):
        """Return {symbol: [bar,...]} of daily bars (t,o,h,l,c,v)."""
        out = {}
        for i in range(0, len(symbols), 50):
            params = {"symbols": ",".join(symbols[i:i + 50]), "timeframe": "1Day", "start": start,
                      "limit": 10000, "adjustment": "split", "feed": feed}
            if end:
                params["end"] = end
            while True:
                j = self._get("/v2/stocks/bars", params)
                for sym, bars in (j.get("bars") or {}).items():
                    out.setdefault(sym, []).extend(bars)
                tok = j.get("next_page_token")
                if not tok:
                    break
                params["page_token"] = tok
        return out

    def stock_snapshots(self, symbols, feed="iex"):
        out = {}
        for i in range(0, len(symbols), 100):
            out.update(self._get("/v2/stocks/snapshots", {"symbols": ",".join(symbols[i:i + 100]), "feed": feed}) or {})
        return out

    def movers(self, top=25):
        return self._get("/v1beta1/screener/stocks/movers", {"top": top})

    def most_actives(self, top=25):
        return self._get("/v1beta1/screener/stocks/most-actives", {"by": "volume", "top": top})

    # ---------- crypto ----------
    def crypto_daily_bars(self, symbols, start, end=None):
        out = {}
        params = {"symbols": ",".join(symbols), "timeframe": "1Day", "start": start, "limit": 10000}
        if end:
            params["end"] = end
        while True:
            j = self._get("/v1beta3/crypto/us/bars", params)
            for sym, bars in (j.get("bars") or {}).items():
                out.setdefault(sym, []).extend(bars)
            tok = j.get("next_page_token")
            if not tok:
                break
            params["page_token"] = tok
        return out

    def crypto_snapshots(self, symbols):
        return (self._get("/v1beta3/crypto/us/snapshots", {"symbols": ",".join(symbols)}) or {}).get("snapshots", {})
