# 員工排班評估模組（`evaluation.py`）

## 一、概述

`evaluation.py` 提供排班表的**品質評估函式**，計算各項違規的加權懲罰總分，作為最佳化演算法的適應度函數。

- 班別代碼：`0`＝休息、`1`＝早班、`2`＝午班、`3`＝夜班、`4`＝行政
- 懲罰分數越低，班表品質越好（最佳解為 0）

---

## 二、公開介面

### 2.1 `evaluate()`

```python
def evaluate(
    assign: List[List[int]],
    daily_demand: List[List[int]],
    groups: Optional[List[str]] = None,
    fixed: Optional[List[List[Optional[int]]]] = None,
    max_consecutive_work: int = 5,
    min_double_rest_occurrences: int = 2,
) -> Tuple[ViolationStats, float]:
```

**回傳值**

| 項目 | 型別 | 說明 |
|---|---|---|
| `ViolationStats` | dataclass | 各項違規的詳細統計 |
| `float` | 浮點數 | 加權懲罰總分（`fitness = TotalPenalty`，越低越好） |

---

### 2.2 `ViolationStats`

```python
@dataclass
class ViolationStats:
    DemandDeviation: float        # 人力缺口總和
    ConsecWorkExceedDays: int     # 連續超班超出天數總和
    TransitionViolations: int     # 換班違規次數
    NightTo_EarlyNoonAdmin: int   #   └ 夜 → 早/午/行政
    NoonTo_EarlyAdmin: int        #   └ 午 → 早/行政
    EarlyNoonAdminTo_Night: int   #   └ 早/午/行政 → 夜
    DoubleRestMissing: int        # 連休次數不足的缺口總和
    RestFairnessMissing: float    # 月休不足 9 天的缺口總和
    WeekendRestMissing: int       # 週末休假不足 4 次的缺口總和
    SingleRestBreaks: int         # 孤立休假（工-休-工）次數
    CrossGroupCount: int          # 跨組別違規次數
    FixedViolations: int          # 固定班別違規次數（不計入懲罰）
    TotalPenalty: float           # 加權懲罰總分
```

---

### 2.3 `allowed(group, shift)`

```python
def allowed(group: str, shift: int) -> bool
```

判斷特定群組是否可排特定班別。規則如下：

| 群組 | 允許班別 |
|---|---|
| `"Morning"` | 早班（1） |
| `"Noon"` | 午班（2） |
| `"Night"` | 夜班（3） |
| `"Admin"` | 行政（4） |
| `"MorningOrAdmin"` | 早班（1）或行政（4） |
| `""` 或其他 | 不限制 |
| 任何群組 | 休息（0）永遠允許 |

---

## 三、輸入參數

### `assign`（必填）

- 型別：`List[List[int]]`，維度 `[員工數 × 天數]`
- 值域：`0`–`4`

```python
assign = [
    [1, 2, 0, 1, 3],   # 員工01：早、午、休、早、夜
    [0, 1, 1, 2, 0],   # 員工02：休、早、早、午、休
]
```

### `daily_demand`（必填）

- 型別：`List[List[int]]`，維度 `[4 × 天數]`
- 列索引對應班別：`[0]`早、`[1]`午、`[2]`夜、`[3]`行政

```python
daily_demand = [
    [5, 5, 4, 5, 5, 5, 5, ...],  # 早班每日需求
    [3, 3, 3, 3, 3, 3, 3, ...],  # 午班每日需求
    [2, 2, 2, 2, 2, 2, 2, ...],  # 夜班每日需求
    [1, 1, 1, 1, 1, 1, 1, ...],  # 行政每日需求
]
```

### `groups`（選填，預設 `None`）

- 型別：`List[str]`，長度等於員工數
- 若為 `None`，跳過跨組別違規檢查

```python
groups = ["MorningOrAdmin", "Morning", "Morning", "Noon", "Night", "Admin"]
```

### `fixed`（選填，預設 `None`）

- 型別：`List[List[Optional[int]]]`，維度同 `assign`
- `int`：該格固定班別；`None`：不限制
- 違反固定設定**僅計入 `FixedViolations`，不計入懲罰**

```python
fixed = [
    [None, 0, None, None, None],   # 員工01 第2天固定休假
    [None, None, None, None, None],
]
```

### `max_consecutive_work`（選填，預設 `5`）

員工連續上班天數上限，超出天數按差額計罰。

### `min_double_rest_occurrences`（選填，預設 `2`）

每位員工每月最少的「非重疊連續休假兩天」次數。  
計算方式採**貪婪掃描**：由左至右，遇到連續兩天休假即計一次並跳過，確保計到最多可能次數。

---

## 四、懲罰項目與權重

| # | 項目 | 計算方式 | 權重 |
|---|---|---|---|
| A | **人力缺口** | Σ `max(0, 需求 − 實際人力)` | 1.0 |
| 1 | **連續超班** | 超過 `max_consecutive_work` 的總天數 | 1.0 |
| 2 | **換班違規** | 違規換班次數（見下方規則） | 1.0 |
| 3 | **連休不足** | 非重疊連休次數 < `min_double_rest_occurrences` 的缺口 | 0.1 |
| 4 | **月休不足** | 月休假 < 9 天的缺口 | 0.1 |
| 5 | **週末休不足** | 週末休假 < 4 次的缺口 | 0.1 |
| 6 | **孤立休假** | 工-休-工 出現次數 | 0.1 |
| 7 | **跨組別違規** | 員工被排到群組不允許班別的次數 | 0.2 |
| 8 | **固定班違規** | 違反 `fixed` 設定的次數 | **不計罰** |

### 換班違規規則

兩天皆為上班（非休息）時，以下順序違規：

| 前一天 | 後一天 | 原因 |
|---|---|---|
| 夜班（3） | 早班（1）、午班（2）、行政（4） | 夜班後需緩衝 |
| 午班（2） | 早班（1）、行政（4） | 午班後接早/行政太緊 |
| 早/午/行政（1,2,4） | 夜班（3） | 日班直接接夜班 |

---

## 五、週末定義

以天數索引（0-based）判斷，每 7 天中的第 0、1 天為週末（週六、週日）：

```python
is_weekend = (d % 7 == 0) or (d % 7 == 1)
```

> 呼叫端需自行對齊月份起始日與星期的對應關係。

---

## 六、完整呼叫範例

```python
from evaluation import evaluate

assign = [
    [1, 2, 3, 0, 0, 1, 2, 1, 3, 0],
    [0, 1, 1, 2, 3, 0, 0, 2, 1, 1],
]

daily_demand = [
    [1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
    [1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
    [1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
]

groups = ["Morning", "Noon"]

fixed = [
    [None] * 10,
    [None] * 10,
]

stats, penalty = evaluate(
    assign=assign,
    daily_demand=daily_demand,
    groups=groups,
    fixed=fixed,
    max_consecutive_work=5,
    min_double_rest_occurrences=2,
)

print(f"Penalty: {penalty:.2f}")          # 越低越好
print(f"Demand deviation: {stats.DemandDeviation}")
print(f"Transition violations: {stats.TransitionViolations}")
print(f"Double rest missing: {stats.DoubleRestMissing}")
print(stats)
```
