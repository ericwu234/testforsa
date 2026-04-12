"""
實驗結果比較工具

執行方式：
    python3 show_results.py           # 顯示所有結果
    python3 show_results.py --top 5   # 只顯示最佳 5 筆
"""
import argparse
import json
import os

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
BASELINE_PATH = os.path.join(RESULTS_DIR, "baseline_ortools.json")

KEY_STATS = [
    "TotalPenalty",
    "DemandDeviation",
    "ConsecWorkExceedDays",
    "TransitionViolations",
    "DoubleRestMissing",
    "RestFairnessMissing",
    "WeekendRestMissing",
    "SingleRestBreaks",
    "CrossGroupCount",
    "FixedViolations",
]


def load_results():
    if not os.path.isdir(RESULTS_DIR):
        print("results/ 資料夾不存在，尚無實驗記錄。")
        return []

    records = []
    for fname in sorted(os.listdir(RESULTS_DIR)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(RESULTS_DIR, fname)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        data["_filename"] = fname
        records.append(data)
    return records


def print_table(records, top_n=None):
    if not records:
        print("沒有任何實驗記錄。")
        return

    # 依 TotalPenalty 排序，baseline 固定置頂
    baseline = next((r for r in records if r["_filename"] == "baseline_ortools.json"), None)
    others = [r for r in records if r["_filename"] != "baseline_ortools.json"]
    others.sort(key=lambda r: r["stats"]["TotalPenalty"])
    if top_n:
        others = others[:top_n]
    ordered = ([baseline] if baseline else []) + others

    # 欄寬
    col_ver  = 20
    col_pen  = 10
    col_dem  = 6
    col_con  = 6
    col_tra  = 6
    col_dbl  = 6
    col_rest = 6
    col_wknd = 6
    col_srb  = 6
    col_cg   = 6
    col_time = 8
    col_iter = 10
    col_note = 30

    header = (
        f"{'版本':<{col_ver}} {'Penalty':>{col_pen}} {'Demand':>{col_dem}}"
        f" {'Consec':>{col_con}} {'Trans':>{col_tra}} {'DblRst':>{col_dbl}}"
        f" {'MonRst':>{col_rest}} {'WkndRst':>{col_wknd}} {'SRB':>{col_srb}}"
        f" {'CrsGrp':>{col_cg}} {'時間(s)':>{col_time}} {'迭代數':>{col_iter}}"
        f"  {'備註'}"
    )
    sep = "-" * len(header)

    print(sep)
    print(header)
    print(sep)

    baseline_penalty = baseline["stats"]["TotalPenalty"] if baseline else None

    for r in ordered:
        s = r["stats"]
        penalty = s["TotalPenalty"]
        is_baseline = (r["_filename"] == "baseline_ortools.json")

        # 標示是否打敗基準
        if is_baseline:
            tag = "[baseline]"
        elif baseline_penalty is not None and penalty < baseline_penalty:
            tag = f"[v{r.get('version','')}] ✓ BEAT"
        else:
            tag = f"[v{r.get('version','')}]"

        label = tag[:col_ver]
        note  = r.get("notes", "")[:col_note]
        elapsed = r.get("elapsed_sec", 0)
        iters   = r.get("iterations", 0)

        row = (
            f"{label:<{col_ver}} {penalty:>{col_pen}.4f} {s['DemandDeviation']:>{col_dem}.1f}"
            f" {s['ConsecWorkExceedDays']:>{col_con}} {s['TransitionViolations']:>{col_tra}}"
            f" {s['DoubleRestMissing']:>{col_dbl}} {s['RestFairnessMissing']:>{col_rest}.1f}"
            f" {s['WeekendRestMissing']:>{col_wknd}} {s['SingleRestBreaks']:>{col_srb}}"
            f" {s['CrossGroupCount']:>{col_cg}} {elapsed:>{col_time}.1f} {iters:>{col_iter},}"
            f"  {note}"
        )
        print(row)

    print(sep)
    if baseline_penalty is not None:
        beaten = [r for r in others if r["stats"]["TotalPenalty"] < baseline_penalty]
        print(f"共 {len(others)} 筆 SA 實驗，其中 {len(beaten)} 筆打敗基準線（{baseline_penalty}）")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=None, help="只顯示最佳 N 筆（不含 baseline）")
    args = parser.parse_args()

    records = load_results()
    print_table(records, top_n=args.top)


if __name__ == "__main__":
    main()
