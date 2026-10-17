"""Write the static API (JSON/CSV), history, and Excel download into the output directory."""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
from dataclasses import asdict
from pathlib import Path

from .excel import build_workbook
from .model import Curve
from .sources import MarketData

CSV_COLS = ["as_of", "month", "reset_date", "end_date", "days", "forward_1m", "avg_overnight",
            "futures_implied", "convexity_bp", "status"]


def annual(curve: Curve) -> list[dict]:
    by = {}
    for m in curve.months:
        by.setdefault(int(m["month"][:4]), []).append(m["forward_1m"])
    return [{"year": y, "months": len(v), "avg_forward_1m": round(sum(v) / len(v), 5)}
            for y, v in sorted(by.items())]


def curve_json(curve: Curve, md: MarketData, base_url: str) -> dict:
    return {
        "as_of": curve.as_of.isoformat(),
        "generated_at": dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "description": "Monthly SOFR forward curve fitted to CME SOFR futures, 10 years.",
        "units": "percent per annum, ACT/360",
        "fields": {
            "forward_1m": "compounded SOFR over the calendar month (~1M Term SOFR fixing on reset_date)",
            "avg_overnight": "simple average daily SOFR over the month",
            "futures_implied": "forward_1m fitted without convexity adjustment",
            "convexity_bp": "futures_implied - forward_1m, basis points",
            "status": "fixed | partially_fixed | market | extrapolated",
        },
        "last_sofr_fixing": {"date": curve.last_fixing[0].isoformat(), "rate": curve.last_fixing[1]},
        "model": {**asdict(curve.params), **curve.stats},
        "source": {"futures": "CME Group daily settlements: SR3 (8462), SR1 (8463)",
                   "fixings": "Federal Reserve Bank of New York Markets API",
                   "input_type": md.source.get("type")},
        "links": {
            "csv": f"{base_url}/api/v1/latest.csv",
            "xlsx": f"{base_url}/downloads/sofr-forward-curve.xlsx",
            "inputs": f"{base_url}/api/v1/inputs/latest.json",
            "history": f"{base_url}/api/v1/history/index.json",
        },
        "annual": annual(curve),
        "curve": curve.months,
    }


def curve_csv(curve: Curve) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLS, lineterminator="\n")
    w.writeheader()
    for m in curve.months:
        w.writerow({"as_of": curve.as_of.isoformat(), **m})
    return buf.getvalue()


def publish(curve: Curve, md: MarketData, out: Path, base_url: str) -> dict:
    api = out / "api" / "v1"
    hist = api / "history"
    for d in (api / "inputs", hist, out / "downloads"):
        d.mkdir(parents=True, exist_ok=True)

    doc = curve_json(curve, md, base_url)
    text = json.dumps(doc, indent=1)
    csv_text = curve_csv(curve)
    day = curve.as_of.isoformat()

    (api / "latest.json").write_text(text)
    (api / "latest.csv").write_text(csv_text)
    (hist / f"{day}.json").write_text(text)
    (hist / f"{day}.csv").write_text(csv_text)

    inputs = {"as_of": day, "stats": curve.stats, "instruments": curve.instruments,
              "periods": curve.periods,
              "sofr_fixings": [[d.isoformat(), r] for d, r in sorted(md.fixings.items())]}
    (api / "inputs" / "latest.json").write_text(json.dumps(inputs, indent=1))
    (api / "inputs" / f"{day}.json").write_text(json.dumps(inputs, indent=1))

    dates = sorted({p.stem for p in hist.glob("*.json") if p.stem != "index"}, reverse=True)
    (hist / "index.json").write_text(json.dumps({
        "dates": dates,
        "url_template": f"{base_url}/api/v1/history/{{date}}.json",
        "csv_template": f"{base_url}/api/v1/history/{{date}}.csv",
    }, indent=1))

    build_workbook(curve, md.fixings, base_url, str(out / "downloads" / "sofr-forward-curve.xlsx"))
    return doc
