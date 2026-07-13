"""SQLite 持久化层（quant-state-node）

Schema 设计：
- macro_series: FRED M2 / FINRA Margin Debt 时间序列
- risk_config: 用户风控配置（YAML 通过 API 写入）
- audit_log: 配置变更审计
- skew_history: 历史 Skew 与 Z-Score（用于 Z-Score 计算的本地缓存）
- alert_log: 历史告警流水

采用 connection-per-call 模式（SQLite 推荐做法）。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS macro_series (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    series_name     TEXT NOT NULL,
    series_date     TEXT NOT NULL,
    value           REAL,
    source          TEXT NOT NULL DEFAULT 'fred',
    ingested_at     TEXT NOT NULL,
    UNIQUE(series_name, series_date)
);

CREATE INDEX IF NOT EXISTS idx_macro_series_name
    ON macro_series(series_name, series_date);

CREATE TABLE IF NOT EXISTS risk_config (
    config_key      TEXT PRIMARY KEY,
    config_value    TEXT NOT NULL,
    config_yaml     TEXT,
    updated_at      TEXT NOT NULL,
    updated_by      TEXT DEFAULT 'api'
);

CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    action          TEXT NOT NULL,
    payload         TEXT,
    actor           TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skew_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT NOT NULL,
    as_of_date      TEXT NOT NULL,
    skew_25d        REAL,
    z_score         REAL,
    z_score_5d      REAL,
    z_score_20d     REAL,
    iv_atm          REAL,
    data_quality    TEXT DEFAULT 'primary',
    signal_quality  TEXT DEFAULT 'primary',
    greeks_source   TEXT DEFAULT 'vendor',
    extra_json      TEXT,
    ingested_at     TEXT NOT NULL,
    UNIQUE(ticker, as_of_date)
);

CREATE INDEX IF NOT EXISTS idx_skew_history_ticker
    ON skew_history(ticker, as_of_date);

CREATE TABLE IF NOT EXISTS alert_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT NOT NULL,
    as_of_date      TEXT NOT NULL,
    severity        TEXT NOT NULL,
    is_alert        INTEGER NOT NULL DEFAULT 0,
    z_score         REAL,
    reasons         TEXT,
    raw_json        TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_alert_log_date
    ON alert_log(as_of_date, severity);
"""


class SqliteStore:
    """线程安全的 SQLite 包装器。

    用法::

        store = SqliteStore("data/v13_state.db")
        store.upsert_macro("M2", date(2026, 7, 1), 22000.0)
        rows = store.fetch_macro("M2", limit=365)
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.db_path,
            timeout=10.0,
            isolation_level=None,  # autocommit; we manage txns explicitly
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """手动事务上下文。"""
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    # ── 宏观序列 ─────────────────────────────────────────────────────────

    def upsert_macro(
        self,
        series_name: str,
        series_date: date,
        value: float | None,
        source: str = "fred",
    ) -> None:
        """插入或更新一条宏观序列数据点。"""
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO macro_series (series_name, series_date, value, source, ingested_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(series_name, series_date) DO UPDATE SET
                    value = excluded.value,
                    source = excluded.source,
                    ingested_at = excluded.ingested_at
                """,
                (
                    series_name,
                    series_date.isoformat(),
                    value,
                    source,
                    datetime.now().isoformat(),
                ),
            )

    def fetch_macro(
        self,
        series_name: str,
        start: date | None = None,
        end: date | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """读取宏观序列（升序）。"""
        clauses = ["series_name = ?"]
        params: list[Any] = [series_name]
        if start is not None:
            clauses.append("series_date >= ?")
            params.append(start.isoformat())
        if end is not None:
            clauses.append("series_date <= ?")
            params.append(end.isoformat())
        where = " AND ".join(clauses)
        sql = f"SELECT series_date, value, source FROM macro_series WHERE {where} ORDER BY series_date ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            {"date": r["series_date"], "value": r["value"], "source": r["source"]}
            for r in rows
        ]

    # ── Skew 历史 ────────────────────────────────────────────────────────

    def upsert_skew_snapshot(
        self,
        ticker: str,
        as_of_date: date,
        record: dict[str, Any],
    ) -> None:
        """插入或更新单日 Skew 快照（用于历史 Z-Score 计算）。"""
        extra = {
            k: v
            for k, v in record.items()
            if k
            not in {
                "ticker",
                "as_of_date",
                "skew_25d",
                "z_score",
                "z_score_5d",
                "z_score_20d",
                "iv_atm",
                "data_quality",
                "signal_quality",
                "greeks_source",
            }
        }
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO skew_history (
                    ticker, as_of_date, skew_25d, z_score,
                    z_score_5d, z_score_20d, iv_atm,
                    data_quality, signal_quality, greeks_source,
                    extra_json, ingested_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker, as_of_date) DO UPDATE SET
                    skew_25d=excluded.skew_25d,
                    z_score=excluded.z_score,
                    z_score_5d=excluded.z_score_5d,
                    z_score_20d=excluded.z_score_20d,
                    iv_atm=excluded.iv_atm,
                    data_quality=excluded.data_quality,
                    signal_quality=excluded.signal_quality,
                    greeks_source=excluded.greeks_source,
                    extra_json=excluded.extra_json,
                    ingested_at=excluded.ingested_at
                """,
                (
                    ticker,
                    as_of_date.isoformat(),
                    record.get("skew_25d"),
                    record.get("z_score"),
                    record.get("z_score_5d"),
                    record.get("z_score_20d"),
                    record.get("iv_atm"),
                    record.get("data_quality", "primary"),
                    record.get("signal_quality", "primary"),
                    record.get("greeks_source", "vendor"),
                    json.dumps(extra, ensure_ascii=False, default=str),
                    datetime.now().isoformat(),
                ),
            )

    def fetch_skew_history(
        self,
        ticker: str,
        start: date | None = None,
        end: date | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """读取 Skew 历史。"""
        clauses = ["ticker = ?"]
        params: list[Any] = [ticker]
        if start is not None:
            clauses.append("as_of_date >= ?")
            params.append(start.isoformat())
        if end is not None:
            clauses.append("as_of_date <= ?")
            params.append(end.isoformat())
        where = " AND ".join(clauses)
        sql = f"""
            SELECT as_of_date, skew_25d, z_score, z_score_5d, z_score_20d, iv_atm,
                   data_quality, signal_quality, greeks_source, extra_json
            FROM skew_history WHERE {where}
            ORDER BY as_of_date ASC
        """
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        result: list[dict[str, Any]] = []
        for r in rows:
            extra = json.loads(r["extra_json"]) if r["extra_json"] else {}
            result.append(
                {
                    "ticker": ticker,
                    "as_of_date": r["as_of_date"],
                    "skew_25d": r["skew_25d"],
                    "z_score": r["z_score"],
                    "z_score_5d": r["z_score_5d"],
                    "z_score_20d": r["z_score_20d"],
                    "iv_atm": r["iv_atm"],
                    "data_quality": r["data_quality"],
                    "signal_quality": r["signal_quality"],
                    "greeks_source": r["greeks_source"],
                    **extra,
                }
            )
        return result

    # ── 风险配置 ─────────────────────────────────────────────────────────

    def put_config(self, key: str, value: Any, yaml_text: str | None = None) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO risk_config (config_key, config_value, config_yaml, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(config_key) DO UPDATE SET
                    config_value = excluded.config_value,
                    config_yaml  = excluded.config_yaml,
                    updated_at   = excluded.updated_at
                """,
                (key, json.dumps(value, ensure_ascii=False, default=str), yaml_text, datetime.now().isoformat()),
            )
            conn.execute(
                "INSERT INTO audit_log (action, payload, actor, created_at) VALUES (?, ?, ?, ?)",
                (
                    "config_update",
                    json.dumps({"key": key}, ensure_ascii=False),
                    "api",
                    datetime.now().isoformat(),
                ),
            )

    def get_config(self, key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT config_value, config_yaml, updated_at FROM risk_config WHERE config_key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return {
            "value": json.loads(row["config_value"]),
            "yaml": row["config_yaml"],
            "updated_at": row["updated_at"],
        }

    def list_configs(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT config_key, config_yaml, updated_at FROM risk_config ORDER BY config_key"
            ).fetchall()
        return [
            {
                "key": r["config_key"],
                "yaml": r["config_yaml"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]

    # ── 审计 ─────────────────────────────────────────────────────────────

    def append_audit(self, action: str, payload: dict[str, Any], actor: str = "system") -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO audit_log (action, payload, actor, created_at) VALUES (?, ?, ?, ?)",
                (action, json.dumps(payload, ensure_ascii=False, default=str), actor, datetime.now().isoformat()),
            )

    def fetch_audit(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT action, payload, actor, created_at FROM audit_log "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "action": r["action"],
                "payload": json.loads(r["payload"]) if r["payload"] else {},
                "actor": r["actor"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    # ── 告警流水 ─────────────────────────────────────────────────────────

    def append_alert(self, record: dict[str, Any]) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO alert_log (ticker, as_of_date, severity, is_alert, z_score, reasons, raw_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["ticker"],
                    record["as_of_date"],
                    record.get("severity", "normal"),
                    1 if record.get("is_alert") else 0,
                    record.get("z_score"),
                    json.dumps(record.get("reasons", []), ensure_ascii=False),
                    json.dumps(record, ensure_ascii=False, default=str),
                    datetime.now().isoformat(),
                ),
            )

    def fetch_alerts(
        self,
        start: date | None = None,
        end: date | None = None,
        severity_min: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses = ["1=1"]
        params: list[Any] = []
        if start is not None:
            clauses.append("as_of_date >= ?")
            params.append(start.isoformat())
        if end is not None:
            clauses.append("as_of_date <= ?")
            params.append(end.isoformat())
        where = " AND ".join(clauses)
        sql = f"SELECT * FROM alert_log WHERE {where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            if severity_min is not None:
                _SEVERITY_ORDER = {"normal": 0, "watch": 1, "elevated": 2, "high": 3, "critical": 4}
                if _SEVERITY_ORDER.get(r["severity"], 0) < _SEVERITY_ORDER.get(severity_min, 0):
                    continue
            out.append(
                {
                    "ticker": r["ticker"],
                    "as_of_date": r["as_of_date"],
                    "severity": r["severity"],
                    "is_alert": bool(r["is_alert"]),
                    "z_score": r["z_score"],
                    "reasons": json.loads(r["reasons"]) if r["reasons"] else [],
                    "raw": json.loads(r["raw_json"]) if r["raw_json"] else {},
                    "created_at": r["created_at"],
                }
            )
        return out

    # ── 健康检查 ─────────────────────────────────────────────────────────

    def ping(self) -> bool:
        try:
            with self._connect() as conn:
                conn.execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False
