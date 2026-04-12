"""
實驗結果比較工具

執行方式：
    python3 show_results.py           # 顯示所有結果
    python3 show_results.py --top 5   # 只顯示最佳 5 筆（以 mean TotalPenalty 排序）
"""
import argparse
import json
import os

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
BASELINE_PATH = os.path.join(RESULTS_DIR, "baseline_ortools.json")


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


def get_penalty(record):
    """取出 TotalPenalty mean（SA）或單次值（baseline）"""
    if "aggregate" in record:
        return record["aggregate"]["TotalPenalty"]["mean"]
    return record["stats"]["TotalPenalty"]


def print_table(records, top_n=None):
    if not records:
        print("沒有任何實驗記錄。")
        return

    baseline = next((r for r in records if r["_filename"] == "baseline_ortools.json"), None)
    others = [r for r in records if r["_filename"] != "baseline_ortools.json"]
    others.sort(key=get_penalty)
    if top_n:
        others = others[:top_n]
    ordered = ([baseline] if baseline else []) + others

    baseline_penalty = get_penalty(baseline) if baseline else None

    # 欄位寬度
    W = dict(ver=22, mean=8, std=7, best=7, worst=7,
             dem=6, srb=6, cg=6, runs=5, time=8, note=28)

    def h(name, w, align=">"): return f"{name:{align}{w}}"

    header = (
        f"{'版本':<{W['ver']}} {h('Mean',W['mean'])} {h('±Std',W['std'])}"
        f" {h('Best',W['best'])} {h('Worst',W['worst'])}"
        f" {h('Demand',W['dem'])} {h('SRB',W['srb'])} {h('CrsGrp',W['cg'])}"
        f" {h('Runs',W['runs'])} {h('時間(s)',W['time'])}  {'備註'}"
    )
    sep = "─" * len(header)

    print(sep)
    print(header)
    print(sep)

    for r in ordered:
        is_baseline = (r["_filename"] == "baseline_ortools.json")

        agg = r["aggregate"]
        penalty_mean  = agg["TotalPenalty"]["mean"]
        penalty_std   = agg["TotalPenalty"]["std"]
        penalty_best  = agg["TotalPenalty"]["best"]
        penalty_worst = agg["TotalPenalty"]["worst"]
        demand  = agg["DemandDeviation"]["mean"]
        srb     = agg["SingleRestBreaks"]["mean"]
        cg      = agg["CrossGroupCount"]["mean"]
        num_runs = r.get("num_runs", len(r.get("runs", [])))
        elapsed = agg["elapsed_sec"]["mean"]

        if is_baseline:
            tag = "[baseline]"
        else:
            beat = baseline_penalty is not None and penalty_mean < baseline_penalty
            tag = f"[v{r.get('version','')}]" + (" ✓" if beat else "")

        note = r.get("notes", "")[:W["note"]]

        row = (
            f"{tag:<{W['ver']}} {penalty_mean:>{W['mean']}.4f} {penalty_std:>{W['std']}.4f}"
            f" {penalty_best:>{W['best']}.4f} {penalty_worst:>{W['worst']}.4f}"
            f" {demand:>{W['dem']}.1f} {srb:>{W['srb']}.1f} {cg:>{W['cg']}.1f}"
            f" {num_runs:>{W['runs']}} {elapsed:>{W['time']}.1f}  {note}"
        )
        print(row)

    print(sep)

    if baseline_penalty is not None and others:
        beaten = [r for r in others if get_penalty(r) < baseline_penalty]
        print(
            f"共 {len(others)} 筆 SA 實驗，其中 {len(beaten)} 筆"
            f"（mean）打敗基準線（{baseline_penalty:.4f}）"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=None, help="只顯示最佳 N 筆（不含 baseline）")
    args = parser.parse_args()

    records = load_results()
    print_table(records, top_n=args.top)


if __name__ == "__main__":
    main()
