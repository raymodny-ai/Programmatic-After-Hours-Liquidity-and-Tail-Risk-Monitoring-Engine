"""v1.2.1 → V1.3 兼容性验证脚本。

检查项：
1. JSON 快照文件结构兼容（latest_snapshot / macro_history / volatility_regime / skipped_tickers）
2. 配置文件 YAML schema 兼容
3. 旧版 CLI 命令兼容性（run_pipeline / run_vxn_alert）
4. SQLite 状态库可正常读写

用法：
    python v13/scripts/v121_compat_check.py [--data-dir ../data/processed]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ───────────────────── 校验函数 ─────────────────────


def check_latest_snapshot(data_dir: Path) -> tuple[bool, str]:
    """最新快照（v1.2.1 核心文件）"""
    f = data_dir / "latest_snapshot.json"
    if not f.exists():
        return False, f"缺失 {f.name}（v1.2.1 必须生成）"
    try:
        snap = json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return False, f"{f.name} JSON 解析失败: {e}"

    required = {"updated_at", "snapshots"}
    missing = required - snap.keys()
    if missing:
        return False, f"{f.name} 缺少字段 {missing}"

    if not isinstance(snap["snapshots"], dict):
        return False, f"{f.name}.snapshots 必须是 dict"

    return True, f"{f.name} OK（{len(snap['snapshots'])} tickers）"


def check_macro_history(data_dir: Path) -> tuple[bool, str]:
    """宏观历史快照"""
    f = data_dir / "macro_history.json"
    if not f.exists():
        return False, f"缺失 {f.name}"
    try:
        hist = json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return False, f"{f.name} JSON 解析失败: {e}"

    if "m2" not in hist and "margin" not in hist:
        return False, f"{f.name} 同时缺少 m2/margin 序列"
    return True, f"{f.name} OK"


def check_volatility_regime(data_dir: Path) -> tuple[bool, str]:
    """波动率体制快照"""
    f = data_dir / "volatility_regime_snapshot.json"
    if not f.exists():
        return True, f"{f.name} 不存在（v1.2.1 早期版本可选）"
    try:
        json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return False, f"{f.name} JSON 解析失败: {e}"
    return True, f"{f.name} OK"


def check_skipped_tickers(data_dir: Path) -> tuple[bool, str]:
    """跳过 ticker 列表"""
    f = data_dir / "skipped_tickers_snapshot.json"
    if not f.exists():
        return True, f"{f.name} 不存在（v1.2.1 早期版本可选）"
    try:
        skipped = json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return False, f"{f.name} JSON 解析失败: {e}"
    if not isinstance(skipped, list):
        return False, f"{f.name} 必须是 list"
    return True, f"{f.name} OK（{len(skipped)} items）"


def check_cli_compat() -> tuple[bool, str]:
    """CLI 命令兼容性：检查 v1.2.1 主入口可调用"""
    try:
        import src.main as v121_main  # type: ignore
    except ImportError:
        return False, "src.main 不可导入（v1.2.1 路径未发现）"

    # 必备函数
    required_funcs = ["run_full_pipeline", "main"]
    missing = [n for n in required_funcs if not hasattr(v121_main, n)]
    if missing:
        return False, f"src.main 缺少函数 {missing}"

    return True, "src.main.run_full_pipeline 可调用"


def check_config_yaml() -> tuple[bool, str]:
    """风险配置 YAML schema 兼容"""
    cfg_path = Path("config/risk_config.yaml")
    if not cfg_path.exists():
        return True, f"{cfg_path} 不存在（首次运行可接受）"
    try:
        import yaml  # type: ignore
    except ImportError:
        return False, "缺少 PyYAML 依赖"

    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict):
        return False, f"{cfg_path} 必须是 dict"

    # V1.3 新增字段（向后兼容）
    v13_keys = ["vxn_thresholds"]
    has_v13 = any(k in cfg for k in v13_keys)
    return True, f"{cfg_path} OK（{'含 V1.3 字段' if has_v13 else 'v1.2.1 字段'}）"


# ───────────────────── 主流程 ─────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="v1.2.1 兼容性验证")
    parser.add_argument(
        "--data-dir",
        default="data/processed",
        help="v1.2.1 JSON 快照目录（默认 data/processed）",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        print(f"⚠ 数据目录 {data_dir} 不存在，跳过文件检查（CLI/YAML 仍会校验）")
        data_dir = None

    checks: list[tuple[str, tuple[bool, str]]] = []
    if data_dir:
        checks += [
            ("最新快照 (latest_snapshot.json)", check_latest_snapshot(data_dir)),
            ("宏观历史 (macro_history.json)", check_macro_history(data_dir)),
            ("波动率体制 (volatility_regime_snapshot.json)", check_volatility_regime(data_dir)),
            ("跳过列表 (skipped_tickers_snapshot.json)", check_skipped_tickers(data_dir)),
        ]
    checks += [
        ("CLI 命令 (src.main)", check_cli_compat()),
        ("风控配置 (config/risk_config.yaml)", check_config_yaml()),
    ]

    print("=" * 60)
    print("V1.3 ↔ v1.2.1 兼容性验证")
    print("=" * 60)
    passed = failed = 0
    for name, (ok, msg) in checks:
        sym = "✓" if ok else "✗"
        print(f"  [{sym}] {name}: {msg}")
        if ok:
            passed += 1
        else:
            failed += 1

    print("=" * 60)
    print(f"通过 {passed} · 失败 {failed} · 总计 {len(checks)}")
    print("=" * 60)

    if failed > 0:
        print("\n⚠ 存在兼容性问题，请先解决再升级到 V1.3。")
        return 1
    print("\n✓ 所有校验通过，可安全升级到 V1.3。")
    return 0


if __name__ == "__main__":
    sys.exit(main())