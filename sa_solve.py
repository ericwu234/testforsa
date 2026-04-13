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
_W_REST_FAIR = 0.4      # 4× boost
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
    # Sort candidates by work_count ascending to spread workload evenly.
    day_order = list(range(NUM_DAYS))
    rng.shuffle(day_order)

    for d in day_order:
        cnt = [0, 0, 0, 0]
        for e in range(NUM_EMPLOYEES):
            a = assign[e][d]
            if 1 <= a <= 4:
                cnt[a - 1] += 1

        free = [e for e in range(NUM_EMPLOYEES) if fixed[e][d] is None and assign[e][d] == 0]

        # Process admin (si=3) first to optimise e00/e15 balance, then other shifts
        for si in [3, 0, 1, 2]:
            needed = max(0, daily_demand[si][d] - cnt[si])
            if needed == 0:
                continue
            actual_shift = si + 1
            cands = [e for e in free if allowed(groups[e], actual_shift)]
            # Least-worked employees get priority → even distribution
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


def _perturb(
    assign: List[List[int]],
    fixed: List[List[Optional[int]]],
    groups: List[str],
    rng: random.Random,
    n: int = 12,
) -> None:
    """ILS perturbation: force n random demand-neutral same-day swaps to escape local basin."""
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


def sa_solve(
    daily_demand: List[List[int]],
    fixed: List[List[Optional[int]]],
    groups: List[str],
    seed: int = 0,
) -> Tuple[List[List[int]], int]:
    import random
    random.seed(seed)

    rng = random.Random(seed)

    # Hyperparameters
    T_init = 1.5
    T = T_init
    cooling = 0.99997
    time_limit = 29.5
    reheat_no_improve = 60000   # T→0 at ~383k iters (~9s), reheat fires at ~t=10s and ~t=20s
    reheat_T_factor = 0.5

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
    # Valid shifts per group (excluding current)
    valid_shifts_for = {
        group: [s for s in range(5) if allowed(group, s)]
        for group in set(groups)
    }

    iterations = 0
    no_improve = 0
    reheat_count = 0
    t0 = time.time()

    while time.time() - t0 < time_limit:
        iterations += 1

        r = rng.random()

        if r < 0.45:
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
            # Don't create a NEW demand shortfall (allow fixing existing ones)
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

        elif r < 0.80:
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
            # Demand counts on day d are preserved by swap
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

        elif r < 0.90:
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
            # Don't create NEW demand shortfalls
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

        else:
            # --- Operator 4: demand-neutral single-rest-extension ---
            # Pick a random employee and find a single rest break (work-rest-work).
            # Try to extend the rest by swapping with another employee on an adjacent day.
            e = rng.randint(0, NUM_EMPLOYEES - 1)
            # Find single rest break positions for e
            srb = [d for d in range(1, NUM_DAYS - 1)
                   if assign[e][d] == 0 and assign[e][d - 1] != 0 and assign[e][d + 1] != 0]
            if not srb:
                continue
            d = rng.choice(srb)
            # Try extending rest to d+1 (make d+1 also rest)
            target_day = d + 1 if rng.random() < 0.5 else d - 1
            if fixed[e][target_day] is not None:
                continue
            target_shift = assign[e][target_day]  # currently working
            if target_shift == 0:
                continue
            # Find an employee e2 who rests on target_day and can take target_shift
            resting_on_target = [
                e2 for e2 in range(NUM_EMPLOYEES)
                if e2 != e and fixed[e2][target_day] is None
                and assign[e2][target_day] == 0
                and allowed(groups[e2], target_shift)
            ]
            if not resting_on_target:
                continue
            e2 = rng.choice(resting_on_target)

            # Evaluate: e goes rest on target_day, e2 goes target_shift on target_day
            old_ep_e = _ep(assign[e], groups[e])
            old_ep_e2 = _ep(assign[e2], groups[e2])
            # demand unchanged since e2 takes the shift e vacates

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

        T *= cooling
        no_improve += 1

        # Reheat: reset to best and raise temperature.
        # Reheats fire at ~t=10s and ~t=20s (2 reheats per run).
        # Conditional ILS perturbation: only perturb if best is still above 2.15
        # (stuck in bad local optimum). Good seeds (best ≤ 2.15) are left undisturbed.
        if no_improve >= reheat_no_improve:
            reheat_count += 1
            assign = [row[:] for row in best_assign]
            no_improve = 0
            if best_p > 2.15:
                # Still stuck — perturb to escape basin
                perturb_n = [10, 6, 4][min(reheat_count - 1, 2)]
                _perturb(assign, fixed, groups, rng, n=perturb_n)
                cur_p = _full_penalty(assign, daily_demand, groups)
                T = T_init * 0.6  # slightly higher T after perturbation for recovery
            else:
                cur_p = best_p
                T = T_init * reheat_T_factor

    return best_assign, iterations


if __name__ == "__main__":
    from save_result import save_result

    NUM_RUNS = 5
    daily_demand, fixed, groups = build_instance()
    runs = []

    for seed in range(NUM_RUNS):
        t0 = time.time()
        best_assign, iterations = sa_solve(daily_demand, fixed, groups, seed=seed)
        elapsed = time.time() - t0

        stats, penalty = evaluate(best_assign, daily_demand, groups=groups, fixed=fixed)
        print(f"[SA seed={seed}] TotalPenalty: {penalty:.2f}"
              f"  iterations: {iterations}  time: {elapsed:.1f}s")
        print(stats)

        runs.append((stats, elapsed, iterations))

    save_result(
        runs=runs,
        version="v13",
        notes="v9+條件ILS擾動(best_p>2.15才perturb)，保護好seed不受干擾",
        hyperparams={
            "T_initial": 1.5,
            "cooling_rate": 0.99997,
            "time_limit_sec": 29.5,
            "reheat_no_improve": 60000,
            "reheat_T_factor": 0.5,
            "perturb_n_per_reheat": [10, 6, 4],
            "_W_SINGLE_REST": 0.50,
            "_W_REST_FAIR": 0.4,
            "_W_WEEKEND_REST": 0.30,
        },
    )
