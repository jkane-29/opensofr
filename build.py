"""Build and publish the SOFR forward curve.

    python build.py                       # fetch live CME + NY Fed data, write to ./output
    python build.py --snapshot FILE.json  # rebuild from a saved input snapshot (offline)

Every live run saves its raw inputs to data/inputs/<trade-date>.json so any published curve
can be rebuilt exactly.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sofrcurve.model import Params, build_curve
from sofrcurve.publish import publish
from sofrcurve.sources import MarketData, fetch_market_data

ROOT = Path(__file__).parent


def default_base_url() -> str:
    # Files are served straight from the repo's main branch.
    repo = os.environ.get("GITHUB_REPOSITORY", "jkane-29/opensofr")
    return f"https://raw.githubusercontent.com/{repo}/main/output"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", help="build from a saved MarketData JSON instead of fetching")
    ap.add_argument("--out", default=str(ROOT / "output"))
    ap.add_argument("--base-url", default=os.environ.get("BASE_URL") or default_base_url())
    ap.add_argument("--max-residual-bp", type=float, default=3.0,
                    help="refuse to publish if a liquid contract misprices by more than this")
    args = ap.parse_args(argv)

    if args.snapshot:
        md = MarketData.load(args.snapshot)
    else:
        md = fetch_market_data()
        inputs_dir = ROOT / "data" / "inputs"
        inputs_dir.mkdir(parents=True, exist_ok=True)
        (inputs_dir / f"{md.trade_date.isoformat()}.json").write_text(json.dumps(md.to_json(), indent=1))

    curve = build_curve(md, Params())
    worst = curve.stats["max_abs_residual_liquid_bp"] or 0.0
    if worst > args.max_residual_bp:
        print(f"Fit rejected: liquid contract residual {worst} bp > {args.max_residual_bp} bp", file=sys.stderr)
        return 2

    publish(curve, md, Path(args.out), args.base_url.rstrip("/"))
    m = curve.months
    print(f"as_of {curve.as_of}  months {len(m)}  "
          f"1y {m[12]['forward_1m']:.3f}%  5y {m[60]['forward_1m']:.3f}%  10y {m[120]['forward_1m']:.3f}%  "
          f"fit rms {curve.stats['rms_residual_bp']}bp max {curve.stats['max_abs_residual_bp']}bp")
    return 0


if __name__ == "__main__":
    sys.exit(main())
