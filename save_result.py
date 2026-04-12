"""
實驗結果記錄工具

在 sa_solve.py 跑完後呼叫 save_result()，自動將結果存入 results/ 資料夾。
"""
import json
import os
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, Optional

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")


def save_result(
    stats,
    elapsed_sec: float,
    iterations: int,
    version: str,
    notes: str = "",
    hyperparams: Optional[Dict[str, Any]] = None,
) -> str:
    """
    將一次實驗結果序列化成 JSON 存入 results/。

    Parameters
    ----------
    stats        : evaluate() 回傳的 ViolationStats 物件
    elapsed_sec  : 執行時間（秒）
    iterations   : 實際執行的迭代次數
    version      : 版本標籤，例如 "v1"、"v2-reheat"
    notes        : 本次實驗的文字說明（算法變動、調參動機等）
    hyperparams  : 超參數 dict，任意 key-value

    Returns
    -------
    str : 存檔路徑
    """
    os.makedirs(RESULTS_DIR, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"sa_{version}_{timestamp}.json"
    filepath = os.path.join(RESULTS_DIR, filename)

    record = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "version": version,
        "notes": notes,
        "hyperparams": hyperparams or {},
        "stats": asdict(stats),
        "elapsed_sec": round(elapsed_sec, 2),
        "iterations": iterations,
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)

    print(f"[save_result] 已儲存 → {filepath}")
    return filepath
