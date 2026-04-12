"""
實驗結果記錄工具

在 sa_solve.py 跑完 5 次後呼叫 save_result()，
自動計算統計量並將結果存入 results/ 資料夾。
"""
import json
import os
import math
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")

STAT_KEYS = [
    "TotalPenalty", "DemandDeviation", "ConsecWorkExceedDays",
    "TransitionViolations", "DoubleRestMissing", "RestFairnessMissing",
    "WeekendRestMissing", "SingleRestBreaks", "CrossGroupCount", "FixedViolations",
]


def _mean(values):
    return sum(values) / len(values)


def _std(values):
    m = _mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / len(values))


def save_result(
    runs: List[Tuple],   # List of (stats, elapsed_sec, iterations)
    version: str,
    notes: str = "",
    hyperparams: Optional[Dict[str, Any]] = None,
) -> str:
    """
    將多次 SA 執行結果序列化成 JSON 存入 results/。

    Parameters
    ----------
    runs         : [(stats, elapsed_sec, iterations), ...]，建議 5 筆
    version      : 版本標籤，例如 "v1"、"v2-reheat"
    notes        : 本次實驗的文字說明（算法變動、調參動機等）
    hyperparams  : 超參數 dict

    Returns
    -------
    str : 存檔路徑
    """
    os.makedirs(RESULTS_DIR, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"sa_{version}_{timestamp}.json"
    filepath = os.path.join(RESULTS_DIR, filename)

    # 個別 run 資料
    run_records = []
    for i, (stats, elapsed, iters) in enumerate(runs, start=1):
        run_records.append({
            "run": i,
            "stats": asdict(stats),
            "elapsed_sec": round(elapsed, 2),
            "iterations": iters,
        })

    # 聚合統計
    aggregate = {}
    for key in STAT_KEYS:
        values = [r["stats"][key] for r in run_records]
        aggregate[key] = {
            "mean": round(_mean(values), 4),
            "std":  round(_std(values), 4),
            "best": round(min(values), 4),
            "worst": round(max(values), 4),
        }

    elapsed_values = [r["elapsed_sec"] for r in run_records]
    aggregate["elapsed_sec"] = {
        "mean": round(_mean(elapsed_values), 2),
        "std":  round(_std(elapsed_values), 2),
    }

    record = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "version": version,
        "notes": notes,
        "hyperparams": hyperparams or {},
        "num_runs": len(runs),
        "aggregate": aggregate,
        "runs": run_records,
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)

    penalty_agg = aggregate["TotalPenalty"]
    print(
        f"[save_result] {filename}\n"
        f"  TotalPenalty: mean={penalty_agg['mean']:.4f}  "
        f"std={penalty_agg['std']:.4f}  "
        f"best={penalty_agg['best']:.4f}  "
        f"worst={penalty_agg['worst']:.4f}  "
        f"({len(runs)} runs)"
    )
    return filepath
