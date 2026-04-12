from typing import List, Optional, Tuple


def build_instance(
    num_employees: int = 16,
    num_days: int = 30,
) -> Tuple[List[List[int]], List[List[Optional[int]]], List[str]]:
    """
    回傳排班問題的資料實例：(daily_demand, fixed, groups)

    daily_demand[s][d] : 班別 s（0=早,1=午,2=夜,3=行政）在第 d 天的需求人數
    fixed[e][d]        : 員工 e 在第 d 天的固定班別，None 表示自由排班
    groups[e]          : 員工 e 所屬群組
    """

    # --------------------------------------------------
    # 每日人力需求（週末 / 平日不同）
    # 週末定義：d % 7 in {0, 1}（第 1 天 = 週六）
    # --------------------------------------------------
    daily_demand = [[0] * num_days for _ in range(4)]
    for d in range(num_days):
        is_weekend = (d % 7 == 0) or (d % 7 == 1)
        if is_weekend:
            daily_demand[0][d] = 4  # 早
            daily_demand[1][d] = 3  # 午
            daily_demand[2][d] = 2  # 夜
            daily_demand[3][d] = 1  # 行政
        else:
            daily_demand[0][d] = 5
            daily_demand[1][d] = 3
            daily_demand[2][d] = 2
            daily_demand[3][d] = 1

    # --------------------------------------------------
    # 預排固定班別
    # --------------------------------------------------
    fixed: List[List[Optional[int]]] = [[None] * num_days for _ in range(num_employees)]
    fixed[0][0]   = 2   # 員工00 第01天 午班
    fixed[0][1]   = 0   # 員工00 第02天 休息
    fixed[1][0]   = 0   # 員工01 第01天 休息
    fixed[2][0]   = 0   # 員工02 第01天 休息
    fixed[3][1]   = 1   # 員工03 第02天 早班
    fixed[4][2]   = 1   # 員工04 第03天 早班
    fixed[9][1]   = 2   # 員工09 第02天 午班
    fixed[12][1]  = 0   # 員工12 第02天 休息
    fixed[12][29] = 0   # 員工12 第30天 休息

    # --------------------------------------------------
    # 員工群組（決定可排的班別）
    # --------------------------------------------------
    groups = [""] * num_employees
    groups[0] = "MorningOrAdmin"    # 早班或行政
    for i in range(1, 7):
        groups[i] = "Morning"       # 僅早班
    for i in range(7, 12):
        groups[i] = "Noon"          # 僅午班
    for i in range(12, 15):
        groups[i] = "Night"         # 僅夜班
    groups[15] = "Admin"            # 僅行政

    return daily_demand, fixed, groups
