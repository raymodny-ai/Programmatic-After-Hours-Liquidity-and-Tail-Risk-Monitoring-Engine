"""V1.3 持久化层单元测试。

注意：Windows 平台 + SQLite WAL 模式下，临时文件的 unlink 容易失败。
我们采用 ``tmp_path`` 而不主动 unlink（pytest 自动管理）。
"""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

import pytest

from v13.quant_state_node.persistence import SnapshotCompat, SqliteStore


def test_sqlite_macro_upsert_and_fetch(tmp_path: Path):
    db = str(tmp_path / "state.db")
    s = SqliteStore(db)
    s.upsert_macro("M2", date(2026, 5, 1), 22000.0)
    s.upsert_macro("M2", date(2026, 6, 1), 22050.0)
    s.upsert_macro("FINRA_MARGIN", date(2026, 5, 25), 850.0)

    m2 = s.fetch_macro("M2", limit=10)
    assert len(m2) == 2
    assert m2[0]["date"] == "2026-05-01"
    assert m2[-1]["value"] == 22050.0

    mfin = s.fetch_macro("FINRA_MARGIN")
    assert mfin[0]["source"] == "fred"


def test_sqlite_skew_history(tmp_path: Path):
    db = str(tmp_path / "state.db")
    s = SqliteStore(db)
    record = {
        "ticker": "SPY",
        "skew_25d": 0.55,
        "z_score": 1.2,
        "z_score_5d": 0.8,
        "z_score_20d": 1.5,
        "iv_atm": 0.18,
        "data_quality": "primary",
        "signal_quality": "primary",
        "greeks_source": "vendor",
        "iv_25d_call": 0.20,
        "iv_25d_put": 0.16,
    }
    s.upsert_skew_snapshot("SPY", date(2026, 7, 10), record)

    hist = s.fetch_skew_history("SPY", limit=10)
    assert len(hist) == 1
    assert hist[0]["skew_25d"] == 0.55
    assert hist[0]["iv_25d_call"] == 0.20
    assert hist[0]["data_quality"] == "primary"


def test_sqlite_config(tmp_path: Path):
    db = str(tmp_path / "state.db")
    s = SqliteStore(db)
    s.put_config("vxn_thresholds", {"z_alert": 1.0}, yaml_text="z_alert: 1.0")

    cfg = s.get_config("vxn_thresholds")
    assert cfg is not None
    assert cfg["value"]["z_alert"] == 1.0
    assert cfg["yaml"].strip() == "z_alert: 1.0"
    assert len(s.fetch_audit(limit=10)) == 1


def test_sqlite_alerts_log(tmp_path: Path):
    db = str(tmp_path / "state.db")
    s = SqliteStore(db)
    s.append_alert(
        {
            "ticker": "VXN",
            "as_of_date": "2026-07-10",
            "severity": "critical",
            "is_alert": True,
            "z_score": 3.2,
            "reasons": ["VXN Z=3.2", "VXN-VIX Z=2.5"],
        }
    )
    s.append_alert(
        {
            "ticker": "SPY",
            "as_of_date": "2026-07-10",
            "severity": "elevated",
            "is_alert": True,
            "z_score": 2.0,
            "reasons": ["Skew Z=2.0"],
        }
    )

    high = s.fetch_alerts(severity_min="high", limit=10)
    assert len(high) == 1
    assert high[0]["ticker"] == "VXN"

    all_alerts = s.fetch_alerts(severity_min=None, limit=10)
    assert len(all_alerts) == 2


def test_snapshot_compat_atomic_write(tmp_path: Path):
    snap_dir = str(tmp_path / "processed")
    (tmp_path / "processed").mkdir()
    sc = SnapshotCompat(snap_dir)
    sc.write_latest_snapshot({"snapshots": {"SPY": {"skew_25d": 0.55}}})
    snap = sc.read_latest_snapshot()
    assert snap is not None
    assert "SPY" in snap["snapshots"]

    sc.write_skipped_tickers([{"ticker": "QQQ", "skip_reason": "no_chain"}])
    skipped = sc.read_skipped_tickers()
    assert len(skipped) == 1
    assert skipped[0]["ticker"] == "QQQ"


def test_snapshot_compat_no_tmp_leftover(tmp_path: Path):
    snap_dir = str(tmp_path / "processed")
    (tmp_path / "processed").mkdir()
    sc = SnapshotCompat(snap_dir)
    for _ in range(5):
        sc.write_latest_snapshot({"updated_at": "2026-07-10"})
    leftover = list(Path(snap_dir).glob("*.tmp"))
    assert leftover == []
