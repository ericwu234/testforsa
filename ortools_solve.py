from ortools.sat.python import cp_model
from typing import List, Optional, Tuple, Dict
import math

from evaluation import evaluate
from instance import build_instance



# ============================================================
# Constraint switch indices (list[bool] -> hard/soft)
# True  => enforce as HARD constraint
# False => enforce as SOFT penalty (if a soft formulation exists)
# ============================================================
RULES: Dict[str, int] = {
    # Originally hard in many models
    "FIXED_SCHEDULE": 0,          # fixed[e][d] must be that shift
    "DEMAND_COVERAGE": 1,         # staffing >= demand

    # Originally soft in your problem statement (per your note)
    "FORBIDDEN_TRANSITION": 2,    # transition violations (weight=1)

    # Other soft constraints you already had
    "MAX_CONSEC_WORK": 3,         # consecutive work > 5
    "REST_MONTHLY_OR_FAIRNESS": 4,# monthly rest deficit or fairness missing
    "MIN_DOUBLE_REST": 5,         # double-rest occurrences >= 2
    "MIN_WEEKEND_REST": 6,        # weekend rest >= 4
    "SINGLE_REST_BREAK": 7,       # work-rest-work count
    "CROSS_GROUP": 8,             # disallowed shift assignment for group
}


def solve_schedule_cp_sat(
    num_employees: int = 16,
    num_days: int = 30,
    time_limit_sec: float = 30.0,
    random_seed: int = 12345,
    num_workers: int = 8,
    log_progress: bool = False,
    # Rest rule mode:
    use_rest_fairness: bool = False,
    min_monthly_rest: int = 9,
    # Constraint switches:
    constraint_hard_flags: Optional[List[bool]] = None,
    # Weights for SOFT penalties (used when the corresponding flag is False)
    weights: Optional[Dict[str, float]] = None,
    demand_hard_equal=True
):
    """
    constraint_hard_flags:
      - list[bool] with length == len(RULES)
      - True  => make that rule HARD
      - False => make that rule SOFT (penalty), if supported

    Notes:
      - Some rules (e.g., "exactly one shift per day") are always hard.
      - If you set many rules to HARD simultaneously, the model may become infeasible.
    """

    # -----------------------
    # Defaults
    # -----------------------
    if constraint_hard_flags is None:
        # Default behavior: keep your "problem statement" intent:
        # - fixed + demand as HARD
        # - forbidden transition as SOFT
        # - others remain SOFT (matching your current code structure)
        constraint_hard_flags = [False] * len(RULES)
        constraint_hard_flags[RULES["FIXED_SCHEDULE"]] = True
        constraint_hard_flags[RULES["DEMAND_COVERAGE"]] = True

    if len(constraint_hard_flags) != len(RULES):
        raise ValueError(f"constraint_hard_flags length must be {len(RULES)} (got {len(constraint_hard_flags)})")

    def is_hard(rule_name: str) -> bool:
        return bool(constraint_hard_flags[RULES[rule_name]])

    # Default weights (only used when a rule is SOFT)
    default_weights = {
        "FIXED_SCHEDULE":          1000,  # 特殊：hard 實驗用，不需與 evaluate 對齊
        "DEMAND_COVERAGE":           10,  # evaluate: 1.0  (×10)
        "FORBIDDEN_TRANSITION":      10,  # evaluate: 1.0  (×10)
        "MAX_CONSEC_WORK":           10,  # evaluate: 1.0  (×10)
        "REST_MONTHLY_OR_FAIRNESS":   1,  # evaluate: 0.1  (×10)
        "MIN_DOUBLE_REST":            1,  # evaluate: 0.1  (×10)
        "MIN_WEEKEND_REST":           1,  # evaluate: 0.1  (×10)
        "SINGLE_REST_BREAK":          1,  # evaluate: 0.1  (×10)
        "CROSS_GROUP":                2,  # evaluate: 0.2  (×10)
    }
    if weights is None:
        weights = default_weights
    else:
        tmp = dict(default_weights)
        tmp.update(weights)
        weights = tmp

    # -----------------------
    # Data
    # -----------------------
    daily_demand, fixed, groups = build_instance(num_employees, num_days)

    from evaluation import allowed as allowed_local

    # -----------------------
    # Model
    # -----------------------
    model = cp_model.CpModel()
    shifts = list(range(5))  # 0..4

    # x[e,d,s] in {0,1}
    x = {}
    for e in range(num_employees):
        for d in range(num_days):
            for s in shifts:
                x[(e, d, s)] = model.NewBoolVar(f"x_e{e}_d{d}_s{s}")
            # always hard: exactly one shift per day
            model.Add(sum(x[(e, d, s)] for s in shifts) == 1)

    penalties = []

    # -----------------------
    # FIXED_SCHEDULE: hard or soft
    # -----------------------
    for e in range(num_employees):
        for d in range(num_days):
            if fixed[e][d] is None:
                continue
            fs = fixed[e][d]
            if is_hard("FIXED_SCHEDULE"):
                model.Add(x[(e, d, fs)] == 1)
            else:
                viol = model.NewBoolVar(f"fixed_viol_e{e}_d{d}")
                # viol == 1 - x[e,d,fs]
                model.Add(viol + x[(e, d, fs)] == 1)
                penalties.append((weights["FIXED_SCHEDULE"], viol))

    # -----------------------
    # DEMAND_COVERAGE: hard or soft
    # hard:
    #   - demand_hard_equal=False: staff >= demand
    #   - demand_hard_equal=True : staff == demand
    # soft:
    #   - 只懲罰人數不足 shortage = max(0, demand - staff)
    #   - 若 staff >= demand，則不加 penalty
    # -----------------------
    for d in range(num_days):
        for s in range(1, 5):  # 1..4
            staff = sum(x[(e, d, s)] for e in range(num_employees))
            demand = daily_demand[s - 1][d]

            if is_hard("DEMAND_COVERAGE"):
                if demand_hard_equal:
                    model.Add(staff == demand)
                else:
                    model.Add(staff >= demand)
            else:
                shortage = model.NewIntVar(0, demand, f"demand_shortage_d{d}_s{s}")
                model.Add(shortage >= demand - staff)
                penalties.append((weights["DEMAND_COVERAGE"], shortage))


    # -----------------------
    # FORBIDDEN_TRANSITION: hard or soft (weight=1 when soft)
    # skip rest transitions in evaluate; but for CP decision vars, we model violations for all days;
    # to match evaluate more closely, we only count when both days are working shifts (1..4).
    # -----------------------
    forbidden_pairs = [
        (3, 1), (3, 2), (3, 4),  # night -> early/noon/admin
        (2, 1), (2, 4),          # noon -> early/admin
        (1, 3), (2, 3), (4, 3),  # early/noon/admin -> night
    ]
    for e in range(num_employees):
        for d in range(num_days - 1):
            for s1, s2 in forbidden_pairs:
                if is_hard("FORBIDDEN_TRANSITION"):
                    model.Add(x[(e, d, s1)] + x[(e, d + 1, s2)] <= 1)
                else:
                    viol = model.NewBoolVar(f"trans_viol_e{e}_d{d}_{s1}to{s2}")
                    # count only if both days are work-shifts in those exact s1,s2
                    model.Add(viol >= x[(e, d, s1)] + x[(e, d + 1, s2)] - 1)
                    penalties.append((weights["FORBIDDEN_TRANSITION"], viol))

    # work[e,d] bool = 1 if not rest
    work = {}
    for e in range(num_employees):
        for d in range(num_days):
            work[(e, d)] = model.NewBoolVar(f"work_e{e}_d{d}")
            model.Add(work[(e, d)] + x[(e, d, 0)] == 1)

    # -----------------------
    # MAX_CONSEC_WORK: hard or soft
    # -----------------------
    max_consec = 5
    for e in range(num_employees):
        for start in range(0, num_days - (max_consec + 1) + 1):
            window = [work[(e, start + k)] for k in range(max_consec + 1)]
            if is_hard("MAX_CONSEC_WORK"):
                model.Add(sum(window) <= max_consec)
            else:
                # 【優化】因為 window 最多 6 天，超標時 slack 必定為 1，改用 BoolVar 大幅加速
                slack = model.NewBoolVar(f"consec_slack_e{e}_st{start}")
                model.Add(sum(window) <= max_consec + slack)
                penalties.append((weights["MAX_CONSEC_WORK"], slack))
    
    # -----------------------
    # REST_MONTHLY_OR_FAIRNESS:
    # 統一以「員工休假數 < min_monthly_rest」為準
    # 目前題意固定為：休假數 < 9
    # hard:  rest_cnt >= min_monthly_rest
    # soft:  deficit = max(0, min_monthly_rest - rest_cnt)
    # -----------------------
    rest_cnt_vars = []
    for e in range(num_employees):
        r = model.NewIntVar(0, num_days, f"rest_cnt_e{e}")
        model.Add(r == sum(x[(e, d, 0)] for d in range(num_days)))
        rest_cnt_vars.append(r)

    for e in range(num_employees):
        if is_hard("REST_MONTHLY_OR_FAIRNESS"):
            model.Add(rest_cnt_vars[e] >= min_monthly_rest)
        else:
            deficit = model.NewIntVar(0, min_monthly_rest, f"monthly_rest_deficit_e{e}")
            model.Add(deficit >= min_monthly_rest - rest_cnt_vars[e])
            penalties.append((weights["REST_MONTHLY_OR_FAIRNESS"], deficit))
    # ==========================================
    # 【優化：對稱性破除 (Symmetry Breaking)】
    # ==========================================
    from collections import defaultdict
    # 1. 找出完全沒有被預排班表 (fixed) 的員工
    no_fixed_emps = [e for e in range(num_employees) if all(fixed[e][d] is None for d in range(num_days))]
    
    # 2. 將這些員工按技能群組分類
    group_to_emps = defaultdict(list)
    for e in no_fixed_emps:
        group_to_emps[groups[e]].append(e)
        
    # 3. 對於同群組的鏡像員工，強加休假天數的字典排序，打破對稱性
    for grp, emps in group_to_emps.items():
        for i in range(len(emps) - 1):
            # 強制約定：同組中，前面的員工休假天數必須大於等於後面的員工
            model.Add(rest_cnt_vars[emps[i]] >= rest_cnt_vars[emps[i+1]])
    # -----------------------
    # MIN_DOUBLE_REST: hard or soft
    # 以 non-overlap double rest 為準（與 evaluation 的貪婪掃描一致）：
    # 對所有相鄰兩天定義 pair 變數，再加非重疊約束 dr[d]+dr[d+1]<=1，
    # Solver 最小化 deficit 時會自動最大化 pair_cnt，等同貪婪結果。
    # -----------------------
    min_double_rest = 2
    for e in range(num_employees):
        dr = []
        for d in range(num_days - 1):
            pair = model.NewBoolVar(f"dr_e{e}_d{d}")
            model.Add(pair <= x[(e, d, 0)])
            model.Add(pair <= x[(e, d + 1, 0)])
            model.Add(pair >= x[(e, d, 0)] + x[(e, d + 1, 0)] - 1)
            dr.append(pair)

        # 非重疊：相鄰兩個 pair 不可同時計入
        for d in range(len(dr) - 1):
            model.Add(dr[d] + dr[d + 1] <= 1)

        pair_cnt = model.NewIntVar(0, num_days // 2, f"paircnt_e{e}")
        model.Add(pair_cnt == sum(dr))

        if is_hard("MIN_DOUBLE_REST"):
            model.Add(pair_cnt >= min_double_rest)
        else:
            deficit = model.NewIntVar(0, min_double_rest, f"double_deficit_e{e}")
            model.Add(deficit >= min_double_rest - pair_cnt)
            penalties.append((weights["MIN_DOUBLE_REST"], deficit))

    # -----------------------
    # MIN_WEEKEND_REST: hard or soft
    # weekend: d%7 in {0,1}
    # -----------------------
    weekend_days = [d for d in range(num_days) if (d % 7 == 0 or d % 7 == 1)]
    min_weekend_rest = 4
    for e in range(num_employees):
        wrest = sum(x[(e, d, 0)] for d in weekend_days)
        if is_hard("MIN_WEEKEND_REST"):
            model.Add(wrest >= min_weekend_rest)
        else:
            deficit = model.NewIntVar(0, len(weekend_days), f"wrest_deficit_e{e}")
            model.Add(deficit >= min_weekend_rest - wrest)
            penalties.append((weights["MIN_WEEKEND_REST"], deficit))

    # -----------------------
    # SINGLE_REST_BREAK: hard or soft
    # -----------------------
    for e in range(num_employees):
        for d in range(1, num_days - 1):
            if is_hard("SINGLE_REST_BREAK"):
                # 【優化】HARD：直接禁止 (工作 + 休假 + 工作) == 3 的情況，連 brk 變數都不用宣告
                model.Add(work[(e, d - 1)] + x[(e, d, 0)] + work[(e, d + 1)] <= 2)
            else:
                # 【優化】SOFT：只保留下界約束來觸發懲罰，拔除無效的上限約束
                brk = model.NewBoolVar(f"wrw_e{e}_d{d}")
                model.Add(brk >= work[(e, d - 1)] + x[(e, d, 0)] + work[(e, d + 1)] - 2)
                penalties.append((weights["SINGLE_REST_BREAK"], brk))

    # -----------------------
    # CROSS_GROUP: hard or soft
    # hard: disallowed shifts forced to 0
    # soft: penalty for assigning disallowed shifts
    # -----------------------
    for e in range(num_employees):
        for d in range(num_days):
            fs = fixed[e][d]
            for s in shifts:
                if not allowed_local(groups[e], s):
                    if is_hard("CROSS_GROUP"):
                        if fs != None and fs == s:
                            # Option A (most common): skip completely (no hard constraint, no penalty)
                            continue
                        model.Add(x[(e, d, s)] == 0)
                        
                    else:
                        penalties.append((weights["CROSS_GROUP"], x[(e, d, s)]))

    # -----------------------
    # Objective (integer)
    # -----------------------
    if penalties:
        obj_terms = [int(round(w)) * var for (w, var) in penalties]
        model.Minimize(sum(obj_terms))
    else:
        model.Minimize(0)

    # -----------------------
    # Solve
    # -----------------------
    solver = cp_model.CpSolver()
    solver.parameters.cp_model_presolve = True
    solver.parameters.linearization_level = 2
    solver.parameters.search_branching = cp_model.PORTFOLIO_SEARCH
    solver.parameters.num_search_workers = num_workers
    solver.parameters.randomize_search = True
    solver.parameters.random_seed = random_seed
    solver.parameters.log_search_progress = log_progress
    solver.parameters.max_time_in_seconds = time_limit_sec

    status = solver.Solve(model)
    print(f"Status: {solver.StatusName(status)}")
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        print("No solution found.")
        return None

    assign = [[0] * num_days for _ in range(num_employees)]
    for e in range(num_employees):
        for d in range(num_days):
            for s in shifts:
                if solver.Value(x[(e, d, s)]) == 1:
                    assign[e][d] = s
                    break

    print(f"Objective: {solver.ObjectiveValue():.0f}")
    return assign, daily_demand, groups, fixed



if __name__ == "__main__":
    # Example: toggle some constraints
    flags = [False] * len(RULES)
    flags[RULES["FIXED_SCHEDULE"]] = True
    flags[RULES["DEMAND_COVERAGE"]] = True
    # keep transitions soft (per your statement)
    flags[RULES["FORBIDDEN_TRANSITION"]] = False
    flags[RULES["REST_MONTHLY_OR_FAIRNESS"]] = False
    flags[RULES["MAX_CONSEC_WORK"]] = False
    flags[RULES["MIN_DOUBLE_REST"]] = False
    flags[RULES["MIN_WEEKEND_REST"]] = False
    flags[RULES["SINGLE_REST_BREAK"]] = False
    flags[RULES["CROSS_GROUP"]] = False
    use_rest_fairness = False
    demand_hard_equal = False

    out = solve_schedule_cp_sat(
        time_limit_sec=30.0,
        num_workers=20,
        log_progress=True,
        use_rest_fairness=use_rest_fairness,
        demand_hard_equal=demand_hard_equal,
        constraint_hard_flags=flags,
    )
    if out is not None:
        assign, daily_demand, groups, fixed = out
        stats, fitness = evaluate(assign, daily_demand, groups=groups, fixed=fixed)
        print("Fitness =", fitness)
        print("TotalPenalty =", stats.TotalPenalty)
        print(stats)
