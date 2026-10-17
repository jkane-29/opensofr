import datetime as dt
from pathlib import Path

import numpy as np
import pytest

from sofrcurve import calendar as cal
from sofrcurve.model import Params, build_curve, hw_convexity
from sofrcurve.sources import MarketData

FIX = Path(__file__).parent / "fixtures" / "snapshot_2026-10-16.json"


@pytest.fixture(scope="module")
def md():
    return MarketData.load(FIX)


@pytest.fixture(scope="module")
def curve(md):
    return build_curve(md, Params())


def test_calendar_matches_published_fixing_dates(md):
    d, end, bds = dt.date(2026, 7, 1), dt.date(2026, 10, 15), []
    while d <= end:
        if cal.is_business_day(d):
            bds.append(d)
        d += dt.timedelta(days=1)
    assert bds == sorted(md.fixings)  # includes Jul 3, Labor Day, Columbus Day closures


def test_imm_dates():
    assert cal.third_wednesday(2026, 9) == dt.date(2026, 9, 16)
    assert cal.third_wednesday(2026, 12) == dt.date(2026, 12, 16)
    assert cal.third_wednesday(2024, 6) == dt.date(2024, 6, 19)


def test_horizon_is_ten_years(curve):
    assert len(curve.months) == 121
    assert curve.months[0]["month"] == "2026-10"
    assert curve.months[-1]["month"] == "2036-10"


def test_fit_reprices_futures(curve):
    assert curve.stats["max_abs_residual_liquid_bp"] < 1.0
    assert curve.stats["rms_residual_bp"] < 0.5


def test_sr3_compounding_matches_brute_force(curve):
    """Re-price SR3 DEC 26 day by day from the fitted period levels and published fixings."""
    periods = curve.periods
    starts = [dt.date.fromisoformat(p["start"]) for p in periods]
    levels = [p["sofr"] for p in periods]
    fixings = MarketData.load(FIX).fixings

    def sofr(bd):
        if bd in fixings:
            return fixings[bd]
        k = max(i for i, s in enumerate(starts) if s <= bd)
        return levels[k]

    inst = next(i for i in curve.instruments if i["contract"] == "SR3 DEC 26")
    s, e = dt.date.fromisoformat(inst["ref_start"]), dt.date.fromisoformat(inst["ref_end"])
    growth, d = 1.0, s
    while d < e:
        if cal.is_business_day(d):
            n = (min(cal.next_business_day(d), e) - d).days
            growth *= 1 + sofr(d) / 100 * n / 360
        d += dt.timedelta(days=1)
    brute = (growth - 1) * 360 / (e - s).days * 100
    model_fwd = inst["model_rate"] - inst["convexity_bp"] / 100
    assert abs(brute - model_fwd) < 0.002  # < 0.2bp; differences are rounding of stored levels


def test_jul26_contract_is_mostly_realized(curve):
    inst = next(i for i in curve.instruments if i["contract"] == "SR3 JUL 26")
    assert abs(inst["residual_bp"]) < 0.5


def test_convexity_shape():
    assert hw_convexity(0, 0.25, 0.009, 0.03) == 0
    c = [hw_convexity(t, t + 0.25, 0.009, 0.03) for t in (1, 3, 5, 10)]
    assert all(np.diff(c) > 0)
    assert 0.0015 < c[-1] < 0.0040  # 15-40bp at 10y


def test_status_flags(curve):
    st = [m["status"] for m in curve.months]
    assert st[0] == "partially_fixed"
    assert st[-1] == "extrapolated"
    assert "market" in st


def test_no_negative_zero(curve):
    assert all(str(m["convexity_bp"]) != "-0.0" for m in curve.months)
