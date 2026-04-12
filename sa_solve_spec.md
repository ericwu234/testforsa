# SA 排班最佳化實作規格

## 一、目標

實作模擬退火演算法（Simulated Annealing，SA），以 `evaluation.py` 的 `evaluate()` 為適應度函數，在相同資料設定下取得比 OR-Tools CP-SAT 求解器更低的懲罰分數。

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

與 `ortools_solve.py` 完全相同的資料，**不要**在 `sa_solve.py` 中重新定義，直接呼叫 `ortools_solve.py` 提供的資料生成邏輯，或將資料提取為共用函式。

| 項目 | 值 |
|---|---|
| 員工數 `num_employees` | 16 |
| 天數 `num_days` | 30 |
| 班別 | 0=休、1=早、2=午、3=夜、4=行政 |
| 週末定義 | `d % 7 in {0, 1}`（週六、週日） |

**資料結構**

- `assign[e][d]`：`int`，員工 `e` 在第 `d` 天的班別
- `daily_demand[s][d]`：`int`，班別 `s`（0-based，對應 shift 1–4）在第 `d` 天的需求人數
- `groups[e]`：`str`，員工所屬群組
- `fixed[e][d]`：`Optional[int]`，預排班別，`None` 表示自由排班

---

## 三、硬限制（Hard Constraints）

以下限制**必須始終滿足**，不可作為懲罰項處理，違反者直接拒絕該移動：

1. **每人每天恰好一個班別**：`assign[e][d] ∈ {0, 1, 2, 3, 4}`
2. **固定班別**：若 `fixed[e][d] is not None`，則 `assign[e][d]` 必須等於 `fixed[e][d]`，移動時不可更動這些格子

---

## 四、評估規則：5-Run 平均

SA 具有隨機性，**單次結果不具統計意義**。每個版本的實驗結果以 **5 次獨立執行的平均 TotalPenalty** 作為正式成績。

- 每次執行使用不同的隨機種子（`seed = 0, 1, 2, 3, 4` 或任意 5 個固定值）
- **打敗基準的判斷標準**：5-run 的 `mean TotalPenalty < 2.56`（baseline）
- `std` 越小代表演算法越穩定，也是重要的參考指標

SA 主函式必須接受 `seed` 參數以確保可重現性：

```python
def sa_solve(daily_demand, fixed, groups, seed: int = 0) -> Tuple[List[List[int]], int]:
    """
    Returns
    -------
    assign     : 最佳班表
    iterations : 實際執行的迭代次數
    """
```

---

## 五、解的表示與初始解

```python
assign: List[List[int]]  # shape: [num_employees][num_days]
```

**初始解建議**（擇一）

- **方案 A（推薦）**：直接使用 OR-Tools 的輸出解作為初始解，在已知可行解上進行局部搜尋
- **方案 B**：隨機生成，但必須先修復所有 hard constraint 再開始退火

---

## 五、鄰域算子（Neighbourhood Operators）

每次迭代從以下算子中**隨機選一種**執行，生成候選解：

### 算子 1：單格修改（Single-cell Shift）

隨機選一個**非固定**的格子 `(e, d)`，將其班別改為另一個合法班別（`allowed(groups[e], s) == True` 的 `s`，且 `s != assign[e][d]`）。

### 算子 2：同員工換天（Intra-employee Day Swap）

隨機選同一員工 `e` 的兩天 `(d1, d2)`（兩格皆非固定），互換其班別。
僅在兩天的班別對該員工的群組都合法時才執行。

### 算子 3：跨員工換班（Inter-employee Shift Swap）

隨機選兩位員工 `(e1, e2)` 與同一天 `d`（兩格皆非固定），互換其班別。
僅在 `assign[e1][d]` 對 `groups[e2]` 合法，且 `assign[e2][d]` 對 `groups[e1]` 合法時才執行。

> **提示**：算子 3 能在不改變每日人力分布的情況下調整個人排班，對改善 `CrossGroupCount` 與 `SingleRestBreaks` 特別有效。

---

## 六、SA 演算法架構

```
初始化：
    solution ← 初始解
    best ← solution
    T ← T_initial

迴圈（直到時間到或達到最大迭代次數）：
    candidate ← apply_random_operator(solution)
    若 candidate 違反任何 hard constraint → 跳過

    delta ← evaluate(candidate).TotalPenalty - evaluate(solution).TotalPenalty

    若 delta < 0：
        solution ← candidate
        若 TotalPenalty(solution) < TotalPenalty(best)：
            best ← solution
    否則：
        以機率 exp(-delta / T) 接受 candidate：
            solution ← candidate

    T ← T × cooling_rate

回傳 best
```

---

## 七、超參數設計

以下為需要決定的超參數，數值由實作者依問題規模與實驗結果自行調校：

| 參數 | 說明 |
|---|---|
| `T_initial` | 初始溫度，控制初期接受劣解的機率，需與 penalty 的量級匹配 |
| `cooling_rate` | 每步降溫的比例（`T ← T × cooling_rate`），決定搜尋從探索轉為開發的速度 |
| `max_iterations` | 總迭代上限，作為時間限制之外的安全停止條件 |
| `time_limit_sec` | 執行時間上限，建議優先以此為主要停止條件 |
| 算子選擇比例 | 三種鄰域算子各自被選中的機率，可視改善瓶頸動態調整 |

> 可在執行過程中監控**當前接受率**（近 N 次迭代中被接受的比例），作為調校溫度的參考依據。

---

## 八、輸出格式

執行完畢後印出以下資訊，格式與 `ortools_solve.py` 一致，方便直接比較：

```
[SA] Best TotalPenalty: X.XX  (OR-Tools baseline: 2.6)
[SA] Iterations: XXXXXX  |  Time: XX.Xs
表示法：0=休,1=早,2=午,3=夜,4=行
員工01: 1,2,0,...
...
每日現有人力分布 (早,午,夜,行):
Day 01: 5 , 3 , 2 , 1  (需求: 5,3,2,1)
...
ViolationStats(...)
```

---

## 九、檔案結構

```
scheduling/
├── instance.py         # 問題資料（daily_demand, fixed, groups）
├── evaluation.py       # 適應度函式（不可修改）
├── ortools_solve.py    # 基準求解器
├── sa_solve.py         # 本次實作目標
├── evaluation.md       # evaluate() 完整說明
└── sa_solve_spec.md    # 本文件
```

**`sa_solve.py` 的 import 結構**

```python
from instance import build_instance
from evaluation import evaluate, allowed, ViolationStats
```

---

## 十、研究工作流程

每次修改或實驗後，按以下流程執行：

### Step 1：執行 5 次並記錄結果

在 `sa_solve.py` 結尾執行 5-run 並呼叫 `save_result()`：

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
    version="v1",                        # 每次有意義的改動更新版號
    notes="基礎 SA，三種算子等比例",      # 記錄本次的主要改動
    hyperparams={
        "T_initial": ...,
        "cooling_rate": ...,
        "time_limit_sec": ...,
        "num_runs": NUM_RUNS,
    },
)
```

結果會自動存入 `results/sa_v1_YYYYMMDD_HHMMSS.json`，包含每次個別結果與聚合統計（mean、std、best、worst）。

### Step 2：比較所有版本

```bash
python3 show_results.py          # 完整列表
python3 show_results.py --top 5  # 只看最佳 5 筆
```

輸出範例：

```
-----------------------------------------------------------
版本                    Penalty  Demand  Consec  ...  備註
-----------------------------------------------------------
[baseline]               2.3000     0.0       0  ...  OR-Tools CP-SAT 30s
[v1] ✓ BEAT              2.1000     0.0       0  ...  基礎 SA，三種算子等比例
[v2]                     2.5000     0.0       0  ...  調高初始溫度
-----------------------------------------------------------
共 2 筆 SA 實驗，其中 1 筆打敗基準線（2.3）
```

### Step 3：版本控管

每當一個版本有明確進展（打敗基準或提出新策略），建立 git commit：

```bash
git add sa_solve.py
git commit -m "v1: 基礎 SA，TotalPenalty=2.1，打敗 OR-Tools"
```

這樣可以隨時 `git diff` 比較不同版本的程式碼差異。

---

## 十一、驗證清單

實作完成後確認以下項目：

- [ ] `fixed` 格子在整個 SA 過程中未被更動
- [ ] 所有 `assign[e][d]` 值都在 `{0,1,2,3,4}` 內
- [ ] 最終解的 `DemandDeviation == 0`（人力不可有缺口）
- [ ] `TotalPenalty < 2.6`（打敗 OR-Tools 基準）
- [ ] 印出的班表與 `ViolationStats` 數字互相一致
