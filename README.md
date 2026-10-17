# OpenSOFR

A monthly SOFR forward curve covering the next ten years. It is refitted every US business morning to CME SOFR futures settlements. A GitHub Action commits the results to `output/` in this repo, and they're served from there as a static JSON/CSV API and an Excel download.

| What | URL |
|---|---|
| Latest curve (JSON) | https://raw.githubusercontent.com/jkane-29/opensofr/main/output/api/v1/latest.json |
| Latest curve (CSV) | https://raw.githubusercontent.com/jkane-29/opensofr/main/output/api/v1/latest.csv |
| Excel workbook | https://raw.githubusercontent.com/jkane-29/opensofr/main/output/downloads/sofr-forward-curve.xlsx |
| Archive index | https://raw.githubusercontent.com/jkane-29/opensofr/main/output/api/v1/history/index.json |
| Curve on a given date | `.../api/v1/history/YYYY-MM-DD.json` (or `.csv`) |
| Fit inputs and residuals | https://raw.githubusercontent.com/jkane-29/opensofr/main/output/api/v1/inputs/latest.json |

These are raw GitHub URLs on a public repo, so no authentication is needed. You can call them from Python, Excel Power Query, or Google Sheets (`=IMPORTDATA(".../latest.csv")`). Raw URLs are CDN-cached for up to about 5 minutes after each daily commit.

## Curve fields

There is one row per calendar month. The first row is the current month and the last is 120 months later. Rates are in percent per annum, ACT/360.

| Field | Meaning |
|---|---|
| `reset_date` / `end_date` | First day of the month / first day of the next month |
| `forward_1m` | SOFR compounded over the month. This is approximately the 1M Term SOFR fixing on `reset_date`. |
| `avg_overnight` | Simple average of daily SOFR over the month, which is what an SR1 contract settles on |
| `futures_implied` | `forward_1m` refitted with no convexity adjustment |
| `convexity_bp` | `futures_implied - forward_1m`, in basis points |
| `status` | `partially_fixed` (contains published fixings), `market`, or `extrapolated` (after the last listed contract) |

## Excel

The workbook contains the following sheets:

- **Forward Curve:** the monthly curve and a chart, plus spread, floor and cap inputs that compute an all-in rate for each month
- **Annual:** calendar-year averages
- **Futures Inputs:** every contract used in the fit, with its pricing residual
- **SOFR Fixings:** recent published SOFR
- **Methodology**

To keep a workbook live, use *Data > From Web* with the CSV URL, then *Refresh All*.

## Method

- **Inputs:** CME Three-Month (SR3) and One-Month (SR1) SOFR futures daily settlements, plus New York Fed SOFR fixings for days that have already elapsed.
- **Contract definitions:** SR1 settles on the calendar-day average of SOFR in its month. SR3 settles on business-day compounded SOFR from the 3rd Wednesday of its named month to the 3rd Wednesday three months later.
- **Front end:** overnight SOFR is held constant between FOMC decisions, and each change takes effect the day after the meeting. This is the same step structure CME uses for Term SOFR. The dates are listed in `sofrcurve/fomc.py`.
- **Back end:** after the last scheduled FOMC meeting, the curve has one level per month.
- **Fitting:** the levels are a least-squares fit to every quarterly SR3 contract and to each serial SR3 and SR1 contract that has open interest. A curvature penalty keeps the back end smooth. On the 2026-10-16 data the fit uses 59 contracts and reprices them with an RMS error of 0.22 bp and a maximum error of 0.7 bp.
- **Convexity:** each futures rate is reduced by a Hull-White one-factor adjustment (σ = 0.90% normal, a = 0.03). The adjustment is about 3 bp at 3 years, 9 bp at 5 years and 28 bp at 10 years. It is a model choice and the main source of uncertainty past year five. The parameters are in `sofrcurve/model.py` (`Params`).
- **Long end:** SR3 contracts beyond about 7 years have little or no open interest, and CME sets their settlements from spreads. Months after the last listed contract (currently June 2036) hold its level flat.

`forward_1m` is a close proxy for CME 1M Term SOFR but it is not Term SOFR. Term SOFR is a licensed CME benchmark.

## How it runs

`.github/workflows/update.yml` runs at 13:17 UTC from Tuesday to Saturday. Each run:

1. fetches CME settlements and NY Fed SOFR
2. fits the curve
3. writes `output/` and `data/inputs/<date>.json`
4. commits those files

A push of code changes only runs the tests. To fetch and rebuild immediately, use *Actions > Update curve > Run workflow*.

Safety rails:

- The build refuses to publish if any liquid contract misprices by more than 3 bp.
- If CME or the NY Fed can't be reached, the job fails and the previous curve stays up. GitHub emails you when a scheduled job fails.

## Local use

```bash
pip install -r requirements.txt pytest
python -m pytest -q tests
python build.py --snapshot data/inputs/2026-10-16.json   # offline rebuild into ./output
```

To fetch live data instead, run `python build.py` with no arguments.

## Maintenance

- **FOMC dates:** each year, when the Fed publishes the next year's meeting schedule, add the dates to `sofrcurve/fomc.py`. If the list runs out, the model falls back to monthly periods.
- **CME blocking:** CME sometimes blocks automated requests, including requests from cloud IP ranges. If the scheduled job starts failing on the CME step, you can save a settlements file by hand in the `data/inputs` format and push it. Alternatively, switch the source to a licensed feed such as CME DataMine.

Market data © CME Group and the Federal Reserve Bank of New York. Use of CME settlement data is subject to CME's terms. This is not investment advice.
