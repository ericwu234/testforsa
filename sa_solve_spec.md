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
- **每次執行時間上限：30 秒**（與 OR-Tools 基準相同，確保公平比較）
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
    import random
    random.seed(seed)
    # 若使用 numpy，同步設定：
    # import numpy as np; np.random.seed(seed)
```

---

## 五、解的表示與初始解

```python
assign: List[List[int]]  # shape: [num_employees][num_days]
```

**初始解建議**

- 根據群組限制與需求隨機生成，或使用其他策略建構初始解
- 初始解必須滿足所有 Hard Constraints，且建議 **`DemandDeviation == 0`**（人力需求已滿足），避免退火從高懲罰起點出發

---

## 六、鄰域算子（Neighbourhood Operators）

算子的設計由實作者自主研究，以下為必須遵守的**合法性條件**：

- 每個算子產生的候選解必須滿足所有 Hard Constraints（見第三節）
- 不合法的候選解直接拒絕，不進入接受/拒絕判斷

可以設計多種算子，並在迭代中混合使用。算子設計的核心問題是：**如何在保持合法的前提下，有效探索懲罰較低的解空間？**

---

## 七、SA 演算法定義

模擬退火的核心特徵是：

1. **接受劣解的機率**隨溫度 `T` 降低而遞減，使搜尋從早期的廣泛探索逐漸轉為後期的局部開發
2. **持續追蹤歷史最佳解**（`best`），與當前解（`current`）分開記錄
3. 最終回傳 `best`，而非最後的 `current`

具體的降溫策略、重啟機制、接受函數形式等實作細節由實作者自主研究。

---

## 八、效能注意事項（重要）

`evaluate()` 支援 `verbose` 參數：

```python
# 熱循環內：只計算，不印任何東西
stats, penalty = evaluate(assign, daily_demand, groups=groups, fixed=fixed, verbose=False)

# run 結束後：計算並印出完整班表報告（預設行為）
stats, penalty = evaluate(assign, daily_demand, groups=groups, fixed=fixed)
```

**使用規則：**
- SA 熱循環內一律使用 `verbose=False`
- 每次 run 結束後呼叫一次預設的 `evaluate()`，取得最終 `ViolationStats` 用於記錄

**深拷貝注意**：每次生成候選解前須對當前解做深拷貝，被拒絕時才能還原原始狀態。若直接修改原解再嘗試復原，容易因邏輯錯誤造成狀態污染，是 SA 常見 bug。

也可考慮**增量更新（incremental delta）**：移動只影響少數幾個員工和天，只重算受影響項目，可大幅提升每秒迭代次數。

---

## 九、超參數設計

以下超參數由實作者依問題規模與實驗結果自行調校：

| 參數 | 說明 |
|---|---|
| `T_initial` | 初始溫度，控制初期接受劣解的機率，需與 penalty 量級匹配 |
| `cooling_rate` | 每步降溫比例（`T ← T × cooling_rate`），決定探索轉為開發的速度 |
| `max_iterations` | 總迭代上限，作為時間限制以外的安全停止條件 |
| `time_limit_sec` | 執行時間上限，建議優先以此為主要停止條件 |
| 算子選擇比例 | 各算子被選中的機率，可視改善瓶頸動態調整 |

> 可在執行過程中監控**近期接受率**（近 N 次迭代中被接受的比例），作為溫度是否合適的診斷依據。

---

## 十、輸出格式

每次 run 結束後，由 `__main__` 區塊（非 `sa_solve()` 內部）印出以下資訊：

```python
# __main__ 中的印出方式
print(f"[SA seed={seed}] TotalPenalty: {penalty:.2f}  iterations: {iterations}  time: {elapsed:.1f}s")
stats, penalty = evaluate(best_assign, daily_demand, groups=groups, fixed=fixed)  # verbose=True，印班表
print(stats)
```

預期輸出：
```
[SA seed=0] TotalPenalty: X.XX  iterations: XXXXXX  time: XX.Xs
表示法：0=休,1=早,2=午,3=夜,4=行
員工01: 1,2,0,...
...
ViolationStats(...)
```

---

## 十一、檔案結構

```
scheduling/
├── instance.py         # 問題資料（daily_demand, fixed, groups）唯一來源
├── evaluation.py       # 評估函式（懲罰計算邏輯不可修改，介面參數可擴充）
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
    notes="基礎 SA，初版實作",
    hyperparams={"T_initial": ..., "cooling_rate": ..., "time_limit_sec": ...},
)
```

### Step 2：比較所有版本

```bash
python3 show_results.py          # 完整列表（baseline 置頂，其餘按 mean 排序）
python3 show_results.py --top 5  # 只看最佳 5 筆
```

### Step 3：分析結果並決定下一步改進方向

### Step 4：版本控管

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
- [ ] 熱循環內呼叫 `evaluate(..., verbose=False)`
- [ ] 5-run `mean TotalPenalty < 2.56`（打敗 OR-Tools 基準）
- [ ] `save_result()` 的輸出數字與 `ViolationStats` 一致
