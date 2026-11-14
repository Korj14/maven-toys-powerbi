# Data Dictionary | Maven Toys Analytics v2

## Conventions

- **Source (immutable):** The four CSVs in `data/`. The analytics scripts read but do not change them.
- **Derived:** Regenerable CSVs in `analytics/outputs_v03/`.
- **Currency:** USD, as represented in original listed product prices/costs.
- **Keys:** `Store_ID` and `Product_ID` are integer dimension keys. `Scenario_ID` is a text scenario key.
- **Grains:** *Sale*, *store*, *product*, *recorded store–product stock*, *store–product × 28-day forecast*, *store–product × planning scenario*, or *method × evaluation set*, as noted.
- **Types:** Listed as logical/Power BI types. For currency values, use Fixed Decimal Number where suitable. Floating estimates are Decimal Number; dates are Date. Display formatting does not change the stored type.

## 1. Original sources (unchanged)

### `data/sales.csv` — 829,262 rows; one recorded sale line

| Column | Type | Meaning |
|---|---|---|
| `Sale_ID` | Whole Number | Identifier of the recorded sale; all 829,262 values were distinct in the reported audit |
| `Date` | Date | Recorded sale date, 2017-01-01 to 2018-09-30 |
| `Store_ID` | Whole Number | Foreign key to Stores |
| `Product_ID` | Whole Number | Foreign key to Products |
| `Units` | Whole Number | Units sold on the record; source validation requires positive units |

### `data/products.csv` — 35 rows; one product

| Column | Type | Meaning |
|---|---|---|
| `Product_ID` | Whole Number | Unique product key |
| `Product_Name` | Text | Product label |
| `Product_Category` | Text | Category label |
| `Product_Cost` | Text in raw CSV → currency number after parsing | Listed product unit cost (raw CSV contains `$` and whitespace) |
| `Product_Price` | Text in raw CSV → currency number after parsing | Listed product unit selling price |

The Python pipeline derives `Unit_Cost` and `Unit_Price` as numeric values **in memory**; these are not added to the original CSV.

### `data/stores.csv` — 50 rows; one store

| Column | Type | Meaning |
|---|---|---|
| `Store_ID` | Whole Number | Unique store key |
| `Store_Name` | Text | Store display name |
| `Store_City` | Text | City as given in source data |
| `Store_Location` | Text | Store location type: Airport, Commercial, Downtown, Residential |
| `Store_Open_Date` | Date | Store opening date |

### `data/inventory.csv` — 1,593 rows; one recorded store–product combination

| Column | Type | Meaning |
|---|---|---|
| `Store_ID` | Whole Number | Store key |
| `Product_ID` | Whole Number | Product key |
| `Stock_On_Hand` | Whole Number | Snapshot stock units; cannot be negative per validation |

**Important:** The full 50 × 35 store–product grid has **1,750** possible pairs, but only **1,593** appear in inventory. The **157 absent pairs are not zero-stock observations**. The inventory date is an **assumption**, not a timestamp field in this CSV.

### `Calendar` — Power BI generated date table

Used with `Sales[Date]` for descriptive trends. The confirmed linking field is `Calendar[Date]` (Date). Other Calendar columns depend on the specific Power BI implementation; they are not source CSV fields and are not asserted here.

## 2. Generated predictive outputs

### `analytics/outputs_v03/Forecast_28D.csv` — 1,750 rows; one store–product and one 28-day window

| Column | Type | Meaning |
|---|---|---|
| `Store_ID` | Whole Number | Store key |
| `Product_ID` | Whole Number | Product key |
| `Forecast_Origin` | Date | Last historical observation: 2018-09-30 |
| `Forecast_Start` | Date | First forecast date: 2018-10-01 |
| `Forecast_End` | Date | Last forecast date: 2018-10-28 |
| `Forecast_Units_28D` | Decimal Number (2 display decimals) | Model-estimated sum of units for the next 28 days |
| `Forecast_Std_Units_28D` | Decimal Number | Heuristic standard deviation associated with 28-day unit estimate; not empirically calibrated |
| `Indicative_Lower_Units_28D` | Decimal Number | Nonnegative heuristic lower bound, **not validated prediction interval** |
| `Indicative_Upper_Units_28D` | Decimal Number | Heuristic upper bound, **not validated prediction interval** |
| `Actual_Units_Previous_28D` | Whole Number | Recorded units in the 28 days ending on the origin date |
| `Forecast_Revenue_USD` | Fixed Decimal Number / USD | Forecast units × listed product price |
| `Forecast_Gross_Profit_USD` | Fixed Decimal Number / USD | Forecast units × (listed price − listed cost) |
| `Selected_Method` | Text | Name of selected forecasting method (`pooled_blend_50`) |
| `Model_Version` | Text | Generator version (`0.3.0-experimental`) |

**Not daily:** Each row covers the entire 28-day horizon. All combinations carry the same start/end window.

### `analytics/outputs_v03/Forecast_Backtest_Metrics.csv` — 18 rows; method × evaluation set

| Column | Type | Meaning |
|---|---|---|
| `Evaluation_Set` | Text | `tuning` or `holdout_seen_before` |
| `Method` | Text | Candidate method name (nine candidates in v0.3) |
| `WAPE` | Decimal Number → Percentage display | Total absolute unit error / total actual unit sales across specified windows and store–product pairs |
| `MAE_Units_28D` | Decimal Number | Mean absolute unit error per store–product × 28-day validation window |
| `Bias_Units_28D` | Decimal Number | Mean (forecast units − actual units) per pair-window |
| `Total_Actual_Units` | Whole Number / numeric sum | Actual units aggregated across evaluation windows |
| `Total_Forecast_Units` | Decimal Number | Forecast units aggregated across evaluation windows |
| `Series_Windows` | Whole Number | Number of store–product series-window evaluations (1,750 per window) |

**Interpretation:** The observed September holdout was examined during model development. It is reported as exploratory; its WAPE is not conditional on a product/store slicer in the dashboard.

### `analytics/outputs_v03/Forecast_By_Origin.csv` — 63 rows; validation origin × candidate method

| Column | Type | Meaning |
|---|---|---|
| `Evaluation_Set` | Text | Tuning or previously seen holdout |
| `Method` | Text | Candidate forecast name |
| `Origin` | Date | Forecast cutoff date for that window |
| `Actual_Units` | Decimal Number / numeric sum | Total actual units across all 1,750 pairs in the 28-day window |
| `Predicted_Units` | Decimal Number | Total forecast units across those pairs |
| `Absolute_Error_Units` | Decimal Number | Sum of absolute **pair-level** errors (not absolute error of grand total) |
| `Absolute_Error_Per_Pair` | Decimal Number | Mean absolute pair-level error within the window |
| `Bias_Units_Per_Pair` | Decimal Number | Mean signed (forecast − actual) pair-level error |
| `Series_Count` | Whole Number | Number of evaluated pairs, normally 1,750 |

This file is retained for audit; the five-page Power BI report does not require a direct relationship to it.

## 3. Generated prescriptive outputs

### `analytics/outputs_v03/Scenario_Assumptions.csv` — 3 rows; one scenario

| Column | Type | Meaning |
|---|---|---|
| `Scenario_ID` | Text | `S1_Lean`, `S2_Base`, or `S3_Resilience` |
| `Lead_Time_Days` | Whole Number | Assumed number of days until ordered stock is received |
| `Review_Period_Days` | Whole Number | Assumed planning window after delivery |
| `Target_Service_Level` | Decimal Number → Percentage display | Target **simulation quantile**, not a measured service level |
| `Purchase_Budget_USD` | Fixed Decimal Number / USD | Maximum allowed illustrative order spend |
| `Assumption_Status` | Text | `Illustrative, not observed` |

### `analytics/outputs_v03/Replenishment_Recommendations.csv` — 4,779 rows; recorded store–product × scenario

| Column | Type | Meaning |
|---|---|---|
| `Store_ID` | Whole Number | Store key |
| `Product_ID` | Whole Number | Product key |
| `Stock_On_Hand` | Whole Number | Source stock snapshot units |
| `Forecast_Units_28D` | Decimal Number | Units forecast for next 28 days, copied from predictive output |
| `Forecast_Origin` | Date | 2018-09-30 |
| `Forecast_Start` | Date | 2018-10-01 |
| `Forecast_End` | Date | 2018-10-28 |
| `Scenario_ID` | Text | Foreign key to Scenario_Assumptions |
| `Mean_Lead_Time_Demand_Units` | Decimal Number | Forecast mean scaled to supplier lead-time days |
| `Mean_Review_Period_Demand_Units` | Decimal Number | Forecast mean scaled to post-delivery review days |
| `Projected_Stock_At_Receipt_Units` | Decimal Number | Monte Carlo mean of nonnegative stock remaining when order arrives |
| `Expected_Shortage_Before_Receipt_Units` | Decimal Number | Expected shortage **during supplier lead time**, unaffected by the new order |
| `Expected_Shortage_Review_Without_Order_Units` | Decimal Number | Expected post-delivery review-period unmet units with no order |
| `Expected_Shortage_Review_After_Order_Units` | Decimal Number | Expected post-delivery review-period unmet units after allocated order arrives |
| `Target_Stock_Units` | Whole Number | Simulated target quantile of review-period demand (informational) |
| `Ideal_Order_Units` | Whole Number | Quantile-based unconstrained candidate reorder units |
| `Budgeted_Order_Units` | Whole Number | Greedy-allocated reorder quantity under the selected budget |
| `Unit_Cost` | Fixed Decimal Number / USD | Listed cost of one unit |
| `Unit_Margin` | Fixed Decimal Number / USD | Nonnegative (listed unit price − listed unit cost) |
| `Ideal_Spend_USD` | Fixed Decimal Number / USD | Ideal order units × listed unit cost |
| `Recommended_Spend_USD` | Fixed Decimal Number / USD | Budgeted units × listed unit cost |
| `Shortage_Units_No_Order` | Decimal Number | Expected total lead + review shortage with no new order |
| `Shortage_Units_After_Order` | Decimal Number | Expected total lead + review shortage with new order, delivered after lead time |
| `Modeled_Avoided_Shortage_Units` | Decimal Number | Nonnegative difference between the previous two modeled shortages |
| `Modeled_Protected_Gross_Profit_USD` | Fixed Decimal Number / USD | Simulated avoided shortage units × listed unit margin; **not realized profit** |
| `Lead_Time_Days` | Whole Number | Scenario lead-time setting duplicated for row-level interpretability |
| `Review_Period_Days` | Whole Number | Scenario review-period setting duplicated for row-level interpretability |
| `Assumption_Not_Observed` | True/False | `True`: decision parameters are not observed supplier facts |
| `Model_Version` | Text | Generator version |

### `analytics/outputs_v03/Model_Audit.json`

JSON metadata, not a Power BI fact table. Records source SHA-256 hashes, source profiling, chosen method, tuning and exploratory holdout scores, selection provenance, seed/draw count, simulation limits, output row counts, scenario spending and deployment status. **Treat this as the canonical record of a particular pipeline execution**; the documentation describes its fields and the user-reported v0.3 result, not a separately maintained parallel run ledger.

## 4. Model relationships

| Dimension (one side) | Fact (many side) | Key | Notes |
|---|---|---|---|
| Calendar | Sales | Date | Historical only |
| Products | Sales | Product_ID | Historical sales |
| Stores | Sales | Store_ID | Historical sales |
| Products | Inventory | Product_ID | Recorded stock |
| Stores | Inventory | Store_ID | Recorded stock |
| Products | Forecast_28D | Product_ID | All 1,750 combinations |
| Stores | Forecast_28D | Store_ID | All 1,750 combinations |
| Products | Replenishment_Recommendations | Product_ID | Recorded inventory pairs × scenarios |
| Stores | Replenishment_Recommendations | Store_ID | Recorded inventory pairs × scenarios |
| Scenario_Assumptions | Replenishment_Recommendations | Scenario_ID | Exactly one scenario should be selected in Page 5 |

`Forecast_Backtest_Metrics` remains **disconnected**. Neither new fact table should be directly linked to the other fact or to the original Sales/Inventory fact tables. Avoid treating `Forecast_Start` as a daily fact key.

## 5. Recommended Power BI formatting

- Money (`*_USD`, `Unit_Cost`, `Unit_Margin`): USD Currency, **2** decimal places.
- Probabilities and ratios (`WAPE`, `Target_Service_Level`, DAX margin/change/shortage ratios): Percentage, **2** decimal places.
- Predicted and expected units: Decimal Number, **2** display decimals (precision retained for calculation).
- Recorded stock, integer order units, days and counts: Whole Number, **0** decimals.
- Dates: Date displayed as `yyyy-MM-dd`.
- IDs: Integer/text identifiers, **not** currency or aggregatable business KPIs; set **Don't summarize** when used in report visuals.

[← README](../README.md) · [Methodology](methodology.md) · [Validation](validation.md)
