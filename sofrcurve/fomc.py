"""FOMC policy-decision dates (second day of each meeting).

Source: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm (checked 2026-10-17).
Rate changes take effect the business day after the decision, so each date below starts a new
constant-rate period in the front of the curve. Add the next year's schedule when the Fed
publishes it (usually late summer); with no dates left the model simply falls back to monthly
periods, so a stale list degrades gracefully rather than breaking.
"""
import datetime as dt

D = dt.date

DECISION_DATES = [
    # 2026
    D(2026, 1, 28), D(2026, 3, 18), D(2026, 4, 29), D(2026, 6, 17),
    D(2026, 7, 29), D(2026, 9, 16), D(2026, 10, 28), D(2026, 12, 9),
    # 2027
    D(2027, 1, 27), D(2027, 3, 17), D(2027, 4, 28), D(2027, 6, 9),
    D(2027, 7, 28), D(2027, 9, 15), D(2027, 10, 27), D(2027, 12, 8),
    # 2028 (only January published so far)
    D(2028, 1, 26),
]
