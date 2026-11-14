#!/usr/bin/env python3
"""Additive Maven Toys forecasting and forecast-driven inventory recommendations.

Run from a cloned Maven Toys repository (or pass --data-dir) without writing to
any source CSV. Output dates are a *historical 2018 planning scenario*.

Requires: Python >=3.10, pandas, numpy, scipy. No Power BI Desktop needed to run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd
from scipy.stats import norm

VERSION = "0.1.0"
HORIZON = 28
LOOKBACKS = (28, 56, 84)
CANDIDATES = ("last_28d", "last_56d", "last_84d", "blend_28_84")
REQUIRED = {
    "sales.csv": {"Sale_ID", "Date", "Store_ID", "Product_ID", "Units"},
    "products.csv": {"Product_ID", "Product_Name", "Product_Category", "Product_Cost", "Product_Price"},
    "stores.csv": {"Store_ID", "Store_Name", "Store_Location"},
    "inventory.csv": {"Store_ID", "Product_ID", "Stock_On_Hand"},
}


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for piece in iter(lambda: f.read(1024 * 1024), b""):
            h.update(piece)
    return h.hexdigest()


def money(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.replace(r"[$,\s]", "", regex=True), errors="raise")


def read_sources(data_dir: Path):
    files = {name: data_dir / name for name in REQUIRED}
    for name, path in files.items():
        if not path.is_file():
            raise FileNotFoundError(f"Required source missing: {path}")
    hashes_before = {name: file_sha256(path) for name, path in files.items()}
    sales = pd.read_csv(files["sales.csv"], low_memory=False)
    products = pd.read_csv(files["products.csv"], low_memory=False)
    stores = pd.read_csv(files["stores.csv"], low_memory=False)
    inventory = pd.read_csv(files["inventory.csv"], low_memory=False)
    tables = dict(sales=sales, products=products, stores=stores, inventory=inventory)
    for name, columns in REQUIRED.items():
        table = tables[name.removesuffix(".csv")]
        if not columns.issubset(table.columns):
            raise ValueError(f"{name} missing columns: {sorted(columns - set(table.columns))}")
    sales["Date"] = pd.to_datetime(sales["Date"], errors="raise").dt.normalize()
    for name in ("sales", "products", "stores", "inventory"):
        tab = tables[name]
        for key in (("Store_ID", "Product_ID") if name in ("sales", "inventory") else (name[:-1].capitalize() + "_ID",)):
            if key not in tab.columns:
                continue
            tab[key] = pd.to_numeric(tab[key], errors="raise").astype("int64")
    sales["Units"] = pd.to_numeric(sales["Units"], errors="raise")
    inventory["Stock_On_Hand"] = pd.to_numeric(inventory["Stock_On_Hand"], errors="raise")
    products["Unit_Cost"] = money(products["Product_Cost"])
    products["Unit_Price"] = money(products["Product_Price"])
    if sales.empty or products.empty or stores.empty or inventory.empty:
        raise ValueError("No source table may be empty")
    if sales[["Date", "Store_ID", "Product_ID", "Units"]].isna().any().any():
        raise ValueError("Nulls found in required sales fields")
    if (sales["Units"] <= 0).any() or (inventory["Stock_On_Hand"] < 0).any():
        raise ValueError("Unexpected nonpositive sales units or negative inventory")
    if products["Product_ID"].duplicated().any() or stores["Store_ID"].duplicated().any():
        raise ValueError("Duplicate product or store dimension keys")
    if inventory.duplicated(["Store_ID", "Product_ID"]).any():
        raise ValueError("Duplicate inventory store-product combinations")
    pids = set(products["Product_ID"])
    sids = set(stores["Store_ID"])
    for tab, label in [(sales, "sales"), (inventory, "inventory")]:
        if not set(tab["Store_ID"]).issubset(sids) or not set(tab["Product_ID"]).issubset(pids):
            raise ValueError(f"{label} includes unknown store/product keys")
    if (products["Unit_Cost"] <= 0).any() or (products["Unit_Price"] <= 0).any():
        raise ValueError("Missing or nonpositive product costs/prices")
    hashes_after = {name: file_sha256(path) for name, path in files.items()}
    if hashes_before != hashes_after:
        raise RuntimeError("Immutable source files changed during read")
    audit = {
        "source_hashes_sha256": hashes_after,
        "sales_rows": int(len(sales)),
        "sales_date_min": str(sales["Date"].min().date()),
        "sales_date_max": str(sales["Date"].max().date()),
        "distinct_sales_ids": int(sales["Sale_ID"].nunique()),
        "sales_units": int(sales["Units"].sum()),
        "products": int(len(products)), "stores": int(len(stores)),
        "inventory_rows": int(len(inventory)),
        "inventory_stock_units": int(inventory["Stock_On_Hand"].sum()),
        "missing_inventory_combinations_not_assumed_stockouts": int(len(products) * len(stores) - len(inventory)),
        "zero_stock_rows": int((inventory["Stock_On_Hand"] == 0).sum()),
        "low_stock_1_to_10_rows": int(inventory["Stock_On_Hand"].between(1, 10).sum()),
        "model_version": VERSION,
    }
    return sales, products, stores, inventory, audit


def daily_panel(sales, products, stores):
    panel = pd.MultiIndex.from_product(
        [sorted(stores["Store_ID"]), sorted(products["Product_ID"])],
        names=["Store_ID", "Product_ID"],
    )
    daily = sales.groupby(["Date", "Store_ID", "Product_ID"], sort=False)["Units"].sum().reset_index()
    return daily, panel


def aggregate_window(daily: pd.DataFrame, panel: pd.MultiIndex, start, end):
    subset = daily.loc[daily["Date"].between(start, end)]
    return subset.groupby(["Store_ID", "Product_ID"])["Units"].sum().reindex(panel, fill_value=0).to_numpy(dtype=float)


def predictions_at(daily, panel, origin):
    recent = {}
    for days in LOOKBACKS:
        total = aggregate_window(daily, panel, origin - pd.Timedelta(days=days - 1), origin)
        recent[days] = total * (HORIZON / days)
    return {
        "last_28d": recent[28],
        "last_56d": recent[56],
        "last_84d": recent[84],
        "blend_28_84": 0.65 * recent[28] + 0.35 * recent[84],
    }


def backtest(daily, panel, first_date, last_date):
    # First horizon closest to end is held out completely from model selection.
    cutoffs = []
    for k in range(1, 20):
        origin = last_date - pd.Timedelta(days=HORIZON * k)
        if origin - pd.Timedelta(days=max(LOOKBACKS) - 1) < first_date:
            break
        cutoffs.append(origin)
    if len(cutoffs) < 4:
        raise ValueError("At least 4 complete 28-day backtest windows are required")
    holdout = cutoffs[0]
    tuning = cutoffs[1: min(len(cutoffs), 7)]
    records = []
    for origin in [holdout] + tuning:
        actual = aggregate_window(daily, panel, origin + pd.Timedelta(days=1), origin + pd.Timedelta(days=HORIZON))
        guesses = predictions_at(daily, panel, origin)
        for model_name, guess in guesses.items():
            rows = pd.DataFrame({
                "Forecast_Origin": str(origin.date()), "Evaluation_Set": "holdout" if origin == holdout else "tuning",
                "Method": model_name, "Actual_Units_28D": actual,
                "Predicted_Units_28D": guess,
            })
            records.append(rows)
    return pd.concat(records, ignore_index=True)


def evaluate(records):
    output = []
    for (segment, method), sub in records.groupby(["Evaluation_Set", "Method"], sort=True):
        actual = sub["Actual_Units_28D"].to_numpy()
        predicted = sub["Predicted_Units_28D"].to_numpy()
        output.append({
            "Evaluation_Set": segment, "Method": method,
            "MAE_Units_28D": float(np.mean(np.abs(actual-predicted))),
            "WAPE": float(np.abs(actual-predicted).sum()/max(actual.sum(), 1)),
            "Bias_Units_28D": float(np.mean(predicted-actual)),
            "Total_Actual_Units": float(actual.sum()),
            "Total_Forecast_Units": float(predicted.sum()),
            "Series_Windows": int(len(sub)),
        })
    return pd.DataFrame(output)


def model_forecasts(daily, panel, origin, selected, sigma_scale):
    pred = predictions_at(daily, panel, origin)[selected]
    sigma = sigma_scale * np.sqrt(np.maximum(pred, 1))
    out = panel.to_frame(index=False)
    out["Forecast_Origin"] = origin.date().isoformat()
    out["Forecast_Start"] = (origin+pd.Timedelta(days=1)).date().isoformat()
    out["Forecast_End"] = (origin+pd.Timedelta(days=HORIZON)).date().isoformat()
    out["Forecast_Units_28D"] = np.round(pred, 4)
    out["Forecast_Std_Units_28D"] = np.round(sigma, 4)
    # Heuristic normal approximation; not a validated prediction interval.
    out["Indicative_Lower_Units_28D"] = np.round(np.maximum(0, pred-1.645*sigma), 4)
    out["Indicative_Upper_Units_28D"] = np.round(pred+1.645*sigma, 4)
    out["Selected_Method"] = selected
    out["Model_Version"] = VERSION
    return out


def expected_shortage(mu, sigma, stock):
    """Expected lost units for normal-approximate demand (scenario only)."""
    sigma = max(sigma, 0.01)
    z = (stock - mu) / sigma
    return max(0.0, float(sigma * norm.pdf(z) + (mu - stock) * norm.sf(z)))


def marginal_avoidance(mu, sigma, stock):
    return float(norm.sf((stock + 0.5 - mu) / max(sigma, 0.01)))


def optimize_budget(rows, budget):
    """Approximate per-unit greedy allocation; NOT exact integer optimization."""
    import heapq
    n = len(rows)
    ideal = rows["Ideal_Order_Units"].to_numpy(dtype=int)
    stock = rows["Stock_On_Hand"].to_numpy(dtype=float)
    cost = rows["Unit_Cost"].to_numpy(dtype=float)
    margin = rows["Unit_Margin"].to_numpy(dtype=float)
    mu = rows["Demand_Coverage_Mean"].to_numpy(dtype=float)
    sd = rows["Demand_Coverage_Std"].to_numpy(dtype=float)
    bought = np.zeros(n, dtype=int)
    if budget is None:
        return ideal.copy()
    remaining = float(budget)
    heap = []
    for i in range(n):
        if ideal[i] <= 0 or cost[i] <= 0 or margin[i] <= 0:
            continue
        benefit = marginal_avoidance(mu[i], sd[i], stock[i]) * margin[i]
        heapq.heappush(heap, (-benefit/cost[i], i))
    while heap:
        _, i = heapq.heappop(heap)
        if bought[i] >= ideal[i]:
            continue
        if cost[i] > remaining + 1e-8:
            continue
        bought[i] += 1
        remaining -= cost[i]
        if bought[i] < ideal[i]:
            benefit = marginal_avoidance(mu[i], sd[i], stock[i] + bought[i]) * margin[i]
            heapq.heappush(heap, (-benefit/cost[i], i))
    return bought


def recommend(forecasts, products, inventory, scenarios):
    merged = inventory.merge(forecasts, on=["Store_ID", "Product_ID"], how="left", validate="one_to_one")
    merged = merged.merge(products[["Product_ID", "Unit_Cost", "Unit_Price"]], on="Product_ID", validate="many_to_one")
    if merged["Forecast_Units_28D"].isna().any():
        raise ValueError("Inventory combinations missing forecasts")
    merged["Unit_Margin"] = (merged["Unit_Price"] - merged["Unit_Cost"]).clip(lower=0)
    result = []
    for sc in scenarios:
        x = merged.copy()
        horizon = int(sc["Lead_Time_Days"] + sc["Review_Period_Days"])
        service = float(sc["Target_Service_Level"])
        z = NormalDist().inv_cdf(service)
        x["Demand_Coverage_Mean"] = x["Forecast_Units_28D"] * horizon/HORIZON
        x["Demand_Coverage_Std"] = x["Forecast_Std_Units_28D"] * math.sqrt(horizon/HORIZON)
        target = np.ceil(x["Demand_Coverage_Mean"] + z*x["Demand_Coverage_Std"])
        x["Target_Stock_Units"] = target.astype(int).clip(lower=0)
        x["Ideal_Order_Units"] = (x["Target_Stock_Units"] - x["Stock_On_Hand"]).clip(lower=0).astype(int)
        x["Budgeted_Order_Units"] = optimize_budget(x, sc["Purchase_Budget_USD"])
        x["Recommended_Spend_USD"] = np.round(x["Budgeted_Order_Units"] * x["Unit_Cost"], 2)
        x["Ideal_Spend_USD"] = np.round(x["Ideal_Order_Units"] * x["Unit_Cost"], 2)
        x["Shortage_Units_No_Order"] = [expected_shortage(mu, sd, stock) for mu, sd, stock in zip(x["Demand_Coverage_Mean"], x["Demand_Coverage_Std"], x["Stock_On_Hand"])]
        x["Shortage_Units_After_Order"] = [expected_shortage(mu, sd, stock + bought) for mu, sd, stock, bought in zip(x["Demand_Coverage_Mean"], x["Demand_Coverage_Std"], x["Stock_On_Hand"], x["Budgeted_Order_Units"])]
        x["Modeled_Avoided_Shortage_Units"] = (x["Shortage_Units_No_Order"] - x["Shortage_Units_After_Order"]).clip(lower=0)
        x["Modeled_Protected_Gross_Profit_USD"] = np.round(x["Modeled_Avoided_Shortage_Units"] * x["Unit_Margin"], 2)
        x["Scenario_ID"] = sc["Scenario_ID"]
        x["Assumption_Not_Observed"] = True
        result.append(x[[
            "Scenario_ID", "Store_ID", "Product_ID", "Stock_On_Hand", "Forecast_Units_28D",
            "Demand_Coverage_Mean", "Demand_Coverage_Std", "Target_Stock_Units", "Ideal_Order_Units",
            "Budgeted_Order_Units", "Unit_Cost", "Unit_Margin", "Recommended_Spend_USD", "Ideal_Spend_USD",
            "Shortage_Units_No_Order", "Shortage_Units_After_Order", "Modeled_Avoided_Shortage_Units",
            "Modeled_Protected_Gross_Profit_USD", "Forecast_Origin", "Forecast_Start", "Forecast_End",
            "Assumption_Not_Observed",
        ]])
    return pd.concat(result, ignore_index=True)


def default_scenarios():
    # Illustrative policy inputs: never claim they are real Maven Toys settings.
    return [
        dict(Scenario_ID="S1_Lean", Lead_Time_Days=7, Review_Period_Days=7, Target_Service_Level=0.90, Purchase_Budget_USD=15000.0),
        dict(Scenario_ID="S2_Base", Lead_Time_Days=7, Review_Period_Days=7, Target_Service_Level=0.95, Purchase_Budget_USD=25000.0),
        dict(Scenario_ID="S3_Resilience", Lead_Time_Days=14, Review_Period_Days=7, Target_Service_Level=0.98, Purchase_Budget_USD=40000.0),
    ]


def run(data_dir: Path, output_dir: Path, scenario_csv: Path | None = None, snapshot_date="2018-10-01"):
    output_dir.mkdir(parents=True, exist_ok=True)
    sales, products, stores, inventory, audit = read_sources(data_dir)
    daily, panel = daily_panel(sales, products, stores)
    first_date, last_date = sales["Date"].min(), sales["Date"].max()
    assumed_stock_date = pd.Timestamp(snapshot_date)
    if assumed_stock_date != last_date + pd.Timedelta(days=1):
        raise ValueError(
            f"Inventory assumed at {assumed_stock_date.date()}, but last sale is {last_date.date()}. "
            "A different temporal alignment must be justified before replenishment modeling."
        )
    records = backtest(daily, panel, first_date, last_date)
    metrics = evaluate(records)
    tuning = metrics.loc[metrics.Evaluation_Set == "tuning"].sort_values(["WAPE", "MAE_Units_28D", "Method"])
    selected = str(tuning.iloc[0]["Method"])
    residuals = records.loc[(records.Evaluation_Set == "tuning") & (records.Method == selected)]
    # Dispersion normalization; no future outcome used in model selection or calibration.
    standardized_error = (residuals.Actual_Units_28D.to_numpy() - residuals.Predicted_Units_28D.to_numpy()) / np.sqrt(np.maximum(residuals.Predicted_Units_28D.to_numpy(), 1))
    sigma_scale = float(max(np.sqrt(np.mean(standardized_error ** 2)), 0.1))
    fc = model_forecasts(daily, panel, last_date, selected, sigma_scale)
    fc["Actual_Units_Previous_28D"] = aggregate_window(
        daily, panel, last_date - pd.Timedelta(days=HORIZON - 1), last_date
    )
    price_lookup = products.set_index("Product_ID")["Unit_Price"]
    margin_lookup = (products.set_index("Product_ID")["Unit_Price"] - products.set_index("Product_ID")["Unit_Cost"])
    fc["Forecast_Revenue_USD"] = np.round(fc["Forecast_Units_28D"] * fc["Product_ID"].map(price_lookup), 2)
    fc["Forecast_Gross_Profit_USD"] = np.round(fc["Forecast_Units_28D"] * fc["Product_ID"].map(margin_lookup), 2)
    if scenario_csv:
        scenarios = pd.read_csv(scenario_csv).to_dict("records")
    else:
        scenarios = default_scenarios()
    for sc in scenarios:
        if not (0 < float(sc["Target_Service_Level"]) < 1):
            raise ValueError("Service levels must be between 0 and 1")
        if min(int(sc["Lead_Time_Days"]), int(sc["Review_Period_Days"])) <= 0:
            raise ValueError("Lead and review periods must be positive")
        cost_budget = sc.get("Purchase_Budget_USD")
        sc["Purchase_Budget_USD"] = None if pd.isna(cost_budget) or cost_budget == "" else float(cost_budget)
        if sc["Purchase_Budget_USD"] is not None and sc["Purchase_Budget_USD"] < 0:
            raise ValueError("Budget cannot be negative")
    sr = pd.DataFrame(scenarios)
    recommendations = recommend(fc, products, inventory, scenarios)
    sr["Assumption_Status"] = "Illustrative, not observed"
    audit["selected_method"] = selected
    audit["tuning_WAPE"] = float(tuning.iloc[0].WAPE)
    test_score = metrics.loc[(metrics.Evaluation_Set == "holdout") & (metrics.Method == selected)].iloc[0]
    audit["untouched_holdout_WAPE"] = float(test_score.WAPE)
    audit["holdout_28d_origin"] = str((last_date-pd.Timedelta(days=28)).date())
    audit["forecast_origin"] = str(last_date.date())
    audit["assumed_inventory_snapshot_date"] = str(assumed_stock_date.date())
    audit["coverage_model"] = "Normal approximation with historical residual dispersion; scenario estimate, not a validated demand probability"
    audit["business_validity"] = "Retrospective October 2018 scenario; not current inventory or verified lost sales"
    audit["budget_method"] = "Unitwise decreasing-benefit greedy allocation; approximate, not exact integer optimization"
    audit["holdout_baseline_WAPE"] = float(metrics.loc[(metrics.Evaluation_Set == "holdout") & (metrics.Method == "last_28d")].iloc[0].WAPE)
    audit["holdout_better_than_naive_28d"] = bool(audit["untouched_holdout_WAPE"] < audit["holdout_baseline_WAPE"])
    audit["forecast_deployment_status"] = "EVALUATE_FOR_PILOT" if audit["holdout_better_than_naive_28d"] else "EXPERIMENTAL_ONLY_UNTIL_IMPROVED"
    # Reassert original data sources did not change.
    for name, before in audit["source_hashes_sha256"].items():
        if file_sha256(data_dir / name) != before:
            raise RuntimeError(f"Immutable source was modified: {name}")
    fc.to_csv(output_dir / "Forecast_28D.csv", index=False)
    metrics.to_csv(output_dir / "Forecast_Backtest_Metrics.csv", index=False)
    sr.to_csv(output_dir / "Scenario_Assumptions.csv", index=False)
    recommendations.to_csv(output_dir / "Replenishment_Recommendations.csv", index=False)
    with (output_dir / "Model_Audit.json").open("w") as f:
        json.dump(audit, f, indent=2)
    return audit, metrics, sr, recommendations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True, help="Directory containing original four CSV files (read only)")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"), help="Separate directory for derived files")
    parser.add_argument("--scenario-csv", type=Path, help="Optional custom scenario policy table")
    parser.add_argument("--inventory-snapshot-date", default="2018-10-01", help="Assumed snapshot date; must be first day after final sale")
    args = parser.parse_args()
    audit, metrics, scenarios, recs = run(args.data_dir, args.output_dir, args.scenario_csv, args.inventory_snapshot_date)
    print(json.dumps({
        "output_dir": str(args.output_dir),
        "audit": audit,
        "scenario_totals": recs.groupby("Scenario_ID")["Recommended_Spend_USD"].sum().round(2).to_dict(),
    }, indent=2))


if __name__ == "__main__":
    main()
