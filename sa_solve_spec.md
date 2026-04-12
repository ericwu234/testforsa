# SA 排班最佳化實作規格

## 一、目標

實作模擬退火演算法（Simulated Annealing，SA），以 `evaluation.py` 的 `evaluate()` 為評估標準，在相同資料設定下取得比 OR-Tools CP-SAT 求解器更低的懲罰分數。

**基準線（OR-Tools 30 秒 × 5 seeds 平均）**

```
mean TotalPenalty : 2.56   std=0.15   best=2.30   worst=2.70
DemandDeviation   : 0.0    ← 人力已滿足，不可退步
SingleRestBreaks  : 11.6   ← 最大改善空間
CrossGroupCount   : 3.2
```

目標：`5-run mean TotalPenalty < 2.56`，且每次執行的 `DemandDeviation == 0`。

---

## 二、問題設定

資料由 `instance.py` 的 `build_instance()` 統一提供，**不可在 `sa_solve.py` 中重新定義**。

| 項目 | 值 |
|---|---|
| 員工數 `num_employees` | 16 |
| 天數 `num_days` | 30 |
| 班別代碼 | 0=休、1=早、2=午、3=夜、4=行政 |
| 週末定義 | `d % 7 in {0, 1}`（週六、週日） |

**資料結構**

- `assign[e][d]`：`int`，員工 `e` 在第 `d` 天的班別
- `daily_demand[s][d]`：`int`，班別索引 `s`（0=早、1=午、2=夜、3=行政）在第 `d` 天的需求人數
- `groups[e]`：`str`，員工所屬群組（決定可排的班別，見 `evaluation.py` 的 `allowed()`）
- `fixed[e][d]`：`Optional[int]`，預排班別，`None` 表示自由排班

---

## 三、硬限制（Hard Constraints）

以下限制**必須始終滿足**，違反者直接拒絕該移動，不進入接受/拒絕判斷：

1. **每人每天恰好一個班別**：`assign[e][d] ∈ {0, 1, 2, 3, 4}`
2. **固定班別不可更動**：若 `fixed[e][d] is not None`，移動時跳過這些格子

---

## 四、評估規則：5-Run 平均

SA 具有隨機性，**單次結果不具統計意義**。每個版本的正式成績為 **5 次獨立執行的平均 TotalPenalty**。

- 每次執行使用不同的固定隨機種子（建議 `seed = 0, 1, 2, 3, 4`）
- **打敗基準的判斷標準**：5-run 的 `mean TotalPenalty < 2.56`
- `std` 越小代表演算法越穩定，與 `mean` 同等重要

SA 主函式必須接受 `seed` 參數以確保可重現性：

```python
def sa_solve(
    daily_demand: List[List[int]],
    fixed: List[List[Optional[int]]],
    groups: List[str],
    seed: int = 0,
) -> Tuple[List[List[int]], int]:
    """
    Returns
    -------
    assign     : 最佳班表 [num_employees][num_days]
    iterations : 實際執行的迭代次數
    """
```

---

## 五、解的表示與初始解

```python
assign: List[List[int]]  # shape: [num_employees][num_days]
```

**初始解建議**（擇一）

- **方案 A（推薦）**：使用 OR-Tools 的輸出作為初始解，在已知高品質解上進行局部搜尋
- **方案 B**：根據群組限制與需求隨機生成，但必須確保 hard constraint 全部成立再開始退火

---

## 六、鄰域算子（Neighbourhood Operators）

每次迭代從以下算子中隨機選一種執行，生成候選解：

### 算子 1：單格修改（Single-cell Shift）

隨機選一個**非固定**格子 `(e, d)`，改為另一個對該員工合法的班別（`allowed(groups[e], s) == True` 且 `s != assign[e][d]`）。

### 算子 2：同員工換天（Intra-employee Day Swap）

隨機選同一員工 `e` 的兩天 `(d1, d2)`（兩格皆非固定），互換其班別。  
僅在兩天互換後對該員工群組仍合法時才執行。

### 算子 3：跨員工換班（Inter-employee Shift Swap）

隨機選兩位員工 `(e1, e2)` 與同一天 `d`（兩格皆非固定），互換其班別。  
僅在 `assign[e1][d]` 對 `groups[e2]` 合法，且 `assign[e2][d]` 對 `groups[e1]` 合法時才執行。

> 算子 3 在不改變每日人力分布的情況下調整個人排班，對改善 `CrossGroupCount` 與 `SingleRestBreaks` 特別有效。

---

## 七、SA 演算法架構

```
初始化：
    solution ← 初始解（深拷貝）
    best     ← solution（深拷貝）
    T        ← T_initial

迴圈（直到時間到或達到 max_iterations）：
    candidate ← apply_random_operator(solution)  # 對 solution 的副本操作
    若 candidate 違反任何 hard constraint → 跳過

    delta ← penalty(candidate) - penalty(solution)  # 見「效能注意事項」

    若 delta < 0：
        solution ← candidate
        若 penalty(solution) < penalty(best)：
            best ← solution（深拷貝）
    否則：
        以機率 exp(-delta / T) 接受：
            solution ← candidate

    T ← T × cooling_rate

回傳 best
```

---

## 八、效能注意事項（重要）

> **`evaluate()` 每次呼叫都會印出完整班表**，若在 SA 熱循環中直接使用會產生數十萬行輸出，且每次重新計算所有項目效率極低。

**正確做法：在熱循環中自行實作輕量版 penalty 計算。**

```python
def compute_penalty(assign, daily_demand, groups, fixed) -> float:
    """不印任何東西，只回傳 TotalPenalty，用於 SA 熱循環內的 delta 計算。"""
    # 直接複製 evaluation.py 中的計算邏輯，移除所有 print 語句
    ...
```

也可考慮**增量更新（incremental delta）**：移動只影響少數幾個員工和天，只重算受影響的項目，可大幅提升速度。

`evaluate()` 只在以下時機呼叫：
- 每次 run 結束後，取得最終 `ViolationStats` 用於記錄
- 除此之外不呼叫

---

## 九、超參數設計

以下超參數由實作者依問題規模與實驗結果自行調校：

| 參數 | 說明 |
|---|---|
| `T_initial` | 初始溫度，控制初期接受劣解的機率，需與 penalty 量級匹配 |
| `cooling_rate` | 每步降溫比例（`T ← T × cooling_rate`），決定探索轉為開發的速度 |
| `max_iterations` | 總迭代上限，作為時間限制以外的安全停止條件 |
| `time_limit_sec` | 執行時間上限，建議優先以此為主要停止條件 |
| 算子選擇比例 | 三種算子各自被選中的機率，可視改善瓶頸動態調整 |

> 可在執行過程中監控**近期接受率**（近 N 次迭代中被接受的比例），作為溫度是否合適的診斷依據。

---

## 十、輸出格式

每次 run 結束後印出以下資訊（格式與 `ortools_solve.py` 一致）：

```
[SA seed=0] TotalPenalty: X.XX  iterations: XXXXXX  time: XX.Xs
表示法：0=休,1=早,2=午,3=夜,4=行
員工01: 1,2,0,...
...
每日現有人力分布 (早,午,夜,行):
Day 01: 5 , 3 , 2 , 1  (需求: 5,3,2,1)
...
ViolationStats(...)
```

---

## 十一、檔案結構

```
scheduling/
├── instance.py         # 問題資料（daily_demand, fixed, groups）唯一來源
├── evaluation.py       # 評估函式，不可修改
├── ortools_solve.py    # 基準求解器
├── save_result.py      # 實驗結果記錄工具
├── show_results.py     # 所有版本比較表
├── sa_solve.py         # 本次實作目標（待建立）
├── results/
│   └── baseline_ortools.json  # 基準線（5-run，mean=2.56）
├── evaluation.md       # evaluate() 完整說明
└── sa_solve_spec.md    # 本文件
```

**`sa_solve.py` 的 import 結構**

```python
from instance import build_instance
from evaluation import evaluate, allowed
```

---

## 十二、研究工作流程

### Step 1：執行 5 次並記錄結果

```python
from save_result import save_result
from instance import build_instance
from evaluation import evaluate
import time

NUM_RUNS = 5
daily_demand, fixed, groups = build_instance()
runs = []

for seed in range(NUM_RUNS):
    t0 = time.time()
    best_assign, iterations = sa_solve(daily_demand, fixed, groups, seed=seed)
    elapsed = time.time() - t0
    stats, _ = evaluate(best_assign, daily_demand, groups=groups, fixed=fixed)
    runs.append((stats, elapsed, iterations))

save_result(
    runs=runs,
    version="v1",
    notes="基礎 SA，三種算子等比例",
    hyperparams={"T_initial": ..., "cooling_rate": ..., "time_limit_sec": ...},
)
```

### Step 2：比較所有版本

```bash
python3 show_results.py          # 完整列表（baseline 置頂，其餘按 mean 排序）
python3 show_results.py --top 5  # 只看最佳 5 筆
```

### Step 3：版本控管

每當一個版本有明確進展，建立 git commit：

```bash
git add sa_solve.py
git commit -m "v2: 加入 reheating，mean=2.40，std=0.08"
```

---

## 十三、驗證清單

實作完成後確認以下項目：

- [ ] `fixed` 格子在整個 SA 過程中未被更動
- [ ] 所有 `assign[e][d]` 值都在 `{0,1,2,3,4}` 內
- [ ] 每次 run 的 `DemandDeviation == 0`
- [ ] 熱循環內未直接呼叫 `evaluate()`（改用輕量版 `compute_penalty()`）
- [ ] 5-run `mean TotalPenalty < 2.56`（打敗 OR-Tools 基準）
- [ ] `save_result()` 的輸出數字與 `ViolationStats` 一致
