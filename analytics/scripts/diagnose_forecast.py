#!/usr/bin/env python3
"""Maven Toys forecast diagnostics v0.2 (analysis only; no production forecasts).

Copy into maven_toys_extension/scripts next to build_analytics.py.
Read-only input: original sales/products/stores/inventory CSVs.
Creates only independent diagnostic tables in --output-dir.

All new methods are pre-specified and selected ONLY on historical tuning windows.
The September 2018 holdout was inspected in the earlier v0.1 run and is no
longer a blind test for subsequent iterative model development. Treat it as
an EXPLORATORY check only. This diagnostic does not authorize deployment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

from build_analytics import (  # must be located beside this file
    HORIZON,
    LOOKBACKS,
    aggregate_window,
    daily_panel,
    file_sha256,
    predictions_at,
    read_sources,
)

VERSION = "0.2.0-diagnostics"
# Keep all methods fixed before looking at these diagnostic results.
METHODS = [
    "last_28d", "last_56d", "last_84d", "blend_28_84",
    "pooled_28", "pooled_blend_25", "pooled_blend_50",
    "pooled_blend_75", "seasonal_364d",
]


def group_abs_error(actual: np.ndarray, pred: np.ndarray, group_ids: np.ndarray, n_groups: int) -> float:
    a = np.bincount(group_ids, weights=actual, minlength=n_groups)
    p = np.bincount(group_ids, weights=pred, minlength=n_groups)
    return float(np.abs(a - p).sum())


def forecasts_for_origin(daily, panel, origin: pd.Timestamp, first_date: pd.Timestamp):
    basic = predictions_at(daily, panel, origin)
    # Pooled forecast assumes store share and product share are independently
    # predictive. It smooths noisy product-store intersections, but can hide
    # true local preferences; compare it empirically, do not assume it is better.
    p28 = basic["last_28d"].reshape((panel.levels[0].size, panel.levels[1].size))
    total28 = float(p28.sum())
    if total28 > 0:
        pooled = (p28.sum(axis=1, keepdims=True) @ p28.sum(axis=0, keepdims=True)) / total28
    else:
        pooled = np.zeros_like(p28)
    pooled = pooled.reshape(-1)
    results = dict(basic)
    results["pooled_28"] = pooled
    for n, w in [("pooled_blend_25", 0.25), ("pooled_blend_50", 0.50), ("pooled_blend_75", 0.75)]:
        results[n] = (1.0 - w) * basic["blend_28_84"] + w * pooled

    # Same 28 days shifted back by 52 weeks. For this particular 2018 project
    # the date ranges are available; the guard keeps it valid on other data.
    s = origin + pd.Timedelta(days=1 - 364)
    e = origin + pd.Timedelta(days=HORIZON - 364)
    if s >= first_date and e < origin:
        results["seasonal_364d"] = aggregate_window(daily, panel, s, e)
    return results


def run(data_dir: Path, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    sales, products, stores, inventory, audit = read_sources(data_dir)
    first_date, last_date = sales["Date"].min(), sales["Date"].max()
    daily, panel = daily_panel(sales, products, stores)

    keys = panel.to_frame(index=False)
    store_ids, store_idx = np.unique(keys["Store_ID"], return_inverse=True)
    product_ids, product_idx = np.unique(keys["Product_ID"], return_inverse=True)
    cat_lookup = products.set_index("Product_ID")["Product_Category"]
    cat_name, cat_idx = np.unique(keys["Product_ID"].map(cat_lookup).to_numpy(), return_inverse=True)
    if pd.isna(cat_lookup).any():
        raise ValueError("Missing category label")

    result_rows = []
    category_rows = []
    for k in range(1, 8):
        origin = last_date - pd.Timedelta(days=int(HORIZON * k))
        if origin - pd.Timedelta(days=int(max(LOOKBACKS) - 1)) < first_date:
            raise ValueError("Not enough history for 6 tuning windows plus holdout")
        split = "holdout_seen_before" if k == 1 else "tuning"
        actual = aggregate_window(daily, panel, origin + pd.Timedelta(days=1), origin + pd.Timedelta(days=int(HORIZON)))
        actual_total = float(actual.sum())
        if actual_total <= 0:
            raise ValueError(f"No units in validation period at {origin.date()}")
        forecasts = forecasts_for_origin(daily, panel, origin, first_date)
        for method, pred in forecasts.items():
            if len(pred) != len(actual) or not np.all(np.isfinite(pred)) or np.any(pred < 0):
                raise ValueError(f"Invalid predictions for {method}")
            residual = pred - actual
            result_rows.append({
                "Evaluation_Set": split,
                "Origin": origin.date().isoformat(),
                "Forecast_Start": (origin + pd.Timedelta(days=1)).date().isoformat(),
                "Forecast_End": (origin + pd.Timedelta(days=int(HORIZON))).date().isoformat(),
                "Method": method,
                "Actual_Units": actual_total,
                "Predicted_Units": float(pred.sum()),
                "AbsError_StoreProduct": float(np.abs(residual).sum()),
                "AbsError_Store": group_abs_error(actual, pred, store_idx, len(store_ids)),
                "AbsError_Product": group_abs_error(actual, pred, product_idx, len(product_ids)),
                "AbsError_Category": group_abs_error(actual, pred, cat_idx, len(cat_name)),
                "AbsError_Total": abs(float(residual.sum())),
                "Bias_Total_Units": float(residual.sum()),
                "WAPE_StoreProduct": float(np.abs(residual).sum() / actual_total),
            })
            for c, cat in enumerate(cat_name):
                mask = cat_idx == c
                cat_actual = float(actual[mask].sum())
                cat_pred = float(pred[mask].sum())
                category_rows.append({
                    "Evaluation_Set": split,
                    "Origin": origin.date().isoformat(),
                    "Method": method,
                    "Product_Category": str(cat),
                    "Actual_Units": cat_actual,
                    "Predicted_Units": cat_pred,
                    "AbsError_StoreProduct": float(np.abs(residual[mask]).sum()),
                    "AbsError_CategoryTotal": abs(cat_pred-cat_actual),
                })

    by_origin = pd.DataFrame(result_rows)
    categories = pd.DataFrame(category_rows)
    summaries = []
    for (split, method), sub in by_origin.groupby(["Evaluation_Set", "Method"], sort=True):
        denominator = float(sub["Actual_Units"].sum())
        row = {
            "Evaluation_Set": split,
            "Method": method,
            "Origins": int(sub["Origin"].nunique()),
            "Total_Actual_Units": denominator,
            "Total_Forecast_Units": float(sub["Predicted_Units"].sum()),
            "Bias_Total_Units": float(sub["Bias_Total_Units"].sum()),
        }
        for level in ("StoreProduct", "Store", "Product", "Category", "Total"):
            row[f"WAPE_{level}"] = float(sub[f"AbsError_{level}"].sum()/denominator)
        summaries.append(row)
    summary = pd.DataFrame(summaries)
    tuning = summary.loc[summary.Evaluation_Set == "tuning"].sort_values(["WAPE_StoreProduct", "Method"])
    tuning_ranked = tuning.copy()
    tuning_ranked.insert(0, "Tuning_Rank", np.arange(1, len(tuning_ranked)+1))
    prev_selected = "last_84d"
    new_selected = str(tuning.iloc[0].Method)
    by_origin = by_origin.sort_values(["Evaluation_Set", "Origin", "Method"]).reset_index(drop=True)
    summary = summary.sort_values(["Evaluation_Set", "WAPE_StoreProduct"]).reset_index(drop=True)
    categories = categories.sort_values(["Evaluation_Set", "Origin", "Product_Category", "Method"])

    # Preserve source hashes; no input bytes written.
    for name, initial_hash in audit["source_hashes_sha256"].items():
        if file_sha256(data_dir / name) != initial_hash:
            raise RuntimeError(f"Immutable source unexpectedly changed: {name}")

    by_origin.to_csv(output_dir / "Forecast_Diagnostic_By_Origin.csv", index=False)
    summary.to_csv(output_dir / "Forecast_Diagnostic_Summary.csv", index=False)
    categories.to_csv(output_dir / "Forecast_Diagnostic_By_Category.csv", index=False)
    tuning_ranked.to_csv(output_dir / "Forecast_Tuning_Ranking.csv", index=False)
    memo = {
        "diagnostic_version": VERSION,
        "scope": "Historical sales only; original four CSVs read-only",
        "source_sha256": audit["source_hashes_sha256"],
        "holdout_origin": str((last_date - pd.Timedelta(days=int(HORIZON))).date()),
        "holdout_status": "Previously inspected in v0.1; exploratory for NEW methods, not a fresh blind test",
        "selection_rule": "Minimize store-product WAPE over six tuning origins only",
        "previous_tuning_selected_method": prev_selected,
        "new_tuning_selected_method": new_selected,
        "new_tuning_wape": float(tuning.iloc[0].WAPE_StoreProduct),
        "deployment_allowed": False,
        "caveats": [
            "Actual demand may be censored by historical out-of-stocks, which are unobserved.",
            "Store-product errors can be much higher than aggregated errors; check granularity before dashboard claims.",
            "2018 September holdout was already consulted; improve validation before deploying a chosen new method.",
            "This diagnostic intentionally does not generate reorder outputs or overwrite the 0.1 pipeline.",
        ],
    }
    (output_dir / "Forecast_Diagnostic_Audit.json").write_text(json.dumps(memo, indent=2), encoding="utf-8")
    print(json.dumps({
        "new_tuning_selection": new_selected,
        "tuning_wape_store_product": round(float(tuning.iloc[0].WAPE_StoreProduct), 5),
        "report_files": ["Forecast_Diagnostic_Summary.csv", "Forecast_Diagnostic_By_Origin.csv",
                         "Forecast_Diagnostic_By_Category.csv", "Forecast_Tuning_Ranking.csv", "Forecast_Diagnostic_Audit.json"],
        "warning": "EXPLORATORY ONLY: holdout already viewed; do not import production forecast or reorder outputs",
    }, indent=2))
    return summary, by_origin, categories, memo


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--data-dir", required=True, type=Path)
    cli.add_argument("--output-dir", default=Path("outputs_diagnostics"), type=Path)
    args = cli.parse_args()
    run(args.data_dir, args.output_dir)


if __name__ == "__main__":
    main()
