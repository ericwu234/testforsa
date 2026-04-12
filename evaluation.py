from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class ViolationStats:
    DemandDeviation: float = 0.0
    ConsecWorkExceedDays: int = 0
    TransitionViolations: int = 0
    NightTo_EarlyNoonAdmin: int = 0
    NoonTo_EarlyAdmin: int = 0
    EarlyNoonAdminTo_Night: int = 0
    DoubleRestMissing: int = 0
    RestFairnessMissing: float = 0.0
    WeekendRestMissing: int = 0
    SingleRestBreaks: int = 0
    CrossGroupCount: int = 0
    FixedViolations: int = 0
    TotalPenalty: float = 0.0


def allowed(group: str, shift: int) -> bool:
    if shift == 0:
        return True
    if group == "Morning":
        return shift == 1
    if group == "Noon":
        return shift == 2
    if group == "Night":
        return shift == 3
    if group == "Admin":
        return shift == 4
    if group == "MorningOrAdmin":
        return shift in (1, 4)
    return True  # 空字串或未知群組不限制


def evaluate(
    assign: List[List[int]],
    daily_demand: List[List[int]],
    groups: Optional[List[str]] = None,
    fixed: Optional[List[List[Optional[int]]]] = None,
    max_consecutive_work: int = 5,
    min_double_rest_occurrences: int = 2,
    verbose: bool = True,
) -> Tuple[ViolationStats, float]:
    num_employees = len(assign)
    num_days = len(assign[0]) if num_employees > 0 else 0

    wConsecutiveLocal = 1.0
    wTransitionLocal = 1.0
    wDoubleRestLocal = 0.1
    wRestFairnessLocal = 0.1
    wWeekendRestLocal = 0.1
    wSingleRestBreakLocal = 0.1
    wCrossGroupLocal = 0.2
    wDemandLocal = 1.0

    st = ViolationStats()
    penalty = 0.0

    # (A) Demand shortage only
    # 若現有人力 >= 需求，則不加 penalty
    demand_dev = 0.0
    for d in range(num_days):
        cnt = [0, 0, 0, 0]
        for e in range(num_employees):
            a = assign[e][d]
            if 1 <= a <= 4:
                cnt[a - 1] += 1
        for sh in range(4):
            demand_dev += max(0, daily_demand[sh][d] - cnt[sh])

    st.DemandDeviation = demand_dev
    penalty += wDemandLocal * demand_dev

    # (1) Consecutive work exceed days
    total_exceed = 0
    for e in range(num_employees):
        consec = 0
        exceed = 0
        for d in range(num_days):
            if assign[e][d] != 0:
                consec += 1
            else:
                if consec > max_consecutive_work:
                    exceed += consec - max_consecutive_work
                consec = 0
        if consec > max_consecutive_work:
            exceed += consec - max_consecutive_work
        total_exceed += exceed
    st.ConsecWorkExceedDays = total_exceed
    penalty += wConsecutiveLocal * total_exceed

    # (2) Transition violations (skip rest)
    trans_all = 0
    n2x = 0
    noon2x = 0
    x2n = 0
    for e in range(num_employees):
        for d in range(num_days - 1):
            prev = assign[e][d]
            nxt = assign[e][d + 1]
            if prev == 0 or nxt == 0:
                continue
            if prev == 3 and nxt in (1, 2, 4):
                trans_all += 1; n2x += 1; continue
            if prev == 2 and nxt in (1, 4):
                trans_all += 1; noon2x += 1; continue
            if prev in (1, 2, 4) and nxt == 3:
                trans_all += 1; x2n += 1; continue
    st.TransitionViolations = trans_all
    st.NightTo_EarlyNoonAdmin = n2x
    st.NoonTo_EarlyAdmin = noon2x
    st.EarlyNoonAdminTo_Night = x2n
    penalty += wTransitionLocal * trans_all

    # (3) Double rest missing (non-overlap)
    # 例如 0 0 0 只算 1 次
    total_double_missing = 0
    for e in range(num_employees):
        double_cnt = 0
        d = 0
        while d < num_days - 1:
            if assign[e][d] == 0 and assign[e][d + 1] == 0:
                double_cnt += 1
                d += 2
            else:
                d += 1
        if double_cnt < min_double_rest_occurrences:
            total_double_missing += (min_double_rest_occurrences - double_cnt)
    st.DoubleRestMissing = total_double_missing
    penalty += wDoubleRestLocal * total_double_missing

    # (4) Monthly rest missing: 員工休假數 < 9 的缺口總和
    min_monthly_rest = 9
    rest_missing = 0.0
    for e in range(num_employees):
        rc = sum(1 for d in range(num_days) if assign[e][d] == 0)
        if rc < min_monthly_rest:
            rest_missing += (min_monthly_rest - rc)

    st.RestFairnessMissing = rest_missing
    penalty += wRestFairnessLocal * rest_missing

    # (5) Weekend rest missing
    total_weekend_missing = 0
    for e in range(num_employees):
        weekend_rest = 0
        for d in range(num_days):
            is_weekend = (d % 7 == 0) or (d % 7 == 1)
            if is_weekend and assign[e][d] == 0:
                weekend_rest += 1
        if weekend_rest < 4:
            total_weekend_missing += (4 - weekend_rest)
    st.WeekendRestMissing = total_weekend_missing
    penalty += wWeekendRestLocal * total_weekend_missing

    # (6) Work-Rest-Work count
    wrw = 0
    for e in range(num_employees):
        for d in range(1, num_days - 1):
            if assign[e][d] == 0 and assign[e][d - 1] != 0 and assign[e][d + 1] != 0:
                wrw += 1
    st.SingleRestBreaks = wrw
    penalty += wSingleRestBreakLocal * wrw

    # (7) Cross-group count
    cross = 0
    if groups is not None:
        for e in range(num_employees):
            for d in range(num_days):
                if not allowed(groups[e], assign[e][d]):
                    cross += 1
    st.CrossGroupCount = cross
    penalty += wCrossGroupLocal * cross

    # (8) Fixed violations count (not penalized in your C#)
    fixed_viol = 0
    if fixed is not None:
        for e in range(num_employees):
            for d in range(num_days):
                if fixed[e][d] is not None and assign[e][d] != fixed[e][d]:
                    fixed_viol += 1
    st.FixedViolations = fixed_viol

    st.TotalPenalty = penalty
    fitness = penalty

    if verbose:
        print("表示法：0=休,1=早,2=午,3=夜,4=行")
        for e in range(num_employees):
            print(f"員工{e+1:02d}: " + ",".join(map(str, assign[e])))

        print("\n每日現有人力分布 (早,午,夜,行):")
        for d in range(num_days):
            cnt = [0, 0, 0, 0]
            for e in range(num_employees):
                a = assign[e][d]
                if 1 <= a <= 4:
                    cnt[a - 1] += 1
            dem = [daily_demand[0][d], daily_demand[1][d], daily_demand[2][d], daily_demand[3][d]]
            print(f"Day {d+1:02d}: {cnt[0]} , {cnt[1]} , {cnt[2]} , {cnt[3]}  (需求: {dem[0]},{dem[1]},{dem[2]},{dem[3]})")

    return st, fitness

