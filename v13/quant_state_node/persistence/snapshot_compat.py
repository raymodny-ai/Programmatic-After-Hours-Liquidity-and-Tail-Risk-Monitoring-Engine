"""v1.2.1 JSON 快照兼容层。

提供无 Redis / 无 SQLite 时也可工作的回退模式：
- ``latest_snapshot.json`` —— 单日快照（前端原 dashboard 仍可读）
- ``macro_history.json`` —— 宏观基本面历史
- ``volatility_regime_snapshot.json`` —— VIX/VXN 状态快照
- ``skipped_tickers_snapshot.json`` —— 跳过标的清单

该层让 v1.2.1 的 Web UI 在 V1.3 部署期间不会失效。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any


_DEFAULT_PATH = Path("data/processed")


class SnapshotCompat:
    """V1.2.1 快照 JSON 读写（向后兼容）。"""

    def __init__(self, base_dir: str | Path = _DEFAULT_PATH) -> None:
        self.base = Path(base_dir)
        self.base.mkdir(parents=True, exist_ok=True)

    # ── 通用读写 ─────────────────────────────────────────────────────────

    def _read(self, name: str) -> dict[str, Any] | list[Any] | None:
        p = self.base / name
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _write(self, name: str, payload: dict[str, Any] | list[Any]) -> Path:
        p = self.base / name
        tmp = p.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        tmp.replace(p)  # 原子写入
        return p

    # ── 公开方法 ─────────────────────────────────────────────────────────

    def write_latest_snapshot(self, snapshot: dict[str, Any]) -> Path:
        snapshot["updated_at"] = datetime.now().isoformat()
        snapshot.setdefault("date", date.today().isoformat())
        return self._write("latest_snapshot.json", snapshot)

    def read_latest_snapshot(self) -> dict[str, Any] | None:
        data = self._read("latest_snapshot.json")
        return data if isinstance(data, dict) else None

    def write_macro_history(self, history: dict[str, Any]) -> Path:
        return self._write("macro_history.json", history)

    def read_macro_history(self) -> dict[str, Any] | None:
        data = self._read("macro_history.json")
        return data if isinstance(data, dict) else None

    def write_volatility_regime(self, regime: dict[str, Any]) -> Path:
        return self._write("volatility_regime_snapshot.json", regime)

    def read_volatility_regime(self) -> dict[str, Any] | None:
        data = self._read("volatility_regime_snapshot.json")
        return data if isinstance(data, dict) else None

    def write_skipped_tickers(self, skipped: list[dict[str, Any]]) -> Path:
        payload = {
            "date": date.today().isoformat(),
            "skipped_tickers": skipped,
            "updated_at": datetime.now().isoformat(),
        }
        return self._write("skipped_tickers_snapshot.json", payload)

    def read_skipped_tickers(self) -> list[dict[str, Any]]:
        data = self._read("skipped_tickers_snapshot.json")
        if isinstance(data, dict):
            return data.get("skipped_tickers", [])
        return []

    # ── 一致性检查 ───────────────────────────────────────────────────────

    def ping(self) -> bool:
        try:
            return self.base.exists() and self.base.is_dir()
        except Exception:
            return False
