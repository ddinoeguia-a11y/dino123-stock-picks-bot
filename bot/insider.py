"""SEC Form 4 insider purchases from EDGAR (free, no API key).

Only open-market PURCHASES (transaction code "P") count. Grants, option
exercises and sales are ignored. Signals use the FILING date (when the public
could first see it), never the trade date, so backtests don't peek ahead.

Results are cached in results/insider_cache.json so each run only fetches new filings.
"""
import datetime as dt
import json
import os
import time
import xml.etree.ElementTree as ET

import requests

UA = {"User-Agent": "dino123-stock-picks-bot " + os.environ.get("SEC_CONTACT_EMAIL", ""), "Accept-Encoding": "gzip, deflate"}
CACHE = "results/insider_cache.json"
_s = requests.Session()
_s.headers.update(UA)
_last = [0.0]


def _get(url):
    # SEC allows max 10 requests/second.
    wait = 0.12 - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    for attempt in range(4):
        _last[0] = time.time()
        r = _s.get(url, timeout=30)
        if r.status_code in (429, 503):
            time.sleep(2 + 3 * attempt)
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()


def ticker_ciks():
    j = _get("https://www.sec.gov/files/company_tickers.json").json()
    return {v["ticker"].upper(): int(v["cik_str"]) for v in j.values()}


def _parse_form4(xml_text):
    """Return (list of buyer names, total $ bought) for open-market purchases."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return [], 0.0
    owners = [o.findtext("reportingOwnerId/rptOwnerName", "").strip() for o in root.findall("reportingOwner")]
    total = 0.0
    for t in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        if t.findtext("transactionCoding/transactionCode", "").strip() != "P":
            continue
        try:
            sh = float(t.findtext("transactionAmounts/transactionShares/value", "0"))
            px = float(t.findtext("transactionAmounts/transactionPricePerShare/value", "0") or 0)
        except ValueError:
            continue
        total += sh * px
    return (owners if total > 0 else []), total


def load_cache(path=CACHE):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def save_cache(cache, path=CACHE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(cache, f)


def update(symbols, since, cache=None, ciks=None):
    """Fetch Form 4 filings for symbols filed on/after `since` (YYYY-MM-DD) into cache.

    cache maps accession -> {"sym", "filed", "buyers", "value"}.
    """
    cache = cache if cache is not None else load_cache()
    ciks = ciks or ticker_ciks()
    for sym in symbols:
        cik = ciks.get(sym.upper())
        if not cik:
            continue  # ETFs etc. have no insiders
        try:
            sub = _get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json").json()
        except Exception as e:
            print("skip", sym, e)
            continue
        rec = sub.get("filings", {}).get("recent", {})
        for form, acc, filed, doc in zip(rec.get("form", []), rec.get("accessionNumber", []),
                                         rec.get("filingDate", []), rec.get("primaryDocument", [])):
            if form != "4" or filed < since or acc in cache:
                continue
            raw = doc.split("/")[-1]  # strip the xsl rendering folder to get the raw XML
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{raw}"
            try:
                buyers, value = _parse_form4(_get(url).text)
            except Exception:
                buyers, value = [], 0.0
            cache[acc] = {"sym": sym.upper(), "filed": filed, "buyers": buyers, "value": round(value)}
    return cache


def purchases_by_symbol(cache):
    """{sym: [(filed_date, buyers, value), ...]} for purchase filings only."""
    out = {}
    for v in cache.values():
        if v["buyers"]:
            out.setdefault(v["sym"], []).append((v["filed"], v["buyers"], v["value"]))
    for k in out:
        out[k].sort()
    return out


def recent_buying(purchases, sym, day, lookback_days):
    """Distinct insider buyers and $ bought in filings dated in [day - lookback, day - 1]."""
    d = dt.date.fromisoformat(day[:10])
    lo = (d - dt.timedelta(days=lookback_days)).isoformat()
    hi = (d - dt.timedelta(days=1)).isoformat()
    names, value = set(), 0
    for filed, buyers, val in purchases.get(sym, []):
        if lo <= filed <= hi:
            names.update(buyers)
            value += val
    return len(names), value
