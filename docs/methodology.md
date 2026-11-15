# Methodology | Maven Toys Analytics v2

## 1. Scope and evidence hierarchy

This is a **retrospective analytics demonstration**, not a live retail planning system. The unchanged Maven Analytics source files (`data/*.csv`) are the historical evidence. Derived model outputs in `analytics/outputs_v03/` are generated artifacts. The Power BI report presents source and derived values; its visuals do not constitute independent ground truth.

- Sales observation window: **2017-01-01 to 2018-09-30**.
- Historical inventory: **one stock snapshot**, assumed as of **2018-10-01**.
- Forecast origin: **2018-09-30**; forecast horizon: **2018-10-01 to 2018-10-28 (28 days)**.
- Data granularity: sale records; one inventory row per *recorded* store–product combination; one 28-day forecast row per possible store–product combination; one recommendation row per *recorded* inventory combination × scenario.
- Financial unit: USD as supplied by the dataset. Prices and costs are product-level static values.

The original four CSVs must not be modified. `Model_Audit.json` records their SHA-256 hashes and the output generation settings.

## 2. Descriptive analytics

Four Power Query source tables support five documented active, single-direction, one-to-many relationships:

1. `Calendar[Date]` (1) → `Sales[Date]` (*).
2. `Products[Product_ID]` (1) → `Sales[Product_ID]` (*).
3. `Stores[Store_ID]` (1) → `Sales[Store_ID]` (*).
4. `Products[Product_ID]` (1) → `Inventory[Product_ID]` (*).
5. `Stores[Store_ID]` (1) → `Inventory[Store_ID]` (*).

Core metric definitions:

| Metric | Definition | Interpretation |
|---|---|---|
| Total revenue | Sum of units sold × listed product unit price | Modeled historical sales at static prices |
| Total cost | Sum of units sold × listed product unit cost | Modeled merchandise cost; not total operating cost |
| Gross profit | Revenue − total cost | Not net profit |
| Gross margin | Gross profit / revenue | Weighted ratio, not average of product margins |
| Units sold | Sum of sales `Units` | Recorded units, potentially censored by unavailable stock |
| Average transaction value | Revenue / distinct `Sale_ID` | Per recorded sale ID, not necessarily a customer basket |
| Inventory value at cost | Sum of `Stock_On_Hand × listed unit cost` | Snapshot book value using supplied costs |
| Out-of-stock rate | Recorded inventory rows with 0 stock / recorded inventory rows | Does **not** include absent combinations |
| Low-stock count | Recorded combinations with stock 1–10 | Descriptive threshold, not validated reorder point |

Do not infer historical lost sales, supplier performance, or replenishment events from this snapshot.

## 3. Predictive analytics

### 3.1 Target, universe and aggregation

The target is **units sold over the next 28 days** for each of **50 stores × 35 products = 1,750** combinations. The code aggregates historical transactions into store–product unit totals for each rolling window; combinations with no recorded sales in a window receive a zero for that window.

A forecast row contains `Forecast_Units_28D`, a standard-deviation heuristic, two indicative bounds, previous-28-day actual units, and derived revenue/gross-profit figures. There are **no daily forecast observations** in the output.

Derived financial estimates are:

\[
\widehat{Revenue}_{s,p} = \widehat{Units}_{s,p,28d} \times Price_p
\]

\[
\widehat{GrossProfit}_{s,p} = \widehat{Units}_{s,p,28d} \times (Price_p - Cost_p)
\]

No returns, historical price variation, promotions or overheads are modeled.

### 3.2 Candidate methods

`build_analytics.py` implements historical-window baselines:

- `last_28d`: units in the most recent 28 days.
- `last_56d`: units in the most recent 56 days, scaled to 28 days.
- `last_84d`: units in the most recent 84 days, scaled to 28 days.
- `blend_28_84`: `0.65 × last_28d + 0.35 × last_84d`.

`diagnose_forecast.py` additionally implements:

- `pooled_28`: uses the last-28-day *store totals* and *product totals* to distribute aggregate expected sales over the store–product grid: `pooled[s,p] = store_total[s] × product_total[p] / grand_total` (or 0 if grand total is 0).
- `pooled_blend_25`, `_50`, `_75`: `(1 − w) × blend_28_84 + w × pooled_28`, where `w` is `0.25`, `0.50` or `0.75`.
- `seasonal_364d`: same 28-day horizon shifted 364 days backward, when complete prior-year data exists.

The selected `pooled_blend_50` model combines **50% blended local historical demand** with **50% pooled store/product demand**. Pooling reduces noise but assumes store and product shares provide useful information; it may overlook genuine local product preferences.

### 3.3 Tuning and historical evaluation

`build_analytics_v03.py` evaluates candidate forecasts at seven non-overlapping, 28-day forward horizons:

| Group | Forecasted period | Role |
|---|---|---|
| k=7 | 2018-03-19 to 2018-04-15 | Tuning |
| k=6 | 2018-04-16 to 2018-05-13 | Tuning |
| k=5 | 2018-05-14 to 2018-06-10 | Tuning |
| k=4 | 2018-06-11 to 2018-07-08 | Tuning |
| k=3 | 2018-07-09 to 2018-08-05 | Tuning |
| k=2 | 2018-08-06 to 2018-09-02 | Tuning |
| k=1 | 2018-09-03 to 2018-09-30 | **Exploratory holdout, previously inspected** |

Forecasts at each origin use only sales data available *through that origin*. Candidate selection minimizes pooled **store–product WAPE across the six tuning windows**. The previously examined September window is scored after selection but is **not a new blind test** and must not be advertised as independent deployment validation.

\[
\mathrm{WAPE} = \frac{\sum_{windows,s,p}|Actual_{s,p}-Forecast_{s,p}|}{\sum_{windows,s,p}Actual_{s,p}}
\]

`MAE_Units_28D` is mean absolute error per store–product × window; `Bias_Units_28D` is mean `(forecast − actual)` per store–product × window. WAPE reported at an aggregated level will generally differ from store–product WAPE because errors can cancel upon aggregation.

Observed results from the user's v0.3 execution:

| Evaluation | Store–product WAPE |
|---|---:|
| `pooled_blend_50`, six tuning windows | **55.7131%** |
| Previous `last_84d`, six tuning windows | **57.9979%** (approximately) |
| `pooled_blend_50`, already-inspected September window | **62.1779%** |
| `last_28d`, same September window | **70.0698%** |

The selected method beat `last_84d` on absolute error for **five of six** tuning windows. These are historical comparisons, not guaranteed future performance. Error remains substantial at fine granularity.

### 3.4 Indicative uncertainty (not calibrated)

Residuals from tuning windows are standardized by `sqrt(max(predicted units, 1))` to estimate a global dispersion scale. For a 28-day forecast `mu`, the pipeline sets:

\[
\sigma_{28} = scale \times \sqrt{\max(\mu,1)}
\]

It reports indicative bounds `max(0, mu − 1.645 × sigma)` and `mu + 1.645 × sigma`. These bounds are heuristic; **no empirical coverage validation** establishes that they are reliable confidence or prediction intervals.

## 4. Prescriptive analytics

### 4.1 Inputs and decision scope

The recommendation model joins 28-day forecasts with the **1,593 recorded** store–product stock rows, and uses each product's listed cost and margin. It excludes **157 unrecorded** combinations from ordering recommendations; missing records are not labeled zero stock.

Three illustrative planning scenarios are loaded from `analytics/scenario_inputs_template.csv`:

| Scenario | Lead days | Review days | Target quantile | Budget (USD) |
|---|---:|---:|---:|---:|
| `S1_Lean` | 7 | 7 | 0.90 | 15,000 |
| `S2_Base` | 7 | 7 | 0.95 | 25,000 |
| `S3_Resilience` | 14 | 7 | 0.98 | 40,000 |

The scenario target is an input to a simulated demand quantile; it is **not an observed or verified fill rate**.

### 4.2 Demand before and after delivery

For each store–product and scenario, the model uses a 28-day forecast `(mu_28, sigma_28)` and scales mean demand to each period:

\[
\mu_{lead} = \mu_{28} \times LeadDays/28,\quad
\mu_{review} = \mu_{28} \times ReviewDays/28
\]

Standard deviations are heuristically scaled by the square root of period length. `512` seeded random normal draws (`seed = 2481`) are rounded and truncated at zero to simulate units demanded **before delivery** and in the **post-delivery review period**. This is a scenario device, not a calibrated demand process. Lead days + review days must fit within 28 days.

For a simulated draw:

- `StockAtReceipt = max(0, StartingStock − DemandBeforeDelivery)`.
- `ShortageBeforeReceipt = max(0, DemandBeforeDelivery − StartingStock)`.
- `PostDeliveryGap = ReviewDemand − StockAtReceipt`.
- `PostDeliveryShortage(q) = max(0, PostDeliveryGap − q)` for an order of `q` units.

Total shortage without an order = pre-delivery shortage + post-delivery shortage at `q = 0`.

Total shortage after an order = **the same pre-delivery shortage** + post-delivery shortage after `q` units arrive.

Therefore **orders cannot retroactively prevent shortages while waiting for delivery**. Expectations average the simulated draws.

### 4.3 Reorder quantity and budget allocation

For each recorded store–product pair:

1. **Ideal order units** are the nonnegative ceiling of the simulated `Target_Service_Level` quantile of the post-delivery gap.
2. A greedy algorithm increments purchased units while budget remains, ranking next units by estimated **marginal protected product gross margin / unit purchase cost**.
3. The algorithm never allocates more than ideal units and respects total scenario spending budget (subject to currency rounding tolerance).

The algorithm is **approximate**. It is not an exact integer-programming solution, and does not model pack sizes, minimum order quantities, transport, replenishment transfers, supplier contracts, fixed ordering costs or shelf capacity.

Reported outcome measures:

| Measure | Meaning |
|---|---|
| `Budgeted_Order_Units` | Whole units allocated under the chosen illustrative budget |
| `Recommended_Spend_USD` | Ordered units × listed unit cost |
| `Shortage_Units_No_Order` | Simulated expected shortage across lead + review periods without ordering |
| `Shortage_Units_After_Order` | Simulated expected shortage with order receipt after lead time |
| `Modeled_Avoided_Shortage_Units` | Nonnegative difference between those expected shortages |
| `Modeled_Protected_Gross_Profit_USD` | Avoided simulated units × static product gross margin per unit |

**Protected gross profit is not realized incremental profit, not net profit, and is not a measured return on procurement spend.** Budget spent is not equivalent to an expense fully recognized in the period, and no cash-flow, carrying-cost or holding-cost model is included.

## 5. Power BI integration

The five original descriptive relationships remain intact. Add active, single-direction, one-to-many connections:

- Products → `Forecast_28D` by `Product_ID`.
- Stores → `Forecast_28D` by `Store_ID`.
- Products → `Replenishment_Recommendations` by `Product_ID`.
- Stores → `Replenishment_Recommendations` by `Store_ID`.
- `Scenario_Assumptions` → `Replenishment_Recommendations` by `Scenario_ID`.

`Forecast_Backtest_Metrics` remains disconnected. It contains **global** historical evaluation; a product/store slicer must not imply its WAPE score is segment-specific. Use the report's single-select scenario slicer to avoid summing three alternative recommendation sets. The 28-day aggregate forecast is **not** related to Calendar as if it contained daily rows.

## 6. Threats to validity and next research steps

- **Selection bias:** September 2018 evaluation was already consulted. Fresh unseen dates would be required for a genuine final blind test.
- **Forecast error:** ~56–62% store–product WAPE is large; aggregated accuracy is generally better. Do not imply SKU-level operational reliability.
- **Sales vs demand:** Recorded sales may be constrained by unknown historical stockouts.
- **Inventory timing:** Stock is a single assumed snapshot; no movements or outstanding orders are observed.
- **Assumptions:** Lead time, review period, service target and budget were created for demonstrations.
- **Uncertainty:** The approximate normal process and its period scaling are uncalibrated.
- **Prescription:** No actual post-October-2018 replenishment outcome exists for causal or realized-benefit validation.
- **Scope:** No historical variable pricing, holidays, promotions, lead-time variance, reorder pack constraints or network transfer optimization.

See [validation.md](validation.md) for exact reproducibility checks and [data_dictionary.md](data_dictionary.md) for every field.
