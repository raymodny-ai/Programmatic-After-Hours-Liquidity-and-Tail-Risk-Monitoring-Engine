"""
DataWriter 幂等去重与 source-priority 测试 (v1.2.1)

测试范围:
    1. 同日同标的双写入去重
    2. Polygon 与 yfinance 同日冲突时保留 Polygon
    3. 空快照跳过写入
    4. 缺少主键列时抛出异常
"""

import tempfile
from pathlib import Path

import pandas as pd
import pytest

from src.data_ingestion.data_writer import DataWriter


class TestSaveMasterSnapshotDedup:
    """主快照幂等去重测试。"""

    @pytest.fixture
    def writer_and_dir(self):
        """创建临时目录的 DataWriter。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            writer = DataWriter(base_dir=tmp_path / "raw")
            writer.processed_dir = tmp_path / "processed"
            writer.processed_dir.mkdir(parents=True, exist_ok=True)
            yield writer

    def test_same_date_ticker_dedup(self, writer_and_dir):
        """同 (date, ticker) 双写入应只保留一行。"""
        writer = writer_and_dir

        df1 = pd.DataFrame([{
            "date": "2026-07-10",
            "ticker": "SPY",
            "skew_spread": 0.05,
            "data_source": "polygon",
        }])

        df2 = pd.DataFrame([{
            "date": "2026-07-10",
            "ticker": "SPY",
            "skew_spread": 0.06,
            "data_source": "polygon",
        }])

        writer.save_master_snapshot(df1)
        writer.save_master_snapshot(df2)

        result = writer.load_master_snapshot()
        spy_rows = result[result["ticker"] == "SPY"]

        assert len(spy_rows) == 1, f"应只有 1 行，实际 {len(spy_rows)}"
        # keep="last" 应保留最后写入的值
        assert spy_rows["skew_spread"].iloc[0] == 0.06

    def test_polygon_has_priority_over_yfinance(self, writer_and_dir):
        """Polygon 数据源优先于 yfinance。"""
        writer = writer_and_dir

        fallback = pd.DataFrame([{
            "date": "2026-07-10",
            "ticker": "SPY",
            "skew_spread": 0.12,
            "data_source": "yfinance",
        }])

        primary = pd.DataFrame([{
            "date": "2026-07-10",
            "ticker": "SPY",
            "skew_spread": 0.08,
            "data_source": "polygon",
        }])

        # 先写 fallback，后写 primary
        writer.save_master_snapshot(fallback)
        writer.save_master_snapshot(primary)

        result = writer.load_master_snapshot()
        spy = result[result["ticker"] == "SPY"]

        assert len(result) == 1
        assert spy["data_source"].iloc[0] == "polygon"
        assert spy["skew_spread"].iloc[0] == 0.08

    def test_yfinance_after_polygon_still_keeps_polygon(self, writer_and_dir):
        """yfinance 写入在 Polygon 之后时，应仍保留 Polygon。"""
        writer = writer_and_dir

        primary = pd.DataFrame([{
            "date": "2026-07-10",
            "ticker": "SPY",
            "skew_spread": 0.08,
            "data_source": "polygon",
        }])

        fallback = pd.DataFrame([{
            "date": "2026-07-10",
            "ticker": "SPY",
            "skew_spread": 0.15,
            "data_source": "yfinance",
        }])

        writer.save_master_snapshot(primary)
        writer.save_master_snapshot(fallback)

        result = writer.load_master_snapshot()
        spy = result[result["ticker"] == "SPY"]

        assert len(result) == 1
        assert spy["data_source"].iloc[0] == "polygon", (
            f"应为 polygon，实际 {spy['data_source'].iloc[0]}"
        )

    def test_empty_snapshot_skipped(self, writer_and_dir):
        """空快照应被跳过。"""
        writer = writer_and_dir

        path = writer.save_master_snapshot(pd.DataFrame())
        assert not path.exists()  # 空快照不应写入文件

        # 再次读取应返回空
        result = writer.load_master_snapshot()
        assert result.empty

    def test_missing_date_column_raises(self, writer_and_dir):
        """缺少 date 列时应抛出 ValueError。"""
        writer = writer_and_dir

        with pytest.raises(ValueError, match="主快照缺少主键列"):
            writer.save_master_snapshot(pd.DataFrame({
                "ticker": ["SPY"],
                "skew_spread": [0.05],
            }))

    def test_multiple_tickers_dedup(self, writer_and_dir):
        """多标的共存且各自独立去重。"""
        writer = writer_and_dir

        df1 = pd.DataFrame([
            {"date": "2026-07-10", "ticker": "SPY", "skew_spread": 0.05, "data_source": "polygon"},
            {"date": "2026-07-10", "ticker": "QQQ", "skew_spread": 0.07, "data_source": "polygon"},
        ])

        df2 = pd.DataFrame([
            {"date": "2026-07-10", "ticker": "SPY", "skew_spread": 0.06, "data_source": "polygon"},
            {"date": "2026-07-10", "ticker": "IWM", "skew_spread": 0.04, "data_source": "yfinance"},
        ])

        writer.save_master_snapshot(df1)
        writer.save_master_snapshot(df2)

        result = writer.load_master_snapshot()
        assert len(result) == 3  # SPY(updated) + QQQ + IWM

        spy = result[result["ticker"] == "SPY"]
        assert spy["skew_spread"].iloc[0] == 0.06

        # IWM 使用 yfinance
        iwm = result[result["ticker"] == "IWM"]
        assert iwm["data_source"].iloc[0] == "yfinance"

    def test_different_dates_preserved(self, writer_and_dir):
        """不同日期的数据应全部保留。"""
        writer = writer_and_dir

        df = pd.DataFrame([
            {"date": "2026-07-08", "ticker": "SPY", "skew_spread": 0.05, "data_source": "polygon"},
            {"date": "2026-07-09", "ticker": "SPY", "skew_spread": 0.06, "data_source": "polygon"},
            {"date": "2026-07-10", "ticker": "SPY", "skew_spread": 0.07, "data_source": "polygon"},
        ])

        writer.save_master_snapshot(df)

        result = writer.load_master_snapshot()
        spy = result[result["ticker"] == "SPY"]
        assert len(spy) == 3
        assert list(spy["skew_spread"]) == [0.05, 0.06, 0.07]
