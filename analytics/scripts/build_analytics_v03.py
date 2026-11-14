#!/usr/bin/env python3
"""Maven Toys v0.3 experimental forecast-driven replenishment.

Copy alongside build_analytics.py and diagnose_forecast.py in scripts/.
Original four input CSVs are read-only. All results are retrospective
October 2018 simulations; neither demand distributions nor service levels
are validated for operational deployment.

Models are selected on SIX earlier rolling 28d windows ONLY. September 2018
has already been consulted during development, so its score is exploratory.
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
from pathlib import Path

import numpy as np
import pandas as pd

from build_analytics import (
    HORIZON, aggregate_window, daily_panel, file_sha256, read_sources,
    default_scenarios,
)
from diagnose_forecast import METHODS, forecasts_for_origin

VERSION = "0.3.0-experimental"
DRAW_COUNT = 512
SEED = 2481


def historical_scores(daily, panel, first, last, products):
    """Tuning is k=2..7; the already-viewed September holdout is k=1."""
    rows = []
    residual_pairs = {}
    for k in range(1, 8):
        origin = last - pd.Timedelta(days=int(HORIZON * k))
        if origin - pd.Timedelta(days=83) < first:
            raise ValueError("Not enough historical data for validation windows")
        actual = aggregate_window(daily, panel, origin + pd.Timedelta(days=1), origin + pd.Timedelta(days=int(HORIZON)))
        candidates = forecasts_for_origin(daily, panel, origin, first)
        for name, forecast in candidates.items():
            if len(forecast) != len(panel) or np.any(forecast < 0) or not np.isfinite(forecast).all():
                raise ValueError(f"Invalid predictions for {name}")
            split = "holdout_seen_before" if k == 1 else "tuning"
            rows.append({
                "Evaluation_Set": split, "Method": name,
                "Origin": origin.date().isoformat(),
                "Actual_Units": float(actual.sum()),
                "Predicted_Units": float(forecast.sum()),
                "Absolute_Error_Units": float(np.abs(actual-forecast).sum()),
                "Absolute_Error_Per_Pair": float(np.abs(actual-forecast).mean()),
                "Bias_Units_Per_Pair": float((forecast-actual).mean()),
                "Series_Count": len(actual),
            })
            if split == "tuning":
                residual_pairs.setdefault(name, []).append((actual,forecast))
    windows = pd.DataFrame(rows)
    scores=[]
    for (split, method), sub in windows.groupby(["Evaluation_Set","Method"]):
        denom = max(float(sub.Actual_Units.sum()), 1)
        scores.append({
            "Evaluation_Set": split, "Method": method,
            "WAPE": float(sub.Absolute_Error_Units.sum()/denom),
            "MAE_Units_28D": float(np.average(sub.Absolute_Error_Per_Pair,weights=sub.Series_Count)),
            "Bias_Units_28D": float(np.average(sub.Bias_Units_Per_Pair,weights=sub.Series_Count)),
            "Total_Actual_Units": float(sub.Actual_Units.sum()),
            "Total_Forecast_Units": float(sub.Predicted_Units.sum()),
            "Series_Windows": int(sub.Series_Count.sum()),
        })
    metrics = pd.DataFrame(scores).sort_values(["Evaluation_Set","WAPE","Method"])
    tuning = metrics.loc[metrics.Evaluation_Set == "tuning"].sort_values(["WAPE","Method"])
    selected = str(tuning.iloc[0].Method)
    pair_list = residual_pairs[selected]
    residual = np.concatenate([(a-p)/np.sqrt(np.maximum(p,1)) for a,p in pair_list])
    sigma_scale = max(0.1, float(np.sqrt(np.mean(np.square(residual)))))
    win = windows.loc[windows.Evaluation_Set == "tuning"].pivot(index="Origin",columns="Method",values="Absolute_Error_Units")
    wins = int((win[selected] < win["last_84d"]).sum())
    return windows,metrics,selected,sigma_scale,wins


def forecast_table(daily, panel, last, first, products, selected, sigma_scale):
    guess = forecasts_for_origin(daily,panel,last,first)[selected]
    if len(guess) != len(panel):
        raise ValueError("Inconsistent panel forecast length")
    out=panel.to_frame(index=False)
    out["Forecast_Origin"] = str(last.date())
    out["Forecast_Start"] = str((last+pd.Timedelta(days=1)).date())
    out["Forecast_End"] = str((last+pd.Timedelta(days=int(HORIZON))).date())
    out["Forecast_Units_28D"] = np.round(guess,4)
    sd = sigma_scale*np.sqrt(np.maximum(guess,1))
    out["Forecast_Std_Units_28D"] = np.round(sd,4)
    out["Indicative_Lower_Units_28D"] = np.round(np.maximum(0,guess-1.645*sd),4)
    out["Indicative_Upper_Units_28D"] = np.round(guess+1.645*sd,4)
    out["Actual_Units_Previous_28D"] = aggregate_window(daily,panel,last-pd.Timedelta(days=27),last).astype(int)
    price = products.set_index("Product_ID")["Unit_Price"]
    margin = products.set_index("Product_ID")["Unit_Price"]-products.set_index("Product_ID")["Unit_Cost"]
    out["Forecast_Revenue_USD"] = np.round(guess*out["Product_ID"].map(price).to_numpy(),2)
    out["Forecast_Gross_Profit_USD"] = np.round(guess*out["Product_ID"].map(margin).to_numpy(),2)
    out["Selected_Method"] = selected
    out["Model_Version"] = VERSION
    return out


def validate_scenarios(scenarios):
    mandatory={"Scenario_ID","Lead_Time_Days","Review_Period_Days","Target_Service_Level","Purchase_Budget_USD"}
    if not scenarios or not all(mandatory.issubset(set(s)) for s in scenarios):
        raise ValueError(f"Scenario table must have {sorted(mandatory)}")
    names=[str(s["Scenario_ID"]) for s in scenarios]
    if len(set(names))!=len(names) or any(not x.strip() for x in names):
        raise ValueError("Scenario_ID values must be unique and nonempty")
    for s in scenarios:
        s["Scenario_ID"]=str(s["Scenario_ID"])
        s["Lead_Time_Days"]=int(s["Lead_Time_Days"])
        s["Review_Period_Days"]=int(s["Review_Period_Days"])
        s["Target_Service_Level"]=float(s["Target_Service_Level"])
        if pd.isna(s["Purchase_Budget_USD"]) or str(s["Purchase_Budget_USD"]).strip()=="":
            s["Purchase_Budget_USD"]=None
        else:
            s["Purchase_Budget_USD"]=float(s["Purchase_Budget_USD"])
        if s["Lead_Time_Days"]<1 or s["Review_Period_Days"]<1:
            raise ValueError("Lead time and review period must be >= 1")
        if s["Lead_Time_Days"]+s["Review_Period_Days"]>HORIZON:
            raise ValueError("Lead + review days must fit within 28-day forecast; no extrapolation permitted")
        if not 0<s["Target_Service_Level"]<1:
            raise ValueError("Service target must be between 0 and 1")
        if s["Purchase_Budget_USD"] is not None and s["Purchase_Budget_USD"]<0:
            raise ValueError("Budget must be nonnegative")
    return scenarios


def allocate_budget(ideal,unit_cost,unit_margin,sorted_gap,budget):
    """Greedy marginal expected-profit protection per purchase dollar.

    This is an approximate allocation, NOT an exact knapsack optimum.
    Each increment protects one additional unit only when simulated
    review-period unmet demand exceeds units already ordered.
    """
    n=len(ideal)
    bought=np.zeros(n,dtype=np.int64)
    if budget is None:
        return ideal.copy()
    n_draws=sorted_gap.shape[1]
    def ratio(i):
        # sorted sample gaps represent remaining need *after delivery*.
        # One unit reduces a shortage if gap > number of units bought.
        count=n_draws-int(np.searchsorted(sorted_gap[i],bought[i],side="right"))
        return (count/n_draws)*unit_margin[i]/unit_cost[i]
    heap=[]
    for i in range(n):
        if ideal[i]>0 and unit_margin[i]>0 and unit_cost[i]>0:
            score=ratio(i)
            if score>1e-12:
                heapq.heappush(heap,(-score,i))
    left=float(budget)
    while heap:
        negative,i=heapq.heappop(heap)
        if bought[i]>=ideal[i]:
            continue
        if unit_cost[i]>left+1e-8:
            continue
        bought[i]+=1
        left-=unit_cost[i]
        if bought[i]<ideal[i]:
            score=ratio(i)
            if score>1e-12:
                heapq.heappush(heap,(-score,i))
    return bought


def recommend(forecast,products,inventory,scenarios,draw_count=DRAW_COUNT,seed=SEED):
    src=inventory.merge(forecast,on=["Store_ID","Product_ID"],how="left",validate="one_to_one")
    src=src.merge(products[["Product_ID","Unit_Cost","Unit_Price"]],on="Product_ID",validate="many_to_one")
    if src["Forecast_Units_28D"].isna().any():
        raise ValueError("Missing forecast for a recorded inventory combination")
    n=len(src)
    rng=np.random.default_rng(seed)
    # Common random numbers for cross-scenario comparisons.
    z_before=rng.standard_normal((n,draw_count))
    z_after=rng.standard_normal((n,draw_count))
    mu28=src["Forecast_Units_28D"].to_numpy(float)
    sigma28=src["Forecast_Std_Units_28D"].to_numpy(float)
    stock=src["Stock_On_Hand"].to_numpy(float)
    cost=src["Unit_Cost"].to_numpy(float)
    margin=np.maximum(0,src["Unit_Price"].to_numpy(float)-cost)
    if (cost<=0).any() or (stock<0).any():
        raise ValueError("Bad costs or stock")
    rows=[]
    for sc in scenarios:
        lead=int(sc["Lead_Time_Days"]);review=int(sc["Review_Period_Days"])
        expected_lead=mu28*(lead/HORIZON)
        expected_review=mu28*(review/HORIZON)
        # Heuristic standard deviation scaling. NOT calibrated daily demand.
        sd_lead=sigma28*np.sqrt(lead/HORIZON)
        sd_review=sigma28*np.sqrt(review/HORIZON)
        demand_before=np.rint(np.maximum(0,expected_lead[:,None]+sd_lead[:,None]*z_before))
        demand_after=np.rint(np.maximum(0,expected_review[:,None]+sd_review[:,None]*z_after))
        stock_at_receipt=np.maximum(0,stock[:,None]-demand_before)
        shortage_pre=np.maximum(0,demand_before-stock[:,None]).mean(axis=1)
        # GAP is demand during the post-delivery review period minus
        # any inventory left AFTER the supplier lead time.
        gap=demand_after-stock_at_receipt
        sorted_gap=np.sort(gap,axis=1)
        ideal=np.maximum(0,np.ceil(np.quantile(gap,float(sc["Target_Service_Level"]),axis=1))).astype(np.int64)
        target_review_stock=np.ceil(np.quantile(demand_after,float(sc["Target_Service_Level"]),axis=1)).astype(np.int64)
        bought=allocate_budget(ideal,cost,margin,sorted_gap,sc["Purchase_Budget_USD"])
        shortage_review_no=np.maximum(0,gap).mean(axis=1)
        shortage_review_after=np.maximum(0,gap-bought[:,None]).mean(axis=1)
        shortage_no=shortage_pre+shortage_review_no
        shortage_order=shortage_pre+shortage_review_after
        avoided=np.maximum(0,shortage_no-shortage_order)
        if not np.all(shortage_order>=shortage_pre-1e-8):
            raise AssertionError("Order prevented shortage before delivery: impossible")
        spend=np.round(bought*cost,2)
        if sc["Purchase_Budget_USD"] is not None and spend.sum()>float(sc["Purchase_Budget_USD"])+0.02:
            raise AssertionError(f"Budget exceeded for {sc['Scenario_ID']}")
        out=src[["Store_ID","Product_ID","Stock_On_Hand","Forecast_Units_28D","Forecast_Origin","Forecast_Start","Forecast_End"]].copy()
        out["Scenario_ID"]=sc["Scenario_ID"]
        out["Mean_Lead_Time_Demand_Units"]=np.round(expected_lead,4)
        out["Mean_Review_Period_Demand_Units"]=np.round(expected_review,4)
        out["Projected_Stock_At_Receipt_Units"]=np.round(stock_at_receipt.mean(axis=1),4)
        out["Expected_Shortage_Before_Receipt_Units"]=np.round(shortage_pre,4)
        out["Expected_Shortage_Review_Without_Order_Units"]=np.round(shortage_review_no,4)
        out["Expected_Shortage_Review_After_Order_Units"]=np.round(shortage_review_after,4)
        out["Target_Stock_Units"]=target_review_stock
        out["Ideal_Order_Units"]=ideal
        out["Budgeted_Order_Units"]=bought
        out["Unit_Cost"]=cost
        out["Unit_Margin"]=margin
        out["Ideal_Spend_USD"]=np.round(ideal*cost,2)
        out["Recommended_Spend_USD"]=spend
        out["Shortage_Units_No_Order"]=np.round(shortage_no,4)
        out["Shortage_Units_After_Order"]=np.round(shortage_order,4)
        out["Modeled_Avoided_Shortage_Units"]=np.round(avoided,4)
        out["Modeled_Protected_Gross_Profit_USD"]=np.round(avoided*margin,2)
        out["Lead_Time_Days"]=lead
        out["Review_Period_Days"]=review
        out["Assumption_Not_Observed"]=True
        out["Model_Version"]=VERSION
        rows.append(out)
    return pd.concat(rows,ignore_index=True)


def run(data_dir:Path,output_dir:Path,scenario_csv:Path|None=None,snapshot_date="2018-10-01"):
    output_dir.mkdir(parents=True,exist_ok=True)
    sales,products,stores,inventory,audit=read_sources(data_dir)
    daily,panel=daily_panel(sales,products,stores)
    first,last=sales.Date.min(),sales.Date.max()
    snapshot=pd.Timestamp(snapshot_date)
    if snapshot!=last+pd.Timedelta(days=1):
        raise ValueError("Snapshot assumption must be one day after last sale for this retrospective case")
    windows,metrics,selected,sigma,wins=historical_scores(daily,panel,first,last,products)
    if selected!="pooled_blend_50":
        raise ValueError(f"The pre-reviewed candidate is pooled_blend_50, but local tuning selects {selected}. Stop and inspect data/splits")
    fc=forecast_table(daily,panel,last,first,products,selected,sigma)
    scenarios=validate_scenarios(pd.read_csv(scenario_csv).to_dict("records") if scenario_csv else default_scenarios())
    recs=recommend(fc,products,inventory,scenarios)
    scenario_table=pd.DataFrame(scenarios)
    scenario_table["Assumption_Status"]="Illustrative, not observed"
    score=lambda split,method:float(metrics.loc[(metrics.Evaluation_Set==split)&(metrics.Method==method),"WAPE"].iloc[0])
    audit.update({
        "model_version":VERSION,
        "selected_method":selected,
        "tuning_WAPE":score("tuning",selected),
        "tuning_WAPE_previous_model":score("tuning","last_84d"),
        "tuning_origin_wins_over_previous_out_of_six":wins,
        "holdout_WAPE_exploratory":score("holdout_seen_before",selected),
        "holdout_WAPE_previous_model_exploratory":score("holdout_seen_before","last_84d"),
        "holdout_WAPE_last_28d_exploratory":score("holdout_seen_before","last_28d"),
        "holdout_status":"Already examined before v0.3; NOT a fresh blind test",
        "forecast_origin":str(last.date()),
        "forecast_start":str((last+pd.Timedelta(days=1)).date()),
        "forecast_end":str((last+pd.Timedelta(days=int(HORIZON))).date()),
        "assumed_inventory_snapshot_date":str(snapshot.date()),
        "uncertainty_method":"Heuristic Normal approximation, estimated from earlier tuning residuals; NOT validated prediction intervals",
        "recommendation_method":"512-draw seeded scenario simulation; orders arrive AFTER stated lead time; per-unit greedy budget allocation",
        "recommendation_limits":"Assumes independent period demand, fixed listed costs/prices, no outstanding purchase orders, no historical stock movements, no lost-sales observations",
        "deployment_status":"EXPERIMENTAL_SCENARIO_ONLY",
        "business_validity":"Retrospective October 2018 scenario, not a present-day recommendation",
        "forecast_rows":len(fc),"recommendation_rows":len(recs),"simulation_draws_per_series":DRAW_COUNT,
        "scenario_totals_USD":recs.groupby("Scenario_ID")["Recommended_Spend_USD"].sum().round(2).to_dict(),
    })
    for name,sha in audit["source_hashes_sha256"].items():
        if file_sha256(data_dir/name)!=sha:
            raise RuntimeError("Read-only input file was changed during analysis: "+name)
    fc.to_csv(output_dir/"Forecast_28D.csv",index=False)
    windows.to_csv(output_dir/"Forecast_By_Origin.csv",index=False)
    metrics.to_csv(output_dir/"Forecast_Backtest_Metrics.csv",index=False)
    scenario_table.to_csv(output_dir/"Scenario_Assumptions.csv",index=False)
    recs.to_csv(output_dir/"Replenishment_Recommendations.csv",index=False)
    (output_dir/"Model_Audit.json").write_text(json.dumps(audit,indent=2),encoding="utf-8")
    return audit,metrics,fc,recs


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir",type=Path,required=True)
    parser.add_argument("--output-dir",type=Path,default=Path("outputs_v03"))
    parser.add_argument("--scenario-csv",type=Path)
    parser.add_argument("--inventory-snapshot-date",default="2018-10-01")
    args=parser.parse_args()
    audit,metrics,fc,recs=run(args.data_dir,args.output_dir,args.scenario_csv,args.inventory_snapshot_date)
    print(json.dumps({
        "output_dir":str(args.output_dir),"selected_method":audit["selected_method"],
        "tuning_WAPE":audit["tuning_WAPE"],
        "holdout_WAPE_exploratory":audit["holdout_WAPE_exploratory"],
        "holdout_WAPE_last_28d_exploratory":audit["holdout_WAPE_last_28d_exploratory"],
        "tuning_origin_wins_over_previous_out_of_six":audit["tuning_origin_wins_over_previous_out_of_six"],
        "forecast_rows":audit["forecast_rows"],"recommendation_rows":audit["recommendation_rows"],
        "scenario_totals_USD":audit["scenario_totals_USD"],
        "deployment_status":audit["deployment_status"],
    },indent=2))


if __name__=="__main__":
    main()
