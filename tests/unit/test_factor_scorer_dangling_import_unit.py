"""`utils.universe.factor_scorer` 悬挂 import 回归 (P2-3).

背景 (2026-09-11 代码质量与系统Bug审查 §P2-3)
--------------------------------------------
`factor_scorer._compute_single_stock_factor` 仍 `from utils.vibe_trading_adapter import
get_vibe_adapter` —— 该名**从未存在** (适配器导出的是 `get_adapter`), 且 ImportError
**不在下方捕获元组内** → 运行时直接崩掉整个 worker (v84_UniverseScan 每日 exit 1)。
修复提交 `89b36045` 的 diff 只覆盖了 batch 站点, worker 站点残留至今。

附带: 适配器类上既无 `get_vibe_adapter` / `list_factors` / `compute_single_stock`,
也无动态注册 → 即使 import 修好, 调用仍 AttributeError。故一并改为**显式能力判定
+ 降级**, 而非静默 AttributeError。

修复前实测: `ImportError: cannot import name 'get_vibe_adapter'`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from utils.universe import factor_scorer as fs


def _kline(n: int = 50) -> pd.DataFrame:
    base = np.arange(float(n))
    return pd.DataFrame(
        {
            "open": base,
            "high": base,
            "low": base,
            "close": base,
            "volume": base,
        }
    )


class TestNoDanglingImport:
    def test_source_has_no_dangling_import_statement(self):
        """源码中不得再有 `import ... get_vibe_adapter` 语句 (防复发).

        只查 import 语句本身, 允注释里记录历史名称 (知识沉淀需要)。
        """
        import ast
        import pathlib

        src = pathlib.Path(fs.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported.add(alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[-1])
        assert "get_vibe_adapter" not in imported

    def test_adapter_module_exports_get_adapter(self):
        """真正存在的名字必须是 `get_adapter`."""
        from utils import vibe_trading_adapter as vta

        assert hasattr(vta, "get_adapter")
        assert not hasattr(vta, "get_vibe_adapter")


class TestGracefulDegradation:
    """修复前: 抛 ImportError (不在捕获元组内) -> worker 崩溃."""

    def test_compute_does_not_raise_importerror(self):
        result = fs._compute_single_stock_factor("600519", _kline(), ["a144"])
        assert result == ("600519", {})

    def test_compute_short_kline_returns_empty(self):
        result = fs._compute_single_stock_factor("600519", _kline(10), ["a144"])
        assert result == ("600519", {})

    def test_compute_empty_df_returns_empty(self):
        result = fs._compute_single_stock_factor("600519", pd.DataFrame(), ["a144"])
        assert result == ("600519", {})

    def test_select_factor_ids_degrades_not_raises(self):
        """适配器未实现 list_factors -> 返回空映射, 不抛 AttributeError."""
        from utils.vibe_trading_adapter import get_adapter

        out = fs.select_factor_ids(get_adapter(), fs.ScoringConfig())
        assert out == {}

    def test_capability_probe_matches_reality(self):
        """能力探针必须与适配器真实能力一致 (防止修复基于错误假设)."""
        from utils.vibe_trading_adapter import get_adapter

        adapter = get_adapter()
        assert getattr(adapter, "compute_single_stock", None) is None
        assert getattr(adapter, "list_factors", None) is None


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
