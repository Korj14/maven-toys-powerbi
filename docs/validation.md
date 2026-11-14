# Validation & Reproducibility | Maven Toys Analytics v2

## 1. Verification status

**Evidence classification**

| Area | Status | Source of evidence |
|---|---|---|
| Descriptive source sizes and inventory figures | Audited in development | Original CSVs / Python pipeline source audits |
| Forecast code and v0.3 algorithm | Reviewed in supplied script | `analytics/scripts/build_analytics_v03.py` and imported helper scripts |
| v0.3 executed output numbers | **User-reported successful local execution** | CLI output supplied during development |
| Five-page Power BI report checks | **User-reported passed** | User completed Page 4 and Page 5 validation checklists |
| Independently reopened final v2 PBIX/PDF | **Not yet done** | Final v2 artifacts have not been separately provided for inspection |
| Independent forecast test on *unseen* dates | **Not available** | September 2018 period was already examined before final model selection |
| Real-world effect of replenishment | **Not established** | No future stock/demand or actual ordering outcomes |

The model must be described as **experimental**, and its output as a **retrospective October 2018 scenario**. A passed dashboard checklist does not establish real-world business validity.

## 2. Requirements and file layout

- Python **3.10+**.
- Dependencies in `analytics/requirements.txt`: `numpy>=1.24,<3`, `pandas>=2.0,<3`, `scipy>=1.11,<2`.
- Data files must be readable at `data/sales.csv`, `data/products.csv`, `data/stores.csv`, `data/inventory.csv`.
- Keep `build_analytics.py`, `diagnose_forecast.py`, and `build_analytics_v03.py` **together** in `analytics/scripts/` because the latest script imports the earlier two.
- `analytics/scenario_inputs_template.csv` specifies all three scenarios. The script does not alter these inputs.

From the root of the cloned repository (Windows PowerShell):

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r analytics/requirements.txt

python analytics/scripts/build_analytics_v03.py `
  --data-dir data `
  --output-dir analytics/outputs_v03 `
  --scenario-csv analytics/scenario_inputs_template.csv
```

On other shells, replace PowerShell continuation backticks with an appropriate syntax or use a single line. The script overwrites the **derived output files in the selected output directory**, not source CSVs; preserve prior audits if comparing multiple runs. Use a new output directory for independent comparisons.

### Run output checklist

The console should report:

| Key | Expected user-reported v0.3 result |
|---|---|
| `selected_method` | `pooled_blend_50` |
| `tuning_WAPE` | `0.5571306347920202` |
| `holdout_WAPE_exploratory` | `0.6217788126632177` |
| `holdout_WAPE_last_28d_exploratory` | `0.700697987150206` |
| `tuning_origin_wins_over_previous_out_of_six` | `5` |
| `forecast_rows` | `1750` |
| `recommendation_rows` | `4779` |
| `deployment_status` | `EXPERIMENTAL_SCENARIO_ONLY` |

Expected illustrative spending (`scenario_totals_USD`):

| Scenario | Spend |
|---|---:|
| `S1_Lean` | `$14,999.56` |
| `S2_Base` | `$24,999.77` |
| `S3_Resilience` | `$39,999.49` |

The v0.3 method selects only from the **six tuning windows**; the previously examined September window is displayed as an **exploratory** score. A result that fails to outperform a simple baseline in a fresh, independent evaluation should not be advertised as predictive improvement.

## 3. Source integrity checks

`Model_Audit.json` holds per-file SHA-256 hashes. On any rerun, compare hashes from the same four inputs rather than updating or normalizing the source files in place.

Expected structural checks, reported from the executed pipeline:

| Check | Expected |
|---|---:|
| Recorded sales rows | 829,262 |
| Distinct Sale_ID values | 829,262 |
| Total recorded units sold | 1,090,565 |
| Min / max sale dates | 2017-01-01 / 2018-09-30 |
| Products | 35 |
| Stores | 50 |
| Inventory rows | 1,593 |
| Total stock on hand | 29,742 |
| Recorded zero-stock pairs | 77 |
| Recorded low-stock (1–10 units) pairs | 603 |
| Potential but unrecorded stock pairs | 157 |

**Never convert missing inventory rows to stockouts**. Stockout rate is `77/1,593 ≈ 4.83%`, not `77/1,750`, nor `(77+157)/1,750`.

### Verify output sizes in PowerShell

From the repository root:

```powershell
(Import-Csv analytics/outputs_v03/Forecast_28D.csv | Measure-Object).Count
(Import-Csv analytics/outputs_v03/Forecast_Backtest_Metrics.csv | Measure-Object).Count
(Import-Csv analytics/outputs_v03/Forecast_By_Origin.csv | Measure-Object).Count
(Import-Csv analytics/outputs_v03/Scenario_Assumptions.csv | Measure-Object).Count
(Import-Csv analytics/outputs_v03/Replenishment_Recommendations.csv | Measure-Object).Count
```

Expected: **1,750**, **18**, **63**, **3**, **4,779**, in that order.

### Verify business invariant and budget constraints

```powershell
python -c "import pandas as pd; p='analytics/outputs_v03/'; r=pd.read_csv(p+'Replenishment_Recommendations.csv'); s=pd.read_csv(p+'Scenario_Assumptions.csv'); assert (r.Budgeted_Order_Units>=0).all(); assert (r.Shortage_Units_After_Order>=r.Expected_Shortage_Before_Receipt_Units-0.001).all(); assert (r.Shortage_Units_After_Order<=r.Shortage_Units_No_Order+0.001).all(); totals=r.groupby('Scenario_ID').Recommended_Spend_USD.sum(); budgets=s.set_index('Scenario_ID').Purchase_Budget_USD; assert (totals<=budgets+0.02).all(); print('Scenario arithmetic checks passed')"
```

These checks verify **internal consistency**, not accuracy of the simulated shortage distribution.

## 4. Power BI checks

The PBIX should have five functioning report pages:

1. Executive Overview
2. Product & Store Analysis
3. Inventory Insights
4. Predictive Analytics
5. Prescriptive Analytics

### Predictive page

- Source table `Forecast_28D`: 1,750 rows; forecast origin **2018-09-30** and horizon **2018-10-01 to 2018-10-28**; method `pooled_blend_50`.
- `Forecast_Backtest_Metrics`: 18 rows; the measure filtered to `Evaluation_Set = "holdout_seen_before"` and `Method = "pooled_blend_50"` should read **62.18%** WAPE.
- Product-category and store-location slicers filter forecast KPIs, charts and detail table through Products/Stores relationships.
- The global WAPE card must **not imply** a segmented accuracy rate when slicers change.
- Forecast units and estimated monetary totals match the full-period output and do not falsely display a daily time series.

### Prescriptive page

- `Scenario_Assumptions`: 3 rows; scenario slicer is **single-select**.
- `Replenishment_Recommendations`: 4,779 rows (1,593 recorded combinations × three alternatives).
- With no product/store filters, scenario spend agrees with the three amounts above.
- Choosing a different scenario changes measures, chart values, and positive-order recommendation details.
- Filter the recommendation table to **Reorder Units > 0**.
- **Modeled shortage after order ≤ modeled shortage without order**, with **pre-receipt shortage not eliminated by orders**.
- Currency measures display in USD; `Modeled_Protected_Gross_Profit_USD` is described as **simulated exposure**, not realized incremental earnings.

### Structural checks

- The five descriptive relationships remain active.
- The five additional relationships connect Products/Stores to forecast and recommendation tables, plus scenario dimension to recommendations; cross-filter direction is single from dimensions to facts.
- The global backtest metrics table is disconnected.
- All three original descriptive tabs remain functional and can be independently filtered.
- The report and five-page PDF exports reflect the **v2** layout and updated screenshot names.

**Status:** The user reported all Page 4 and Page 5 checks passed. A separately supplied v2 PBIX/PDF would be needed for independent inspection of the finished artifact.

## 5. Known compatibility warnings

On the user's environment, NumPy emitted a `DeprecationWarning` about generic timedelta units in the scripts. This **did not stop the successful v0.3 run**. For a later maintenance release, normalize date arithmetic to explicit units and rerun regression checks before changing the methodology. Do not suppress warnings merely to suggest they were resolved.

## 6. Reproducibility and distribution cautions

- **Do not commit `.venv/`**, Python `__pycache__/` or local scratch/diagnostic output directories unless intentionally archiving evidence. Keep the final derived artifacts and audit together.
- Opening the Power BI report on another machine may require **updating file paths for both original and derived CSV sources**. Power BI refresh does not rerun the Python generation command.
- The `.pbip` pointer from the original repository referred to an absent report folder; do not advertise it as a self-contained Power BI project unless the matching `.Report/` and `.SemanticModel/` definitions are actually included.
- **Final v2 report files, new screenshots and docs may exist only in a local working tree** until explicitly committed and pushed. Verify that the remote GitHub tree has all artifacts before citing the remote version as completed.
- No post-October-2018 factual replenishment results are available. Do not claim realized avoided stockouts, supplier performance, calibrated uncertainty or optimal procurement.

## 7. Release checklist

- [ ] V2 PBIX opens with all five pages and slicers working.
- [ ] Five-page PDF export opens and matches the report.
- [ ] Eight screenshot files exist with names linked in README.
- [ ] Original four CSVs have matching SHA-256 hashes; no source edits.
- [ ] Analytics scripts and requirements are committed, including required helper imports.
- [ ] All six v0.3 outputs and `Model_Audit.json` are committed and readable.
- [ ] README and all three docs are populated; all links resolve from GitHub.
- [ ] Stale `.pbip` reference is corrected or removed if incomplete.
- [ ] `CITATION.cff` and release version are updated consistently when releasing v2.
- [ ] LICENSE policy is stated explicitly; source dataset public-domain status is not treated as permission to relicense original code/report artifacts.
- [ ] Final release notes distinguish **historical simulation** from production validation.

[← README](../README.md) · [Methodology](methodology.md) · [Data dictionary](data_dictionary.md)
