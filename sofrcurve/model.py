"""Monthly SOFR forward curve fitted to CME One-Month (SR1) and Three-Month (SR3) SOFR futures.

Model
-----
* Unknowns: a constant overnight SOFR level per *period* (percent, ACT/360).
    - Front: periods run between FOMC decision effective dates (decision day + 1), the same
      step structure CME uses for Term SOFR, so policy moves land on the right day.
    - Back (after the last published FOMC date): one period per calendar month.
  A business day's SOFR is its period's level; weekends/holidays carry the prior business day.
* Days already fixed use published NY Fed SOFR.
* SR1 (month M):  R = calendar-day arithmetic average of daily SOFR over M.
* SR3 (month M):  R = business-day compounded SOFR from the 3rd Wed of M (incl.) to the 3rd
  Wed of M+3 (excl.); contracts are named for the month their reference quarter *starts*.
* Futures rates exceed forward rates because futures are margined daily; each contract's
  rate is reduced by a Hull-White one-factor convexity adjustment before fitting.
* Levels are found by least squares on pricing errors in bp (weight 1 per contract) plus a
  curvature penalty (second differences of the levels, in bp): near-zero across FOMC
  periods, so policy steps are free, and heavier on the monthly back end so it is smooth.
* Beyond the last listed contract the last fitted level is held flat ("extrapolated").

Outputs per calendar month
--------------------------
forward_1m      : compounded SOFR over the month (~ the 1M Term SOFR fixing on the 1st)
avg_overnight   : simple average daily SOFR over the month (SR1 definition)
futures_implied : forward_1m refitted with no convexity adjustment
convexity_bp    : futures_implied - forward_1m
"""
from __future__ import annotations

import bisect
import datetime as dt
import math
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import least_squares

from . import calendar as cal
from .fomc import DECISION_DATES
from .sources import MarketData, Quote

DAY = dt.timedelta(days=1)


@dataclass
class Params:
    hw_sigma: float = 0.0090     # HW normal vol of the short rate (decimal, per year)
    hw_a: float = 0.03           # HW mean reversion (per year)
    horizon_months: int = 120    # months after the current month
    smooth_front: float = 0.003  # curvature penalty across FOMC periods (allows policy steps)
    smooth_back: float = 0.20    # curvature penalty across monthly back-end periods
    min_oi_serial: int = 1       # serial SR3 and SR1 need open interest >= this to be used


@dataclass
class Instrument:
    quote: Quote
    start: dt.date
    end: dt.date
    kind: str                     # "avg" (SR1) or "comp" (SR3)
    seg: tuple = None             # (period idx | -1 if fixed, fixed rate, days) arrays
    convexity: float = 0.0        # percent
    weight: float = 1.0

    @property
    def name(self) -> str:
        return f"{self.quote.product} {self.quote.label}"

    @property
    def days(self) -> int:
        return (self.end - self.start).days


@dataclass
class Curve:
    as_of: dt.date
    months: list[dict]
    instruments: list[dict]
    periods: list[dict]
    params: Params
    last_fixing: tuple[dt.date, float]
    stats: dict = field(default_factory=dict)


# ------------------------------------------------------------------ helpers

def hw_convexity(t1: float, t2: float, sigma: float, a: float) -> float:
    """Futures-minus-forward rate (decimal) for a rate over [t1, t2] in Hull-White 1F
    (Hull, *Options, Futures and Other Derivatives*, Eurodollar futures adjustment)."""
    t1 = max(t1, 0.0)
    if t2 <= t1:
        return 0.0
    B = lambda s, t: (1 - math.exp(-a * (t - s))) / a  # noqa: E731
    b12 = B(t1, t2)
    return (b12 / (t2 - t1)) * (b12 * (1 - math.exp(-2 * a * t1)) + 2 * a * B(0, t1) ** 2) * sigma ** 2 / (4 * a)


def contract_window(q: Quote) -> tuple[dt.date, dt.date, str]:
    if q.product == "SR1":
        return cal.month_start(q.year, q.month), cal.month_end_exclusive(q.year, q.month), "avg"
    y2, m2 = cal.add_months(q.year, q.month, 3)
    return cal.third_wednesday(q.year, q.month), cal.third_wednesday(y2, m2), "comp"


def build_periods(first_open: dt.date, last_needed: dt.date) -> tuple[list[dt.date], int]:
    """Period start dates and the number of FOMC (front) periods."""
    starts = [first_open]
    for d in DECISION_DATES:
        eff = d + DAY
        if eff > first_open:
            starts.append(eff)
    n_front = len(starts)
    # Monthly periods begin at the first month start >= 20 days after the last FOMC step.
    y, m = (starts[-1] + dt.timedelta(days=20)).year, (starts[-1] + dt.timedelta(days=20)).month
    if (starts[-1] + dt.timedelta(days=20)).day != 1:
        y, m = cal.add_months(y, m, 1)
    while cal.month_start(y, m) <= last_needed:
        starts.append(cal.month_start(y, m))
        y, m = cal.add_months(y, m, 1)
    return starts, n_front


class Segmenter:
    """Splits a date window into runs of days that share a rate-setting business day."""

    def __init__(self, fixings: dict[dt.date, float], starts: list[dt.date]):
        self.fixings = fixings
        self.last_fix = max(fixings)
        self.starts = starts
        self._sorted = sorted(fixings)

    def fixing_for(self, d: dt.date) -> float:
        if d in self.fixings:
            return self.fixings[d]
        i = bisect.bisect_right(self._sorted, d)  # data gap: carry latest earlier fixing
        if i == 0:
            raise ValueError(f"No SOFR fixing on or before {d}; extend the fixing history")
        return self.fixings[self._sorted[i - 1]]

    def period_index(self, d: dt.date) -> int:
        return max(bisect.bisect_right(self.starts, d) - 1, 0)

    def segments(self, start: dt.date, end: dt.date):
        idx, fixed, length = [], [], []
        d = start
        while d < end:
            setter = cal.prev_business_day(d)
            run_end = min(cal.next_business_day(setter), end)
            if setter <= self.last_fix:
                idx.append(-1); fixed.append(self.fixing_for(setter))
            else:
                idx.append(self.period_index(setter)); fixed.append(0.0)
            length.append((run_end - d).days)
            d = run_end
        return np.array(idx), np.array(fixed, float), np.array(length, float)


def _rates(idx, fixed, f):
    return np.where(idx >= 0, f[np.clip(idx, 0, None)], fixed)


def window_rate(kind: str, seg, f: np.ndarray, days: int) -> float:
    idx, fixed, length = seg
    r = _rates(idx, fixed, f)
    if kind == "avg":
        return float((r * length).sum() / days)
    return float((np.prod(1 + r * length / 36000.0) - 1) * 36000.0 / days)


def window_grad(kind: str, seg, f: np.ndarray, days: int) -> np.ndarray:
    idx, fixed, length = seg
    g = np.zeros_like(f)
    mask = idx >= 0
    if kind == "avg":
        np.add.at(g, idx[mask], length[mask] / days)
        return g
    x = _rates(idx, fixed, f) * length / 36000.0
    P = np.prod(1 + x)
    np.add.at(g, idx[mask], (P / days) * length[mask] / (1 + x[mask]))
    return g


# ------------------------------------------------------------------ build

def select_instruments(md: MarketData, p: Params) -> list[Instrument]:
    out = []
    last_fix = max(md.fixings)
    for q in md.quotes:
        start, end, kind = contract_window(q)
        if end <= last_fix + DAY:                      # fully fixed: nothing to learn
            continue
        quarterly = q.product == "SR3" and q.month in (3, 6, 9, 12)
        if not quarterly and q.open_interest < p.min_oi_serial:
            continue                                   # staff-settled, no open interest
        out.append(Instrument(quote=q, start=start, end=end, kind=kind))
    return sorted(out, key=lambda i: (i.start, i.quote.product))


def _fit(instr: list[Instrument], n: int, use_cvx: bool, smooth_w: np.ndarray, f0: np.ndarray):
    target = np.array([i.quote.rate - (i.convexity if use_cvx else 0.0) for i in instr])
    w = np.array([i.weight for i in instr])

    D2 = np.zeros((n - 2, n))
    for k in range(n - 2):
        D2[k, k:k + 3] = (1, -2, 1)
    S = np.sqrt(smooth_w)[:, None] * D2 * 100.0

    def resid(f):
        m = np.array([window_rate(i.kind, i.seg, f, i.days) for i in instr])
        return np.concatenate([(m - target) * 100.0 * w, S @ f])

    def jac(f):
        J = np.array([window_grad(i.kind, i.seg, f, i.days) for i in instr]) * 100.0 * w[:, None]
        return np.vstack([J, S])

    return least_squares(resid, f0, jac=jac, method="trf", xtol=1e-12, ftol=1e-12, gtol=1e-12).x


def build_curve(md: MarketData, p: Params | None = None) -> Curve:
    p = p or Params()
    as_of = md.trade_date
    last_fix = max(md.fixings)
    first_open = cal.next_business_day(last_fix)        # first business day without a fixing

    instr = select_instruments(md, p)
    last_end = max(i.end for i in instr)                # exclusive end of last contract
    starts, n_front = build_periods(first_open, last_end - DAY)
    n = len(starts)
    seg = Segmenter(md.fixings, starts)

    for i in instr:
        i.seg = seg.segments(i.start, i.end)
        t1, t2 = (i.start - as_of).days / 365.0, (i.end - as_of).days / 365.0
        i.convexity = hw_convexity(t1, t2, p.hw_sigma, p.hw_a) * 100.0

    smooth = np.array([p.smooth_front if k < n_front else p.smooth_back for k in range(n - 2)])
    f0 = np.full(n, float(np.median([i.quote.rate for i in instr])))
    f_adj = _fit(instr, n, True, smooth, f0)
    f_raw = _fit(instr, n, False, smooth, f_adj)

    # -------- monthly output table (current month + horizon)
    months = []
    for k in range(p.horizon_months + 1):
        y, m = cal.add_months(as_of.year, as_of.month, k)
        s, e = cal.month_start(y, m), cal.month_end_exclusive(y, m)
        sg, days = seg.segments(s, e), (e - s).days
        fwd = window_rate("comp", sg, f_adj, days)
        raw = window_rate("comp", sg, f_raw, days)
        avg = window_rate("avg", sg, f_adj, days)
        if (sg[0] == -1).all():
            status = "fixed"
        elif (sg[0] == -1).any():
            status = "partially_fixed"
        elif e > last_end + dt.timedelta(days=15):
            status = "extrapolated"
        else:
            status = "market"
        months.append({
            "month": f"{y:04d}-{m:02d}", "reset_date": s.isoformat(), "end_date": e.isoformat(),
            "days": days, "forward_1m": round(fwd, 5), "avg_overnight": round(avg, 5),
            "futures_implied": round(raw, 5), "convexity_bp": round((raw - fwd) * 100, 2) + 0.0,
            "status": status,
        })

    # -------- diagnostics
    rows, res = [], []
    for i in instr:
        model_fut = window_rate(i.kind, i.seg, f_adj, i.days) + i.convexity
        r_bp = (model_fut - i.quote.rate) * 100
        res.append((r_bp, i.quote.open_interest))
        rows.append({
            "contract": i.name, "product": i.quote.product,
            "ref_start": i.start.isoformat(), "ref_end": i.end.isoformat(),
            "settle": i.quote.settle, "implied_rate": round(i.quote.rate, 5),
            "convexity_bp": round(i.convexity * 100, 2), "model_rate": round(model_fut, 5),
            "residual_bp": round(r_bp, 3), "open_interest": i.quote.open_interest,
        })
    periods = [{"start": s.isoformat(),
                "end": (starts[k + 1] if k + 1 < n else last_end).isoformat(),
                "type": "fomc" if k < n_front else "monthly",
                "sofr": round(float(f_adj[k]), 5)} for k, s in enumerate(starts)]
    r = np.array([x for x, _ in res])
    liquid = np.array([abs(x) for x, oi in res if oi >= 100_000])
    stats = {
        "instruments": len(instr),
        "max_abs_residual_bp": round(float(np.abs(r).max()), 3),
        "rms_residual_bp": round(float(np.sqrt((r ** 2).mean())), 3),
        "max_abs_residual_liquid_bp": round(float(liquid.max()), 3) if liquid.size else None,
        "last_contract_end": last_end.isoformat(),
    }
    return Curve(as_of=as_of, months=months, instruments=rows, periods=periods, params=p,
                 last_fixing=(last_fix, md.fixings[last_fix]), stats=stats)
