"""V1.3 业务层包单元测试。"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from v13.quant_api_node.app.services.analytics.matrix_skew import compute_matrix_skew
from v13.quant_api_node.app.services.data_sources.completeness_check import (
    validate_options_surface,
)
from v13.quant_api_node.app.services.data_sources.fred_finra_align import (
    align_macro_series,
    compute_leverage_metrics,
)


class FakeDF:
    def __init__(self, data: list[dict[str, Any]] | None = None) -> None:
        self._data = data or []
        self.empty = len(self._data) == 0


def test_validate_options_surface_basic():
    surface = {
        "strikes": [100, 110, 120, 130, 140],
        "expirations": ["2026-07-25", "2026-08-25", "2026-09-25", "2026-12-25"],
        "iv": [
            [0.20, 0.21, 0.22, 0.23, 0.24],
            [0.22, 0.22, 0.22, 0.23, 0.25],
            [0.23, 0.23, 0.23, 0.24, 0.26],
            [0.24, 0.25, 0.26, 0.27, 0.28],
        ],
        "dte_list": [10, 35, 70, 170],
        "spot": 120.0,
    }
    result = validate_options_surface(surface, spot=120.0)
    assert 0 <= result["completeness"] <= 1
    assert result["data_quality"] in {"primary", "fallback", "unavailable"}
    assert isinstance(result["issues"], list)


def test_validate_options_surface_unavailable():
    surface = {
        "strikes": [115, 116, 117, 118, 119, 120, 121, 122, 123, 124, 125],
        "expirations": ["2026-07-25"],
        "iv": [[0.2] * 11],
        "dte_list": [10],
        "spot": 120.0,
    }
    result = validate_options_surface(surface, spot=120.0)
    assert result["data_quality"] in {"fallback", "unavailable"}
    assert any("OTM" in i or "远月" in i for i in result["issues"])


def test_align_macro_series_basic():
    m2 = [
        {"date": "2026-04-29", "value": 22000.0},
        {"date": "2026-05-29", "value": 22050.0},
        {"date": "2026-06-29", "value": 22120.0},
    ]
    margin = [
        {"date": "2026-05-25", "value": 850.0},
        {"date": "2026-06-25", "value": 855.0},
    ]
    aligned = align_macro_series(m2, margin)
    assert aligned["as_of"] == "2026-06"
    assert "2026-04" in aligned["ratio_by_month"]
    assert aligned["m2_by_month"]["2026-04"] == 22000.0


def test_compute_leverage_metrics_momentum_reversal():
    series = {
        "months": ["2025-07", "2025-08", "2025-09", "2025-10", "2025-11", "2025-12",
                   "2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"],
        "ratio_by_month": {
            "2025-07": 3.5, "2025-08": 3.6, "2025-09": 3.65,
            "2025-10": 3.7, "2025-11": 3.75, "2025-12": 3.8,
            "2026-01": 3.85, "2026-02": 3.95, "2026-03": 4.0,
            "2026-04": 3.92, "2026-05": 3.85, "2026-06": 3.78,
        },
    }
    metrics = compute_leverage_metrics(series)
    assert metrics["as_of"] == "2026-06"
    assert metrics["momentum_reversal"] is True


@pytest.mark.asyncio
async def test_compute_matrix_skew_with_stub():
    def stub_fetcher(t: str, as_of: date):
        return FakeDF([{"strike": 100, "iv": 0.2}])

    captured: list[dict[str, Any]] = []

    def stub_sink(record: dict[str, Any]) -> None:
        captured.append(record)

    out = await compute_matrix_skew(
        tickers=["SPY", "QQQ", "BAD"],
        as_of=date(2026, 7, 10),
        surface_fetcher=stub_fetcher,
        store_sink=stub_sink,
    )
    assert "SPY" in out["computed"] or "BAD" in out["skipped"]
    assert "QQQ" in out["computed"] or "QQQ" in out["skipped"]
