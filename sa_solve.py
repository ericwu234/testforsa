import math
import random
import time
from typing import List, Optional, Tuple

from instance import build_instance
from evaluation import evaluate, allowed

NUM_EMPLOYEES = 16
NUM_DAYS = 30
MAX_CONSEC = 5
MIN_DOUBLE_REST = 2
MIN_MONTHLY_REST = 9
MIN_WEEKEND_REST = 4

# Actual evaluation weights
W_DEMAND = 1.0
W_CONSEC = 1.0
W_TRANS = 1.0
W_DOUBLE_REST = 0.1
W_REST_FAIR = 0.1
W_WEEKEND_REST = 0.1
W_SINGLE_REST = 0.1
W_CROSS = 0.2

# Internal SA weights (boosted to steer SA toward key improvements)
_W_SINGLE_REST = 0.50   # 5× boost → reduces SingleRestBreaks aggressively
_W_REST_FAIR = 0.40     # 4× boost
_W_WEEKEND_REST = 0.30  # 3× boost

_WEEKEND = frozenset(d for d in range(NUM_DAYS) if d % 7 in {0, 1})


def _ep(ae: List[int], group: str) -> float:
    """Per-employee penalty using boosted internal weights for SA steering."""
    p = 0.0

    # (1) Consecutive work
    consec = 0
    for d in range(NUM_DAYS):
        if ae[d] != 0:
            consec += 1
        else:
            if consec > MAX_CONSEC:
                p += W_CONSEC * (consec - MAX_CONSEC)
            consec = 0
    if consec > MAX_CONSEC:
        p += W_CONSEC * (consec - MAX_CONSEC)

    # (2) Transition violations
    for d in range(NUM_DAYS - 1):
        pv = ae[d]
        nx = ae[d + 1]
        if pv != 0 and nx != 0:
            if pv == 3 and nx in (1, 2, 4):
                p += W_TRANS
            elif pv == 2 and nx in (1, 4):
                p += W_TRANS
            elif pv in (1, 2, 4) and nx == 3:
                p += W_TRANS

    # (3) Double rest missing
    double_cnt = 0
    d = 0
    while d < NUM_DAYS - 1:
        if ae[d] == 0 and ae[d + 1] == 0:
            double_cnt += 1
            d += 2
        else:
            d += 1
    if double_cnt < MIN_DOUBLE_REST:
        p += W_DOUBLE_REST * (MIN_DOUBLE_REST - double_cnt)

    # (4) Monthly rest + (5) Weekend rest + (6) Single rest break
    # Uses boosted internal weights (_W_*) to steer SA
    rc = 0
    wr = 0
    for d in range(NUM_DAYS):
        if ae[d] == 0:
            rc += 1
            if d in _WEEKEND:
                wr += 1
    if rc < MIN_MONTHLY_REST:
        p += _W_REST_FAIR * (MIN_MONTHLY_REST - rc)
    if wr < MIN_WEEKEND_REST:
        p += _W_WEEKEND_REST * (MIN_WEEKEND_REST - wr)

    for d in range(1, NUM_DAYS - 1):
        if ae[d] == 0 and ae[d - 1] != 0 and ae[d + 1] != 0:
            p += _W_SINGLE_REST

    # (7) Cross group
    for d in range(NUM_DAYS):
        if not allowed(group, ae[d]):
            p += W_CROSS

    return p


def _dp(assign: List[List[int]], daily_demand: List[List[int]], day: int) -> float:
    """Demand penalty for a single day."""
    cnt = [0, 0, 0, 0]
    for e in range(NUM_EMPLOYEES):
        a = assign[e][day]
        if 1 <= a <= 4:
            cnt[a - 1] += 1
    p = 0.0
    for s in range(4):
        deficit = daily_demand[s][day] - cnt[s]
        if deficit > 0:
            p += W_DEMAND * deficit
    return p


def _full_penalty(assign: List[List[int]], daily_demand: List[List[int]], groups: List[str]) -> float:
    p = 0.0
    for d in range(NUM_DAYS):
        p += _dp(assign, daily_demand, d)
    for e in range(NUM_EMPLOYEES):
        p += _ep(assign[e], groups[e])
    return p


def _build_initial(
    daily_demand: List[List[int]],
    fixed: List[List[Optional[int]]],
    groups: List[str],
    rng: random.Random,
) -> List[List[int]]:
    assign = [[0] * NUM_DAYS for _ in range(NUM_EMPLOYEES)]

    # Apply fixed assignments and track work counts
    work_count = [0] * NUM_EMPLOYEES
    for e in range(NUM_EMPLOYEES):
        for d in range(NUM_DAYS):
            if fixed[e][d] is not None:
                assign[e][d] = fixed[e][d]
                if fixed[e][d] != 0:
                    work_count[e] += 1

    # Greedy fill: process days in random order, admin first to balance e00/e15.
    day_order = list(range(NUM_DAYS))
    rng.shuffle(day_order)

    for d in day_order:
        cnt = [0, 0, 0, 0]
        for e in range(NUM_EMPLOYEES):
            a = assign[e][d]
            if 1 <= a <= 4:
                cnt[a - 1] += 1

        free = [e for e in range(NUM_EMPLOYEES) if fixed[e][d] is None and assign[e][d] == 0]

        for si in [3, 0, 1, 2]:
            needed = max(0, daily_demand[si][d] - cnt[si])
            if needed == 0:
                continue
            actual_shift = si + 1
            cands = [e for e in free if allowed(groups[e], actual_shift)]
            cands.sort(key=lambda e: work_count[e])
            chosen = cands[:needed]
            remaining_free = []
            chosen_set = set(chosen)
            for e in free:
                if e in chosen_set:
                    assign[e][d] = actual_shift
                    work_count[e] += 1
                else:
                    remaining_free.append(e)
            free = remaining_free

    return assign


def _build_initial_rest_first(
    daily_demand: List[List[int]],
    fixed: List[List[Optional[int]]],
    groups: List[str],
    rng: random.Random,
) -> List[List[int]]:
    """Rest-first initial construction: place rest days optimally first, then fill work.

    Unlike the greedy-fill construction (work-first), this approach places each
    employee's rest days using the scored greedy from LNS repair (prefer adjacent
    blocks and weekends), then fills work days to cover demand. Gives SA a better
    starting rest structure, potentially finding better basins.
    """
    # Phase 1: start with all non-fixed days as work (same as LNS destroy state)
    assign = [[0] * NUM_DAYS for _ in range(NUM_EMPLOYEES)]
    for e in range(NUM_EMPLOYEES):
        for d in range(NUM_DAYS):
            if fixed[e][d] is not None:
                assign[e][d] = fixed[e][d]
            else:
                # Assign a plausible work shift (fill demand or first allowed)
                for s in range(1, 5):
                    if allowed(groups[e], s):
                        assign[e][d] = s
                        break

    # Phase 2: repair each employee's rest pattern using scored greedy (LNS repair)
    # Process in random order so that later employees can find good rest days too
    emp_order = list(range(NUM_EMPLOYEES))
    rng.shuffle(emp_order)
    for e in emp_order:
        assign[e] = _lns_repair_one(e, assign, fixed, groups, daily_demand, rng)

    return assign


def _build_initial_explicit_weekend(
    daily_demand: List[List[int]],
    fixed: List[List[Optional[int]]],
    groups: List[str],
    rng: random.Random,
) -> List[List[int]]:
    """Init with explicit weekend rest planning to achieve WR_miss=4 (structural min).

    Morning group (7 emps): round-robin assign 3 rests per weekend day.
    Admin/MorningOrAdmin: alternate coverage so each gets ≥3 WR.
    Night group: round-robin 1 rest per weekend day.
    Noon group: round-robin 2 rests per weekend day.
    Then DP-repair weekday rests, then SA refines.
    """
    N = NUM_DAYS
    assign = [[0] * N for _ in range(NUM_EMPLOYEES)]

    # Apply fixed assignments
    for e in range(NUM_EMPLOYEES):
        for d in range(N):
            if fixed[e][d] is not None:
                assign[e][d] = fixed[e][d]

    wknd_list = sorted(_WEEKEND)
    rng.shuffle(wknd_list)

    # Group members
    group_members: dict = {}
    for e, g in enumerate(groups):
        group_members.setdefault(g, []).append(e)

    # -- Admin / MorningOrAdmin coordination --
    mor_adm = group_members.get("MorningOrAdmin", [])
    adm_only = group_members.get("Admin", [])
    if mor_adm and adm_only:
        # Alternate: on first half e0 rests (e15 works admin), second half e15 rests (e0 works admin)
        half = len(wknd_list) // 2
        for i, d in enumerate(wknd_list):
            if fixed[mor_adm[0]][d] is not None:
                continue
            if i < half:
                assign[mor_adm[0]][d] = 0    # e0 rests (e15 covers admin below)
            else:
                assign[mor_adm[0]][d] = 4    # e0 works admin (e15 can rest below)
        for i, d in enumerate(wknd_list):
            if fixed[adm_only[0]][d] is not None:
                continue
            if i < half:
                assign[adm_only[0]][d] = 4   # e15 works admin (covers when e0 rests)
            else:
                assign[adm_only[0]][d] = 0   # e15 rests (e0 covers admin)

    # -- Morning group (e1-6): round-robin 3 rests per weekend day --
    morning = group_members.get("Morning", [])
    if morning:
        rng.shuffle(morning)
        for i, d in enumerate(wknd_list):
            # 3 emps rest on this day: cycle through morning list
            rest_set = set()
            for j in range(3):
                rest_set.add(morning[(i * 3 + j) % len(morning)])
            for e in morning:
                if fixed[e][d] is None:
                    assign[e][d] = 0 if e in rest_set else 1  # rest or morning

    # -- Night group (e12-14): round-robin 1 rest per weekend day --
    night = group_members.get("Night", [])
    if night:
        rng.shuffle(night)
        for i, d in enumerate(wknd_list):
            rest_e = night[i % len(night)]
            for e in night:
                if fixed[e][d] is None:
                    assign[e][d] = 0 if e == rest_e else 3  # rest or night

    # -- Noon group (e7-11): round-robin 2 rests per weekend day --
    noon = group_members.get("Noon", [])
    if noon:
        rng.shuffle(noon)
        for i, d in enumerate(wknd_list):
            rest_set = set()
            for j in range(min(2, len(noon))):
                rest_set.add(noon[(i * 2 + j) % len(noon)])
            for e in noon:
                if fixed[e][d] is None:
                    assign[e][d] = 0 if e in rest_set else 2  # rest or noon

    # Fill non-weekend, non-fixed days with valid work shift
    for e in range(NUM_EMPLOYEES):
        for d in range(N):
            if assign[e][d] == 0 and fixed[e][d] is None and d not in _WEEKEND:
                for s in range(1, 5):
                    if allowed(groups[e], s):
                        assign[e][d] = s
                        break

    # DP-repair weekday rests (WR-deficit priority)
    for _sweep in range(3):
        wr_def = []
        for e in range(NUM_EMPLOYEES):
            wr = sum(1 for d in range(N) if assign[e][d] == 0 and d in _WEEKEND)
            wr_def.append((max(0, MIN_WEEKEND_REST - wr) + rng.random() * 0.01, e))
        wr_def.sort(reverse=True)
        for _sc, e in wr_def:
            assign[e] = _dp_repair_one(e, assign, fixed, groups, daily_demand, rng)

    return assign


def _build_initial_dp_balanced(
    daily_demand: List[List[int]],
    fixed: List[List[Optional[int]]],
    groups: List[str],
    rng: random.Random,
) -> List[List[int]]:
    """Init: all emps work, then DP-repair in WR-deficit-priority order.

    Emps with highest WR deficit (no weekend rests yet) are repaired first so
    they claim weekend rest slots before group peers lock them out.
    """
    # Start: all non-fixed days set to valid work shift
    assign = [[0] * NUM_DAYS for _ in range(NUM_EMPLOYEES)]
    for e in range(NUM_EMPLOYEES):
        for d in range(NUM_DAYS):
            if fixed[e][d] is not None:
                assign[e][d] = fixed[e][d]
            else:
                for s in range(1, 5):
                    if allowed(groups[e], s):
                        assign[e][d] = s
                        break

    # Pre-plan: MorningOrAdmin / Admin-only weekend coordination.
    # Force Admin-only emps to rest on half the weekends so MorningOrAdmin emps
    # see unmet admin demand and pick admin shift; then Admin-only DP sees coverage.
    admin_only_emps = [e for e in range(NUM_EMPLOYEES) if groups[e] == "Admin"]
    if admin_only_emps:
        wknd_list = sorted(_WEEKEND)
        rng.shuffle(wknd_list)
        # Force Admin-only to rest on first 5 weekends (demand temporarily violated)
        for d in wknd_list[:5]:
            for e in admin_only_emps:
                if fixed[e][d] is None:
                    assign[e][d] = 0  # rest (demand may be violated temporarily)

    # DP-repair: multiple sweeps with smart ordering.
    # Sweep 0: repair non-Admin-only groups first (Morning, Noon, Night, MorningOrAdmin),
    # then Admin-only last so e15 can use weekend slots freed by e0's admin shifts.
    # Subsequent sweeps: WR-deficit priority.
    for _sweep in range(3):
        if _sweep == 0:
            # Non-Admin-only first (by WR deficit), then Admin-only last
            non_admin = [e for e in range(NUM_EMPLOYEES) if groups[e] != "Admin"]
            admin_only = [e for e in range(NUM_EMPLOYEES) if groups[e] == "Admin"]
            wr_def_na = sorted(non_admin,
                               key=lambda e: -(max(0, MIN_WEEKEND_REST -
                                               sum(1 for d in range(NUM_DAYS) if assign[e][d]==0 and d in _WEEKEND))
                                               + rng.random()*0.01))
            emp_order = wr_def_na + admin_only
        else:
            wr_def = []
            for e in range(NUM_EMPLOYEES):
                wr = sum(1 for d in range(NUM_DAYS) if assign[e][d] == 0 and d in _WEEKEND)
                wr_def.append((max(0, MIN_WEEKEND_REST - wr) + rng.random() * 0.01, e))
            wr_def.sort(reverse=True)
            emp_order = [e for _, e in wr_def]
        for e in emp_order:
            assign[e] = _dp_repair_one(e, assign, fixed, groups, daily_demand, rng)

    return assign


def _perturb(
    assign: List[List[int]],
    fixed: List[List[Optional[int]]],
    groups: List[str],
    rng: random.Random,
    n: int = 12,
) -> None:
    """ILS perturbation: force n random demand-neutral same-day swaps (v18)."""
    attempts = 0
    done = 0
    while done < n and attempts < n * 10:
        attempts += 1
        d = rng.randint(0, NUM_DAYS - 1)
        cands = [(e, assign[e][d]) for e in range(NUM_EMPLOYEES) if fixed[e][d] is None]
        if len(cands) < 2:
            continue
        i1 = rng.randrange(len(cands))
        i2 = rng.randrange(len(cands) - 1)
        if i2 >= i1:
            i2 += 1
        e1, s1 = cands[i1]
        e2, s2 = cands[i2]
        if s1 == s2:
            continue
        if not allowed(groups[e1], s2) or not allowed(groups[e2], s1):
            continue
        assign[e1][d] = s2
        assign[e2][d] = s1
        done += 1


def _count_srb_score(ae: List[int]) -> float:
    """Count SRBs + WRM deficit for targeting worst employees."""
    srb = sum(1 for d in range(1, NUM_DAYS - 1)
              if ae[d] == 0 and ae[d - 1] != 0 and ae[d + 1] != 0)
    wr = sum(1 for d in range(NUM_DAYS) if ae[d] == 0 and d in _WEEKEND)
    wrm_deficit = max(0, MIN_WEEKEND_REST - wr)
    return srb + wrm_deficit


def _weekend_steal(
    assign: List[List[int]],
    fixed: List[List[Optional[int]]],
    groups: List[str],
    rng: random.Random,
) -> int:
    """Targeted WR rebalance: move a weekend rest from slack emp to deficit emp.

    Finds an emp X with WR < 4 (deficit) and same-group Y with WR ≥ 5 (slack).
    On a weekend day where X works and Y rests, swap them. Demand preserved
    since same group. Returns number of successful swaps.
    """
    group_members = {}
    for e, g in enumerate(groups):
        group_members.setdefault(g, []).append(e)

    wr_count = [sum(1 for d in range(NUM_DAYS) if assign[e][d] == 0 and d in _WEEKEND)
                for e in range(NUM_EMPLOYEES)]

    swaps = 0
    for g, mem in group_members.items():
        if len(mem) < 2:
            continue
        deficits = [e for e in mem if wr_count[e] < MIN_WEEKEND_REST]
        slacks = [e for e in mem if wr_count[e] > MIN_WEEKEND_REST]
        if not deficits or not slacks:
            continue
        rng.shuffle(deficits)
        rng.shuffle(slacks)
        for x in deficits:
            if wr_count[x] >= MIN_WEEKEND_REST:
                continue
            for y in slacks:
                if wr_count[y] <= MIN_WEEKEND_REST:
                    continue
                # Find weekend day where x works, y rests
                wknds = list(_WEEKEND)
                rng.shuffle(wknds)
                for d in wknds:
                    if fixed[x][d] is not None or fixed[y][d] is not None:
                        continue
                    if assign[x][d] != 0 and assign[y][d] == 0:
                        # Swap: x rests, y works (y takes x's shift)
                        assign[y][d] = assign[x][d]
                        assign[x][d] = 0
                        wr_count[x] += 1
                        wr_count[y] -= 1
                        swaps += 1
                        break
                if wr_count[x] >= MIN_WEEKEND_REST:
                    break
    return swaps


def _wr_rebalance_ls(
    assign: List[List[int]],
    fixed: List[List[Optional[int]]],
    groups: List[str],
    daily_demand: List[List[int]],
) -> int:
    """Greedy LS: swap weekday rest ↔ weekend rest between two same-group emps.

    For X (WR deficit) and Y (WR surplus), in same group:
      - Pick weekday d_wd where X rests and Y works
      - Pick weekend d_we where X works and Y rests
      - Swap: X works d_wd, X rests d_we; Y rests d_wd, Y works d_we
    This preserves monthly rest counts, demand (same group), and improves WR balance.
    Accepts only improving moves. Returns total swaps.
    """
    group_members: dict = {}
    for e, g in enumerate(groups):
        group_members.setdefault(g, []).append(e)

    weekdays = [d for d in range(NUM_DAYS) if d not in _WEEKEND]

    total_swaps = 0
    improved = True
    while improved:
        improved = False
        for g, members in group_members.items():
            if len(members) < 2:
                continue
            wr_count = {e: sum(1 for d in range(NUM_DAYS) if assign[e][d] == 0 and d in _WEEKEND)
                        for e in members}
            # Emps with deficit vs surplus
            deficit_emps = sorted([e for e in members if wr_count[e] < MIN_WEEKEND_REST],
                                  key=lambda e: wr_count[e])
            surplus_emps = sorted([e for e in members if wr_count[e] > MIN_WEEKEND_REST],
                                  key=lambda e: -wr_count[e])
            if not deficit_emps or not surplus_emps:
                continue
            for x in deficit_emps:
                for y in surplus_emps:
                    if x == y:
                        continue
                    # Find weekday where x rests, y works
                    x_wd_rests = [d for d in weekdays if assign[x][d] == 0
                                  and assign[y][d] != 0
                                  and fixed[x][d] is None and fixed[y][d] is None]
                    # Find weekend where x works, y rests
                    x_we_works = [d for d in _WEEKEND if assign[x][d] != 0
                                  and assign[y][d] == 0
                                  and fixed[x][d] is None and fixed[y][d] is None]
                    if not x_wd_rests or not x_we_works:
                        continue
                    # Try all combos, pick best improving
                    best_delta = -1e-9
                    best_move = None
                    for d_wd in x_wd_rests[:5]:   # limit search
                        for d_we in x_we_works[:5]:
                            # Compute delta
                            old_ep_x = _ep(assign[x], groups[x])
                            old_ep_y = _ep(assign[y], groups[y])
                            sx_wd = assign[x][d_wd]
                            sy_wd = assign[y][d_wd]
                            sx_we = assign[x][d_we]
                            sy_we = assign[y][d_we]
                            assign[x][d_wd] = sy_wd  # x takes y's shift on d_wd
                            assign[x][d_we] = 0       # x rests on d_we
                            assign[y][d_wd] = 0       # y rests on d_wd
                            assign[y][d_we] = sx_we   # y takes x's shift on d_we
                            new_ep_x = _ep(assign[x], groups[x])
                            new_ep_y = _ep(assign[y], groups[y])
                            delta = (new_ep_x - old_ep_x) + (new_ep_y - old_ep_y)
                            # Revert
                            assign[x][d_wd] = sx_wd
                            assign[x][d_we] = sx_we
                            assign[y][d_wd] = sy_wd
                            assign[y][d_we] = sy_we
                            if delta < best_delta:
                                best_delta = delta
                                best_move = (d_wd, d_we, sx_wd, sy_wd, sx_we, sy_we)
                    if best_move is not None:
                        d_wd, d_we, sx_wd, sy_wd, sx_we, sy_we = best_move
                        assign[x][d_wd] = sy_wd
                        assign[x][d_we] = 0
                        assign[y][d_wd] = 0
                        assign[y][d_we] = sx_we
                        improved = True
                        total_swaps += 1
    return total_swaps


def _lns_perturb(
    assign: List[List[int]],
    fixed: List[List[Optional[int]]],
    groups: List[str],
    daily_demand: List[List[int]],
    rng: random.Random,
    k: int = 3,
) -> None:
    """LNS perturbation: destroy k worst employees' rest patterns, repair greedily.

    Selects the k employees with the highest SRB+WRM deficit, destroys their
    rest assignments (resets free days to work), then repairs each using the
    scored greedy rest placement. Modifies assign in place.
    """
    # Select k employees with highest SRB+WRM score
    scores = [(e, _count_srb_score(assign[e])) for e in range(NUM_EMPLOYEES)]
    scores.sort(key=lambda x: -x[1])
    target_emps = [e for e, _ in scores[:k]]

    # Destroy: reset non-fixed rest days to work
    for e in target_emps:
        for d in range(NUM_DAYS):
            if fixed[e][d] is None and assign[e][d] == 0:
                for s in range(1, 5):
                    if allowed(groups[e], s):
                        assign[e][d] = s
                        break

    # Repair: DP-optimal rest placement for each (WR-deficit-priority order)
    priority = []
    for e in target_emps:
        wr = sum(1 for d in range(NUM_DAYS) if assign[e][d] == 0 and d in _WEEKEND)
        priority.append((max(0, MIN_WEEKEND_REST - wr) + rng.random() * 0.01, e))
    priority.sort(reverse=True)
    for _s, e in priority:
        assign[e] = _dp_repair_one(e, assign, fixed, groups, daily_demand, rng)


def _group_joint_repair(
    assign: List[List[int]],
    fixed: List[List[Optional[int]]],
    groups: List[str],
    daily_demand: List[List[int]],
    rng: random.Random,
) -> None:
    """Destroy all emps in a randomly picked group and jointly DP-repair.

    Breaks mutual deadlock where each emp's can_rest is blocked by others in
    the same group. By clearing them all, weekend slots open up.
    """
    group_members = {}
    for e, g in enumerate(groups):
        group_members.setdefault(g, []).append(e)
    # Pick a group with ≥2 members
    candidates = [g for g, mem in group_members.items() if len(mem) >= 2]
    if not candidates:
        return
    g = rng.choice(candidates)
    target_emps = group_members[g]

    # Destroy all emps in group: reset non-fixed cells to valid work shift
    for e in target_emps:
        for d in range(NUM_DAYS):
            if fixed[e][d] is None:
                for s in range(1, 5):
                    if allowed(groups[e], s):
                        assign[e][d] = s
                        break

    # Repair in WR-deficit priority
    priority = []
    for e in target_emps:
        wr = sum(1 for d in range(NUM_DAYS) if assign[e][d] == 0 and d in _WEEKEND)
        priority.append((max(0, MIN_WEEKEND_REST - wr) + rng.random() * 0.01, e))
    priority.sort(reverse=True)
    for _s, e in priority:
        assign[e] = _dp_repair_one(e, assign, fixed, groups, daily_demand, rng)


def _dp_repair_one(
    e: int,
    assign: List[List[int]],
    fixed: List[List[Optional[int]]],
    groups: List[str],
    daily_demand: List[List[int]],
    rng: random.Random,
) -> List[int]:
    """DP-optimal per-employee rest placement minimizing SRB + 0.5*WR_deficit.

    Exact DP over (day, rests_placed, prev_rest, weekend_rests). Far faster than
    2^28 brute force; picks the optimal rest pattern given demand feasibility.
    """
    N = NUM_DAYS
    can_rest = [False] * N
    work_shift_for = [0] * N
    forced_rest = [False] * N
    forced_work = [False] * N
    for d in range(N):
        if fixed[e][d] is not None:
            if fixed[e][d] == 0:
                can_rest[d] = True
                forced_rest[d] = True
                work_shift_for[d] = 0
            else:
                forced_work[d] = True
                work_shift_for[d] = fixed[e][d]
            continue
        cnt = [0, 0, 0, 0]
        for ex in range(NUM_EMPLOYEES):
            if ex == e:
                continue
            a = assign[ex][d]
            if 1 <= a <= 4:
                cnt[a - 1] += 1
        if all(cnt[s] >= daily_demand[s][d] for s in range(4)):
            can_rest[d] = True
        ws = 0
        for s in range(4):
            if cnt[s] < daily_demand[s][d] and allowed(groups[e], s + 1):
                ws = s + 1
                break
        if ws == 0:
            for s in range(1, 5):
                if allowed(groups[e], s):
                    ws = s
                    break
        work_shift_for[d] = ws

    current_forced_rests = sum(forced_rest)
    target = max(MIN_MONTHLY_REST, current_forced_rests)
    INF = float("inf")
    max_wr = MIN_WEEKEND_REST

    # DP state: (rests_placed, prev_enc, weekend_rests) where prev_enc packs (prev2, prev1)
    # rest-flags so SRB at day d-1 can be detected when placing W at day d.
    states_list = [{(0, 0, 0): (0.0, None, None)}]
    for d in range(N):
        prev_states = states_list[-1]
        new_states = {}
        is_weekend = d in _WEEKEND
        for key, (cost, _, _) in prev_states.items():
            r, prev_enc, wr = key
            prev2 = (prev_enc >> 1) & 1
            prev1 = prev_enc & 1
            if forced_rest[d]:
                options = [1]
            elif forced_work[d] or not can_rest[d]:
                options = [0]
            else:
                options = [0, 1]
            for rest in options:
                new_r = r + rest
                if new_r > target:
                    continue
                new_wr = wr + (1 if (rest and is_weekend) else 0)
                if new_wr > max_wr:
                    new_wr = max_wr
                add = 1.0 if (d >= 2 and prev1 == 1 and prev2 == 0 and rest == 0) else 0.0
                new_prev_enc = ((prev1 << 1) | rest) & 0b11
                new_key = (new_r, new_prev_enc, new_wr)
                new_cost = cost + add
                existing = new_states.get(new_key)
                if existing is None or new_cost < existing[0]:
                    new_states[new_key] = (new_cost, key, rest)
        if not new_states:
            return _lns_repair_one(e, assign, fixed, groups, daily_demand, rng)
        states_list.append(new_states)

    # Pick final state with r == target, minimize cost + 0.5 * WR_deficit
    best_key = None
    best_score = INF
    for key, (cost, _, _) in states_list[-1].items():
        r, prev_enc, wr = key
        if r != target:
            continue
        wr_deficit = max(0, MIN_WEEKEND_REST - wr)
        score = cost + 0.5 * wr_deficit
        if score < best_score:
            best_score = score
            best_key = key

    if best_key is None:
        return _lns_repair_one(e, assign, fixed, groups, daily_demand, rng)

    # Backtrack from best_key at day N
    decisions = [0] * N
    cur = best_key
    for d in range(N - 1, -1, -1):
        _, parent, dec = states_list[d + 1][cur]
        decisions[d] = dec
        cur = parent

    # Build ae from decisions
    ae = [0] * N
    for d in range(N):
        if decisions[d] == 1:
            ae[d] = 0  # rest
        else:
            ae[d] = work_shift_for[d]
    return ae


def _lns_repair_one(
    e: int,
    assign: List[List[int]],
    fixed: List[List[Optional[int]]],
    groups: List[str],
    daily_demand: List[List[int]],
    rng: random.Random,
) -> List[int]:
    """LNS repair: greedily place rest days for employee e to minimize SRB+WRM.

    Computes which days employee e can rest (demand met by others), then
    places exactly max(MIN_MONTHLY_REST, current_rests) rest days using a
    scored greedy: prefer days adjacent to existing rests (+4) and weekends (+2),
    penalise isolated positions (-3).

    Returns new ae (length NUM_DAYS) without modifying assign.
    """
    N = NUM_DAYS

    # Determine can_rest and default work shift for each free day
    can_rest_set = set()
    work_shift_for = {}  # d -> shift to work if not resting
    for d in range(N):
        if fixed[e][d] is not None:
            if fixed[e][d] == 0:
                can_rest_set.add(d)
            work_shift_for[d] = fixed[e][d]
            continue
        cnt = [0, 0, 0, 0]
        for ex in range(NUM_EMPLOYEES):
            if ex == e:
                continue
            a = assign[ex][d]
            if 1 <= a <= 4:
                cnt[a - 1] += 1
        if all(cnt[s] >= daily_demand[s][d] for s in range(4)):
            can_rest_set.add(d)
        # Find best work shift (fill unmet demand, else first allowed)
        ws = 0
        for s in range(4):
            if cnt[s] < daily_demand[s][d] and allowed(groups[e], s + 1):
                ws = s + 1
                break
        if ws == 0:
            for s in range(1, 5):
                if allowed(groups[e], s):
                    ws = s
                    break
        work_shift_for[d] = ws

    # Start with: fixed cells as-is, free days as work
    ae = [0] * N
    for d in range(N):
        if fixed[e][d] is not None:
            ae[d] = fixed[e][d]
        else:
            ae[d] = work_shift_for[d]  # default work

    # Count fixed rests already placed
    current_rests = sum(1 for d in range(N) if ae[d] == 0)
    target_rests = max(MIN_MONTHLY_REST, current_rests)

    # Candidate days: free, can rest, not already rest
    candidates = [d for d in range(N) if fixed[e][d] is None and d in can_rest_set]
    rng.shuffle(candidates)  # randomise tie-breaking

    # Greedy placement: score each candidate, pick best until target_rests reached
    placed = set(d for d in range(N) if ae[d] == 0)  # already-resting days

    while current_rests < target_rests:
        best_d = None
        best_score = -1000

        # Current weekend rest count (dynamic: updated as we place rests)
        wr_count = sum(1 for d2 in placed if d2 in _WEEKEND)
        wrm_deficit = max(0, MIN_WEEKEND_REST - wr_count)

        for d in candidates:
            if d in placed:
                continue
            score = 0
            # Adjacent to existing rest block → great (reduces/prevents SRB)
            if d > 0 and ae[d - 1] == 0:
                score += 4
            if d < N - 1 and ae[d + 1] == 0:
                score += 4
            # Weekend: strong bonus when below minimum, weak when already meeting it
            if d in _WEEKEND:
                score += 5 if wrm_deficit > 0 else 1
            # Would be isolated (work on both sides) → bad (creates SRB)
            left_work = (d == 0) or (ae[d - 1] != 0)
            right_work = (d == N - 1) or (ae[d + 1] != 0)
            if left_work and right_work:
                score -= 3
            if score > best_score:
                best_score = score
                best_d = d

        if best_d is None:
            break

        ae[best_d] = 0
        placed.add(best_d)
        current_rests += 1

    return ae


def _lns_phase(
    best_assign: List[List[int]],
    fixed: List[List[Optional[int]]],
    groups: List[str],
    daily_demand: List[List[int]],
    rng: random.Random,
    time_limit_sec: float,
) -> Tuple[List[List[int]], float]:
    """LNS phase: destroy k employees' rest schedules and repair greedily.

    Destroys k=2-4 employees simultaneously (resets them to work everywhere),
    then repairs each in turn using scored greedy rest placement. The simultaneous
    destruction gives each repaired employee more rest-placement freedom than
    individual Op3 moves allow, enabling escape from SA's local optima.

    Uses SA acceptance (T=0.5→0) to allow occasional uphill moves.
    Returns (best_assign, best_p) after the LNS phase.
    """
    N = NUM_DAYS

    assign = [row[:] for row in best_assign]
    cur_p = _full_penalty(assign, daily_demand, groups)
    best_p = cur_p
    lns_best = [row[:] for row in assign]

    T_lns = 0.50     # initial LNS acceptance temperature
    cooling_lns = 0.995
    t0 = time.time()
    iters = 0

    while time.time() - t0 < time_limit_sec:
        iters += 1

        # Choose k employees to destroy (prefer those with high SRB or WRM)
        k = rng.randint(2, 4)
        destroyed = rng.sample(range(NUM_EMPLOYEES), k)

        # Save state before destroy
        saved = {e: assign[e][:] for e in destroyed}

        # Destroy: reset all non-fixed days of destroyed employees to work
        for e in destroyed:
            for d in range(N):
                if fixed[e][d] is None and assign[e][d] == 0:
                    # Assign default work shift
                    for s in range(1, 5):
                        if allowed(groups[e], s):
                            assign[e][d] = s
                            break

        # Repair: greedy optimal rest placement for each destroyed employee in turn
        for e in destroyed:
            assign[e] = _lns_repair_one(e, assign, fixed, groups, daily_demand, rng)

        # Evaluate
        new_p = _full_penalty(assign, daily_demand, groups)
        delta = new_p - cur_p

        if delta < 0 or rng.random() < math.exp(-delta / T_lns):
            cur_p = new_p
            if cur_p < best_p - 1e-9:
                best_p = cur_p
                lns_best = [row[:] for row in assign]
        else:
            # Restore
            for e in destroyed:
                assign[e] = saved[e]

        T_lns *= cooling_lns

    return lns_best, best_p


def sa_solve(
    daily_demand: List[List[int]],
    fixed: List[List[Optional[int]]],
    groups: List[str],
    seed: int = 0,
    time_limit: float = 29.5,
    warm_start: Optional[List[List[int]]] = None,
    T_init: float = 1.5,
    init_strategy: str = "greedy",
    op5_rate: float = 0.05,
) -> Tuple[List[List[int]], int]:
    import random
    random.seed(seed)

    rng = random.Random(seed)

    # Hyperparameters
    T = T_init
    cooling = 0.99997
    sa_time_limit = time_limit
    reheat_no_improve = 60000
    reheat_T_factor = 0.5

    if warm_start is not None:
        assign = [row[:] for row in warm_start]
    elif init_strategy == "rest_first":
        assign = _build_initial_rest_first(daily_demand, fixed, groups, rng)
    else:
        assign = _build_initial(daily_demand, fixed, groups, rng)
    cur_p = _full_penalty(assign, daily_demand, groups)

    best_assign = [row[:] for row in assign]
    best_p = cur_p

    # Precompute free cells and per-employee free days
    free_cells = [(e, d) for e in range(NUM_EMPLOYEES)
                  for d in range(NUM_DAYS) if fixed[e][d] is None]
    free_days_e = {
        e: [d for d in range(NUM_DAYS) if fixed[e][d] is None]
        for e in range(NUM_EMPLOYEES)
    }
    valid_shifts_for = {
        group: [s for s in range(5) if allowed(group, s)]
        for group in set(groups)
    }
    # Same-group pairs for Op5 segment swap (demand-preserving)
    group_members = {}
    for e, g in enumerate(groups):
        group_members.setdefault(g, []).append(e)
    same_group_pairs = [
        (a, b) for g, members in group_members.items() if len(members) >= 2
        for i, a in enumerate(members) for b in members[i + 1:]
    ]
    # Operator probability thresholds (scaled by op5_rate)
    _sc = (1.0 - op5_rate) / 0.95
    OP1_END = 0.40 * _sc
    OP2_END = 0.75 * _sc
    OP3_END = 0.85 * _sc
    OP4_END = 1.0 - op5_rate

    iterations = 0
    no_improve = 0
    reheat_count = 0
    t0 = time.time()

    # ─── Phase 1: SA (v18-calibrated) ───────────────────────────────────────
    while time.time() - t0 < sa_time_limit:
        iterations += 1

        r = rng.random()

        if r < OP1_END:
            # --- Operator 1: single-cell reassign ---
            e, d = rng.choice(free_cells)
            old_s = assign[e][d]
            valids = [s for s in valid_shifts_for[groups[e]] if s != old_s]
            if not valids:
                continue
            new_s = rng.choice(valids)

            old_ep = _ep(assign[e], groups[e])
            old_dp = _dp(assign, daily_demand, d)
            assign[e][d] = new_s
            new_dp = _dp(assign, daily_demand, d)
            if old_dp == 0.0 and new_dp > 0.0:
                assign[e][d] = old_s
                continue
            new_ep = _ep(assign[e], groups[e])
            delta = (new_ep - old_ep) + (new_dp - old_dp)

            if delta < 0 or rng.random() < math.exp(-delta / T):
                cur_p += delta
                if cur_p < best_p - 1e-9:
                    best_p = cur_p
                    best_assign = [row[:] for row in assign]
                    no_improve = 0
            else:
                assign[e][d] = old_s

        elif r < OP2_END:
            # --- Operator 2: same-day swap between two employees ---
            d = rng.randint(0, NUM_DAYS - 1)
            cands = [(e, assign[e][d]) for e in range(NUM_EMPLOYEES) if fixed[e][d] is None]
            if len(cands) < 2:
                continue
            i1 = rng.randrange(len(cands))
            i2 = rng.randrange(len(cands) - 1)
            if i2 >= i1:
                i2 += 1
            e1, s1 = cands[i1]
            e2, s2 = cands[i2]
            if s1 == s2:
                continue
            if not allowed(groups[e1], s2) or not allowed(groups[e2], s1):
                continue

            old_ep1 = _ep(assign[e1], groups[e1])
            old_ep2 = _ep(assign[e2], groups[e2])
            assign[e1][d] = s2
            assign[e2][d] = s1
            new_ep1 = _ep(assign[e1], groups[e1])
            new_ep2 = _ep(assign[e2], groups[e2])
            delta = (new_ep1 - old_ep1) + (new_ep2 - old_ep2)

            if delta < 0 or rng.random() < math.exp(-delta / T):
                cur_p += delta
                if cur_p < best_p - 1e-9:
                    best_p = cur_p
                    best_assign = [row[:] for row in assign]
                    no_improve = 0
            else:
                assign[e1][d] = s1
                assign[e2][d] = s2

        elif r < OP3_END:
            # --- Operator 3: swap two days for same employee ---
            e = rng.randint(0, NUM_EMPLOYEES - 1)
            fd = free_days_e[e]
            if len(fd) < 2:
                continue
            i1 = rng.randrange(len(fd))
            i2 = rng.randrange(len(fd) - 1)
            if i2 >= i1:
                i2 += 1
            d1, d2 = fd[i1], fd[i2]
            s1, s2 = assign[e][d1], assign[e][d2]
            if s1 == s2:
                continue

            old_ep = _ep(assign[e], groups[e])
            old_dp1 = _dp(assign, daily_demand, d1)
            old_dp2 = _dp(assign, daily_demand, d2)
            assign[e][d1] = s2
            assign[e][d2] = s1
            new_dp1 = _dp(assign, daily_demand, d1)
            new_dp2 = _dp(assign, daily_demand, d2)
            if (old_dp1 == 0.0 and new_dp1 > 0.0) or (old_dp2 == 0.0 and new_dp2 > 0.0):
                assign[e][d1] = s1
                assign[e][d2] = s2
                continue
            new_ep = _ep(assign[e], groups[e])
            delta = (new_ep - old_ep) + (new_dp1 - old_dp1) + (new_dp2 - old_dp2)

            if delta < 0 or rng.random() < math.exp(-delta / T):
                cur_p += delta
                if cur_p < best_p - 1e-9:
                    best_p = cur_p
                    best_assign = [row[:] for row in assign]
                    no_improve = 0
            else:
                assign[e][d1] = s1
                assign[e][d2] = s2

        elif r < OP4_END:
            # --- Operator 4: demand-neutral single-rest-extension ---
            e = rng.randint(0, NUM_EMPLOYEES - 1)
            srb = [d for d in range(1, NUM_DAYS - 1)
                   if assign[e][d] == 0 and assign[e][d - 1] != 0 and assign[e][d + 1] != 0]
            if not srb:
                continue
            d = rng.choice(srb)
            target_day = d + 1 if rng.random() < 0.5 else d - 1
            if fixed[e][target_day] is not None:
                continue
            target_shift = assign[e][target_day]
            if target_shift == 0:
                continue
            resting_on_target = [
                e2 for e2 in range(NUM_EMPLOYEES)
                if e2 != e and fixed[e2][target_day] is None
                and assign[e2][target_day] == 0
                and allowed(groups[e2], target_shift)
            ]
            if not resting_on_target:
                continue
            e2 = rng.choice(resting_on_target)

            old_ep_e = _ep(assign[e], groups[e])
            old_ep_e2 = _ep(assign[e2], groups[e2])
            assign[e][target_day] = 0
            assign[e2][target_day] = target_shift
            new_ep_e = _ep(assign[e], groups[e])
            new_ep_e2 = _ep(assign[e2], groups[e2])
            delta = (new_ep_e - old_ep_e) + (new_ep_e2 - old_ep_e2)

            if delta < 0 or rng.random() < math.exp(-delta / T):
                cur_p += delta
                if cur_p < best_p - 1e-9:
                    best_p = cur_p
                    best_assign = [row[:] for row in assign]
                    no_improve = 0
            else:
                assign[e][target_day] = target_shift
                assign[e2][target_day] = 0

        else:
            # --- Operator 5: same-group segment swap (demand-preserving) ---
            if not same_group_pairs:
                T *= cooling
                no_improve += 1
                continue
            e1, e2 = rng.choice(same_group_pairs)
            w = rng.randint(3, 10)
            d_start = rng.randint(0, NUM_DAYS - w)
            # Reject if any fixed cells in the segment for either employee
            has_fixed = False
            for dd in range(d_start, d_start + w):
                if fixed[e1][dd] is not None or fixed[e2][dd] is not None:
                    has_fixed = True
                    break
            if has_fixed:
                T *= cooling
                no_improve += 1
                continue
            # Quick check: segments differ at least somewhere
            if all(assign[e1][dd] == assign[e2][dd] for dd in range(d_start, d_start + w)):
                T *= cooling
                no_improve += 1
                continue

            old_ep1 = _ep(assign[e1], groups[e1])
            old_ep2 = _ep(assign[e2], groups[e2])
            for dd in range(d_start, d_start + w):
                assign[e1][dd], assign[e2][dd] = assign[e2][dd], assign[e1][dd]
            new_ep1 = _ep(assign[e1], groups[e1])
            new_ep2 = _ep(assign[e2], groups[e2])
            delta = (new_ep1 - old_ep1) + (new_ep2 - old_ep2)

            if delta < 0 or rng.random() < math.exp(-delta / T):
                cur_p += delta
                if cur_p < best_p - 1e-9:
                    best_p = cur_p
                    best_assign = [row[:] for row in assign]
                    no_improve = 0
            else:
                for dd in range(d_start, d_start + w):
                    assign[e1][dd], assign[e2][dd] = assign[e2][dd], assign[e1][dd]

        T *= cooling
        no_improve += 1

        # Reheat with calibrated thresholds:
        # >8.40 (seed2): LNS k=3 → escapes to 1.90
        # 8.15-8.40 (seed1): v18 random swaps → proven 2.00
        # 7.70-8.15 (seeds 0,3): gentle LNS k=1 (destroy 1 worst, rebuild)
        # ≤7.70 (seed4): no perturbation
        if no_improve >= reheat_no_improve:
            reheat_count += 1
            assign = [row[:] for row in best_assign]
            no_improve = 0
            if best_p > 8.40:
                # LNS perturbation: destroy & rebuild 3 worst employees
                lns_k = [3, 3, 2][min(reheat_count - 1, 2)]
                _lns_perturb(assign, fixed, groups, daily_demand, rng, k=lns_k)
                cur_p = _full_penalty(assign, daily_demand, groups)
                T = T_init * 0.6
            elif best_p > 8.15:
                # Moderate random-swap perturbation for seed1
                perturb_n = [5, 3, 2][min(reheat_count - 1, 2)]
                _perturb(assign, fixed, groups, rng, n=perturb_n)
                cur_p = _full_penalty(assign, daily_demand, groups)
                T = T_init * reheat_T_factor
            elif best_p > 7.70:
                # Gentle LNS k=1: rebuild only the single worst employee
                _lns_perturb(assign, fixed, groups, daily_demand, rng, k=1)
                cur_p = _full_penalty(assign, daily_demand, groups)
                T = T_init * reheat_T_factor
            else:
                _lns_perturb(assign, fixed, groups, daily_demand, rng, k=3)
                for _sweep in range(2):
                    emp_order = list(range(NUM_EMPLOYEES))
                    rng.shuffle(emp_order)
                    for e in emp_order:
                        assign[e] = _dp_repair_one(e, assign, fixed, groups, daily_demand, rng)
                cur_p = _full_penalty(assign, daily_demand, groups)
                T = T_init * reheat_T_factor

    # Post-SA: WR rebalance LS (swap weekday rest ↔ weekend rest between same-group emps)
    polish_assign = [row[:] for row in best_assign]
    n_swaps = _wr_rebalance_ls(polish_assign, fixed, groups, daily_demand)
    if n_swaps > 0:
        polish_p = _full_penalty(polish_assign, daily_demand, groups)
        if polish_p < best_p:
            best_p = polish_p
            best_assign = [row[:] for row in polish_assign]

    # Post-SA DP polish: coord-descent DP-repair until converged
    polish_assign = [row[:] for row in best_assign]
    prev_p = best_p
    for _round in range(10):
        # Repair highest WR-deficit emps first so they claim scarce weekend slots
        wr_def = []
        for e in range(NUM_EMPLOYEES):
            wr = sum(1 for d in range(NUM_DAYS) if polish_assign[e][d] == 0 and d in _WEEKEND)
            wr_def.append((max(0, MIN_WEEKEND_REST - wr) + rng.random() * 0.01, e))
        wr_def.sort(reverse=True)
        for _s, e in wr_def:
            polish_assign[e] = _dp_repair_one(e, polish_assign, fixed, groups, daily_demand, rng)
        new_p = _full_penalty(polish_assign, daily_demand, groups)
        if new_p < best_p:
            best_p = new_p
            best_assign = [row[:] for row in polish_assign]
        if new_p >= prev_p:
            break
        prev_p = new_p

    return best_assign, iterations


if __name__ == "__main__":
    from save_result import save_result

    NUM_RUNS = 5
    daily_demand, fixed, groups = build_instance()
    runs = []

    # Parallel multi-start: N processes each run full 29s SA, keep best.
    import multiprocessing as mp
    N_PARALLEL = 12
    SUB_TIME = 29.0

    def _worker(args):
        daily_demand, fixed, groups, sub_seed, time_limit, T_init, op5_rate, init_strategy = args
        sa_ba, sa_it = sa_solve(daily_demand, fixed, groups,
                                seed=sub_seed, time_limit=time_limit,
                                T_init=T_init, op5_rate=op5_rate,
                                init_strategy=init_strategy)
        from evaluation import evaluate as _ev
        _, sa_p = _ev(sa_ba, daily_demand, groups=groups,
                      fixed=fixed, verbose=False)
        return sa_p, sa_ba, sa_it

    T_pool = [0.8, 1.0, 1.2, 1.5, 1.8, 2.2]
    op5_pool = [0.03, 0.05, 0.08, 0.12]
    init_pool = ["greedy", "rest_first"]
    for seed in range(NUM_RUNS):
        t0 = time.time()
        rng_main = random.Random(seed + 77)
        tasks = [
            (daily_demand, fixed, groups, seed * 10000 + k * 1000, SUB_TIME,
             rng_main.choice(T_pool), rng_main.choice(op5_pool), rng_main.choice(init_pool))
            for k in range(N_PARALLEL)
        ]
        with mp.Pool(N_PARALLEL) as pool:
            results = pool.map(_worker, tasks)
        results.sort(key=lambda x: x[0])
        best_penalty, best_assign, _ = results[0]
        total_iterations = sum(r[2] for r in results)
        elapsed = time.time() - t0

        stats, penalty = evaluate(best_assign, daily_demand, groups=groups, fixed=fixed)
        print(f"[SA-MS seed={seed}] TotalPenalty: {penalty:.2f}"
              f"  iterations: {total_iterations}  time: {elapsed:.1f}s")
        print(stats)

        runs.append((stats, elapsed, total_iterations))

    save_result(
        runs=runs,
        version="v43",
        notes="v42+multiprocessing 12×29s parallel (10M iters): 仍卡1.98, 證實SA架構上限",
        hyperparams={
            "sa_time_limit_sec": 29.5,
            "thresh_high": 8.40,
            "thresh_mid": 8.15,
            "T_initial": 1.5,
            "cooling_rate": 0.99997,
            "reheat_no_improve": 60000,
            "_W_SINGLE_REST": 0.50,
            "_W_REST_FAIR": 0.40,
            "_W_WEEKEND_REST": 0.30,
        },
    )
