"""Market-data inputs: CME SOFR futures settlements and NY Fed SOFR fixings."""
from __future__ import annotations

import datetime as dt
import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}

CME_URL = "https://www.cmegroup.com/CmeWS/mvc/Settlements/Futures/Settlements/{pid}/FUT?tradeDate={d}"
CME_PRODUCTS = {"SR3": 8462, "SR1": 8463}
NYFED_URL = ("https://markets.newyorkfed.org/api/rates/secured/sofr/search.json"
             "?startDate={start}&endDate={end}")

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.cmegroup.com/markets/interest-rates/stirs/three-month-sofr.settlements.html",
}


@dataclass
class Quote:
    product: str        # "SR3" or "SR1"
    label: str          # e.g. "DEC 26"
    year: int
    month: int
    settle: float       # IMM index price
    open_interest: int

    @property
    def rate(self) -> float:
        return 100.0 - self.settle


@dataclass
class MarketData:
    trade_date: dt.date
    quotes: list[Quote]
    fixings: dict[dt.date, float]          # SOFR, percent
    source: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "trade_date": self.trade_date.isoformat(),
            "source": self.source,
            "quotes": [asdict(q) for q in self.quotes],
            "sofr": [[d.isoformat(), r] for d, r in sorted(self.fixings.items())],
        }

    @classmethod
    def from_json(cls, obj: dict) -> "MarketData":
        if "quotes" in obj:
            quotes = [Quote(**q) for q in obj["quotes"]]
        else:  # compact fixture format
            quotes = [_quote(p, lbl, px, oi) for p in ("sr3", "sr1") for lbl, px, oi in obj[p]]
        return cls(
            trade_date=dt.date.fromisoformat(obj["trade_date"]),
            quotes=quotes,
            fixings={dt.date.fromisoformat(d): float(r) for d, r in obj["sofr"]},
            source=obj.get("source", {"type": "snapshot"}),
        )

    @classmethod
    def load(cls, path: str | Path) -> "MarketData":
        return cls.from_json(json.loads(Path(path).read_text()))


def _quote(product: str, label: str, settle, oi) -> Quote:
    mon, yy = label.split()
    return Quote(product=product.upper(), label=label, year=2000 + int(yy), month=MONTHS[mon],
                 settle=float(settle), open_interest=int(str(oi).replace(",", "") or 0))


def _clean_price(s: str) -> float | None:
    s = (s or "").strip().rstrip("AB")
    if s in ("", "-"):
        return None
    return float(s.replace(",", ""))


# ---------------------------------------------------------------- live fetchers

def _get_json(session, url: str, tries: int = 3) -> dict:
    last = None
    for i in range(tries):
        try:
            r = session.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200:
                return r.json()
            last = RuntimeError(f"HTTP {r.status_code} from {url}")
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(2 * (i + 1))
    raise last  # type: ignore[misc]


def fetch_cme(session, product: str, trade_date: dt.date) -> tuple[list[Quote], dict]:
    url = CME_URL.format(pid=CME_PRODUCTS[product], d=trade_date.strftime("%m/%d/%Y"))
    data = _get_json(session, url)
    if data.get("empty") or not data.get("settlements"):
        return [], data
    out = []
    for row in data["settlements"]:
        if row.get("month") == "Total":
            continue
        px = _clean_price(row.get("settle"))
        if px is None:
            continue
        out.append(_quote(product, row["month"], px, row.get("openInterest", "0")))
    return out, {"reportType": data.get("reportType"), "updateTime": data.get("updateTime")}


def fetch_latest_cme(session, today: dt.date, lookback_days: int = 7) -> tuple[dt.date, list[Quote], dict]:
    """Walk back from `today` to the most recent trade date with SR3 settlements."""
    for k in range(lookback_days + 1):
        d = today - dt.timedelta(days=k)
        if d.weekday() >= 5:
            continue
        sr3, meta3 = fetch_cme(session, "SR3", d)
        if len(sr3) < 20:
            continue
        sr1, meta1 = fetch_cme(session, "SR1", d)
        return d, sr3 + sr1, {"SR3": meta3, "SR1": meta1}
    raise RuntimeError("No CME SOFR futures settlements found in lookback window")


def fetch_sofr(session, start: dt.date, end: dt.date) -> dict[dt.date, float]:
    data = _get_json(session, NYFED_URL.format(start=start.isoformat(), end=end.isoformat()))
    return {dt.date.fromisoformat(r["effectiveDate"]): float(r["percentRate"])
            for r in data.get("refRates", []) if r.get("type", "SOFR") == "SOFR"}


def fetch_market_data(today: dt.date | None = None) -> MarketData:
    import requests

    today = today or dt.date.today()
    s = requests.Session()
    trade_date, quotes, meta = fetch_latest_cme(s, today)
    # Fixings back far enough to cover any in-progress 3M reference quarter.
    fixings = fetch_sofr(s, today - dt.timedelta(days=150), today)
    # Only fixings dated on/before the trade date are "known" at the settlement close.
    fixings = {d: r for d, r in fixings.items() if d <= trade_date}
    return MarketData(trade_date, quotes, fixings,
                      source={"type": "live", "cme": meta, "fetched_at": dt.datetime.utcnow().isoformat() + "Z"})
