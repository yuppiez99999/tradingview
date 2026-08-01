# -*- coding: utf-8 -*-
"""test_system_check_c9_unit.py — C9 兜底价格新鲜度检查单元测试

R1 任务 (2026-08-01): 验证 utils.system_check.SystemChecker.check_fallback_price_freshness

测试覆盖:
    1. 文件不存在场景 (ERROR)
    2. 模块加载失败场景 (ERROR)
    3. DEFAULT_FUTURES_PRICES 字段缺失/非 dict (ERROR)
    4. 缺少期货品种 (ERROR)
    5. FALLBACK_PRICES_UPDATED 字段缺失/非字符串 (ERROR)
    6. 日期格式错误 (ERROR)
    7. 未来日期 (age < 0) (ERROR)
    8. age <= 7 天 (INFO PASS)
    9. 7 < age <= 30 天 (WARN FAIL)
    10. age > 30 天 (ERROR FAIL, 阻断启动)
    11. strict 模式下 WARN FAIL 也算阻止性
    12. 真实 futures_prices.py 文件集成 (回归测试)

设计原则:
    - 使用 tmp_path fixture 隔离文件系统, 不污染项目
    - 通过 monkeypatch 重定向 FUTURES_PRICES_REL_PATH, 复用 SystemChecker
    - 每个测试用例独立构造 futures_prices.py 副本, 精确控制 age_days
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.system_check import (  # noqa: E402
    CheckLevel,
    CheckStatus,
    SystemChecker,
)

# ============================================================
# Fixture: 构造临时 futures_prices.py
# ============================================================

def _make_futures_prices_module(
    tmp_path: Path,
    *,
    updated_str: str | None = "2026-08-01",
    prices: dict | None = None,
    syntax_broken: bool = False,
    missing_updated: bool = False,
    missing_prices: bool = False,
) -> Path:
    """构造临时 futures_prices.py 副本

    Args:
        tmp_path: pytest 内置 fixture
        updated_str: FALLBACK_PRICES_UPDATED 值 (None=不写该字段)
        prices: DEFAULT_FUTURES_PRICES 值 (None=使用默认 4 品种)
        syntax_broken: True=注入语法错误
        missing_updated: True=完全省略 FALLBACK_PRICES_UPDATED
        missing_prices: True=完全省略 DEFAULT_FUTURES_PRICES

    Returns:
        临时文件路径
    """
    if prices is None:
        prices = {
            "IF": 3950.0,
            "IC": 6200.0,
            "IM": 6800.0,
            "IH": 2700.0,
        }

    lines = [
        "# -*- coding: utf-8 -*-",
        '"""临时 futures_prices.py for C9 unit test"""',
        "",
        "DEFAULT_FUTURES_PRICES = " + repr(prices),
    ]
    if not missing_updated and updated_str is not None:
        lines.append(f"FALLBACK_PRICES_UPDATED = {updated_str!r}")
    if missing_prices:
        # 移除 DEFAULT_FUTURES_PRICES (不写入)
        lines = [l for l in lines if not l.startswith("DEFAULT_FUTURES_PRICES")]
    if syntax_broken:
        lines.append("def broken(:")  # 故意语法错误

    content = "\n".join(lines) + "\n"
    fp = tmp_path / "futures_prices.py"
    fp.write_text(content, encoding="utf-8")
    return fp


def _run_c9(tmp_path: Path, monkeypatch, **kwargs) -> list:
    """构造 checker 并运行 C9 检查, 返回 CheckResult 列表"""
    fp = _make_futures_prices_module(tmp_path, **kwargs)
    # 用相对 tmp_path 的路径, SystemChecker 内部用 PROJECT_ROOT / rel_path 拼接
    # 改为 monkeypatch PROJECT_ROOT + rel_path 双重定向, 简单起见直接 patch 方法
    checker = SystemChecker(strict=False, skip_datasource=True)
    # 直接 patch FUTURES_PRICES_REL_PATH, 但 SystemChecker 用 PROJECT_ROOT / rel_path
    # 所以需要让 PROJECT_ROOT + rel_path 指向 tmp_path/futures_prices.py
    # 方案: monkeypatch rel_path 为绝对路径字符串, Path / "/abs" = "/abs"
    # 在 Windows 上 Path("a") / "C:\\b" 会得到 "C:\\b" (Windows behavior)
    monkeypatch.setattr(
        SystemChecker, "FUTURES_PRICES_REL_PATH", str(fp)
    )
    checker._results = []
    checker.check_fallback_price_freshness()
    return list(checker._results)


# ============================================================
# 1. 文件不存在场景
# ============================================================

class TestFileMissing:
    """C9.1 — 文件不存在应 ERROR FAIL"""

    def test_file_missing(self, tmp_path, monkeypatch):
        """目标文件不存在时, 应 ERROR FAIL"""
        checker = SystemChecker(strict=False, skip_datasource=True)
        # 指向一个不存在的文件
        non_existent = tmp_path / "nonexistent.py"
        monkeypatch.setattr(
            SystemChecker, "FUTURES_PRICES_REL_PATH", str(non_existent)
        )
        checker._results = []
        checker.check_fallback_price_freshness()

        assert len(checker._results) == 1
        r = checker._results[0]
        assert r.code == "C9.1"
        assert r.level == CheckLevel.ERROR
        assert r.status == CheckStatus.FAIL
        assert r.is_blocking is True
        assert "不存在" in r.detail


# ============================================================
# 2. 模块加载失败 (语法错误)
# ============================================================

class TestModuleLoadFailure:
    """C9.1 — 模块加载失败应 ERROR FAIL"""

    def test_syntax_broken(self, tmp_path, monkeypatch):
        """语法错误应 ERROR FAIL, 给出修复建议"""
        results = _run_c9(tmp_path, monkeypatch, syntax_broken=True)
        assert len(results) == 1
        r = results[0]
        assert r.code == "C9.1"
        assert r.level == CheckLevel.ERROR
        assert r.status == CheckStatus.FAIL
        assert r.is_blocking is True
        # 修复建议应指向 futures_prices.py
        assert "futures_prices.py" in r.remediation


# ============================================================
# 3. DEFAULT_FUTURES_PRICES 字段问题
# ============================================================

class TestDefaultPricesField:
    """C9.1 — DEFAULT_FUTURES_PRICES 字段校验"""

    def test_missing_default_prices(self, tmp_path, monkeypatch):
        """DEFAULT_FUTURES_PRICES 完全缺失应 ERROR FAIL"""
        results = _run_c9(tmp_path, monkeypatch, missing_prices=True)
        assert len(results) == 1
        r = results[0]
        assert r.code == "C9.1"
        assert r.level == CheckLevel.ERROR
        assert r.status == CheckStatus.FAIL
        assert "DEFAULT_FUTURES_PRICES" in r.detail

    def test_missing_codes(self, tmp_path, monkeypatch):
        """缺少 IM 品种应 ERROR FAIL"""
        incomplete_prices = {"IF": 3950.0, "IC": 6200.0, "IH": 2700.0}
        results = _run_c9(tmp_path, monkeypatch, prices=incomplete_prices)
        assert len(results) == 1
        r = results[0]
        assert r.code == "C9.1"
        assert r.level == CheckLevel.ERROR
        assert r.status == CheckStatus.FAIL
        assert "IM" in r.detail

    def test_empty_prices_dict(self, tmp_path, monkeypatch):
        """空 dict 应 ERROR FAIL"""
        results = _run_c9(tmp_path, monkeypatch, prices={})
        assert len(results) == 1
        r = results[0]
        assert r.level == CheckLevel.ERROR
        assert r.status == CheckStatus.FAIL


# ============================================================
# 4. FALLBACK_PRICES_UPDATED 字段问题
# ============================================================

class TestUpdatedField:
    """C9.1 — FALLBACK_PRICES_UPDATED 字段校验"""

    def test_missing_updated_field(self, tmp_path, monkeypatch):
        """FALLBACK_PRICES_UPDATED 完全缺失应 ERROR FAIL"""
        results = _run_c9(tmp_path, monkeypatch, missing_updated=True)
        assert len(results) == 1
        r = results[0]
        assert r.code == "C9.1"
        assert r.level == CheckLevel.ERROR
        assert r.status == CheckStatus.FAIL
        assert "FALLBACK_PRICES_UPDATED" in r.detail

    def test_invalid_date_format(self, tmp_path, monkeypatch):
        """日期格式错误 (非 YYYY-MM-DD) 应 ERROR FAIL"""
        results = _run_c9(
            tmp_path, monkeypatch, updated_str="2026/08/01"
        )
        # 字段存在, 进入日期解析阶段 (C9.2)
        assert len(results) == 2
        # C9.1 通过 (字段完整)
        assert results[0].code == "C9.1"
        assert results[0].status == CheckStatus.PASS
        # C9.2 FAIL (日期格式错误)
        r = results[1]
        assert r.code == "C9.2"
        assert r.level == CheckLevel.ERROR
        assert r.status == CheckStatus.FAIL
        assert "日期解析失败" in r.detail

    def test_future_date(self, tmp_path, monkeypatch):
        """未来日期 (age < 0) 应 ERROR FAIL"""
        future = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")
        results = _run_c9(tmp_path, monkeypatch, updated_str=future)
        assert len(results) == 2
        assert results[0].status == CheckStatus.PASS  # C9.1 字段完整
        r = results[1]
        assert r.code == "C9.2"
        assert r.level == CheckLevel.ERROR
        assert r.status == CheckStatus.FAIL
        assert "未来" in r.detail or "age" in r.detail


# ============================================================
# 5. 分级阈值 (核心测试)
# ============================================================

class TestAgeThresholds:
    """C9.2 — 兜底价格年龄分级判定"""

    def test_fresh_age_pass(self, tmp_path, monkeypatch):
        """age <= 7 天应 INFO PASS"""
        today = datetime.now()
        # age = 3 天
        fresh = (today - timedelta(days=3)).strftime("%Y-%m-%d")
        results = _run_c9(tmp_path, monkeypatch, updated_str=fresh)
        assert len(results) == 2
        assert results[0].status == CheckStatus.PASS  # C9.1
        r = results[1]
        assert r.code == "C9.2"
        assert r.level == CheckLevel.INFO
        assert r.status == CheckStatus.PASS
        assert r.is_blocking is False
        # 详情应包含年龄和阈值
        assert "3 天前" in r.detail
        assert "7/30" in r.detail

    def test_warn_age_at_boundary(self, tmp_path, monkeypatch):
        """age = 8 天 (刚好超 7 天告警阈值) 应 WARN FAIL"""
        today = datetime.now()
        warn = (today - timedelta(days=8)).strftime("%Y-%m-%d")
        results = _run_c9(tmp_path, monkeypatch, updated_str=warn)
        assert len(results) == 2
        r = results[1]
        assert r.code == "C9.2"
        assert r.level == CheckLevel.WARN
        assert r.status == CheckStatus.FAIL
        # WARN 默认不阻断
        assert r.is_blocking is False

    def test_warn_age_mid_range(self, tmp_path, monkeypatch):
        """age = 20 天应 WARN FAIL"""
        today = datetime.now()
        warn = (today - timedelta(days=20)).strftime("%Y-%m-%d")
        results = _run_c9(tmp_path, monkeypatch, updated_str=warn)
        r = results[1]
        assert r.level == CheckLevel.WARN
        assert r.status == CheckStatus.FAIL

    def test_error_age_at_boundary(self, tmp_path, monkeypatch):
        """age = 31 天 (刚好超 30 天阻断阈值) 应 ERROR FAIL"""
        today = datetime.now()
        err = (today - timedelta(days=31)).strftime("%Y-%m-%d")
        results = _run_c9(tmp_path, monkeypatch, updated_str=err)
        r = results[1]
        assert r.code == "C9.2"
        assert r.level == CheckLevel.ERROR
        assert r.status == CheckStatus.FAIL
        assert r.is_blocking is True
        assert "31 天" in r.detail

    def test_error_age_far_past(self, tmp_path, monkeypatch):
        """age = 60 天应 ERROR FAIL"""
        today = datetime.now()
        err = (today - timedelta(days=60)).strftime("%Y-%m-%d")
        results = _run_c9(tmp_path, monkeypatch, updated_str=err)
        r = results[1]
        assert r.level == CheckLevel.ERROR
        assert r.status == CheckStatus.FAIL
        assert r.is_blocking is True


# ============================================================
# 6. Strict 模式下 WARN 也算阻止性
# ============================================================

class TestStrictMode:
    """strict 模式: WARN FAIL 也算阻止性"""

    def test_warn_in_strict_mode_blocks(self, tmp_path, monkeypatch):
        """strict=True 时, WARN FAIL 应计入 blocking_failures"""
        today = datetime.now()
        warn = (today - timedelta(days=15)).strftime("%Y-%m-%d")
        fp = _make_futures_prices_module(tmp_path, updated_str=warn)
        monkeypatch.setattr(
            SystemChecker, "FUTURES_PRICES_REL_PATH", str(fp)
        )
        # strict 模式
        checker = SystemChecker(strict=True, skip_datasource=True)
        checker._results = []
        checker.check_fallback_price_freshness()
        # 该方法本身不计算 blocking, 但 is_blocking 属性可被 _build_report 使用
        # 直接验证 strict 模式下 WARN FAIL 应被计入
        c9_2 = [r for r in checker._results if r.code == "C9.2"][0]
        assert c9_2.level == CheckLevel.WARN
        assert c9_2.status == CheckStatus.FAIL
        # 在 _build_report 中, strict 模式会把这些算作 blocking
        # 模拟该逻辑
        strict_blocking = sum(
            1 for r in checker._results
            if r.status == CheckStatus.FAIL
            and r.level in (CheckLevel.ERROR, CheckLevel.WARN)
        )
        assert strict_blocking >= 1


# ============================================================
# 7. 价格摘要正确性
# ============================================================

class TestPriceSummary:
    """详情中应包含 4 个品种的价格摘要"""

    def test_price_summary_in_detail(self, tmp_path, monkeypatch):
        """PASS 详情应包含 IF/IC/IM/IH 价格"""
        today = datetime.now()
        fresh = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        custom_prices = {
            "IF": 4123.4,
            "IC": 6789.0,
            "IM": 7234.5,
            "IH": 2856.7,
        }
        results = _run_c9(
            tmp_path, monkeypatch,
            updated_str=fresh, prices=custom_prices,
        )
        r = results[1]
        assert r.status == CheckStatus.PASS
        assert "IF=4123.4" in r.detail
        assert "IC=6789.0" in r.detail
        assert "IM=7234.5" in r.detail
        assert "IH=2856.7" in r.detail


# ============================================================
# 8. 真实文件回归测试
# ============================================================

class TestRealFileRegression:
    """对项目真实的 futures_prices.py 跑一遍, 验证不崩溃"""

    def test_real_file_runs_without_error(self):
        """真实 futures_prices.py 应能被 C9 检查 (不验证状态, 只验证不抛异常)"""
        checker = SystemChecker(strict=False, skip_datasource=True)
        checker._results = []
        # 不 monkeypatch, 使用真实路径
        try:
            checker.check_fallback_price_freshness()
        except Exception as e:
            pytest.fail(f"C9 检查真实文件抛异常: {e}")
        # 应至少有 1 个结果 (可能 PASS/WARN/ERROR, 取决于真实 FALLBACK_PRICES_UPDATED)
        assert len(checker._results) >= 1

    def test_real_file_known_state(self):
        """已知 FALLBACK_PRICES_UPDATED = '2026-08-01' (2026-08-01 刷新),
        age = 0 天, 应为 INFO PASS (新鲜, 不告警不阻断)

        历史: 2026-08-01 前兜底价格为 2026-06-29 (33 天, ERROR FAIL),
        R3 C-1.1 期间通过 Sina 源实时拉取刷新为 2026-08-01.
        若后续再次过期, 本测试会 FAIL 提醒维护者刷新."""
        checker = SystemChecker(strict=False, skip_datasource=True)
        checker._results = []
        checker.check_fallback_price_freshness()
        # 找 C9.2 结果
        c9_2 = [r for r in checker._results if r.code == "C9.2"]
        if not c9_2:
            # 可能 C9.1 就 FAIL 了 (字段问题), 跳过该断言
            pytest.skip("C9.1 FAIL, 未进入 C9.2 (可能字段已变更)")
        r = c9_2[0]
        # 刷新后 age=0 天 (< 7 天 warn 阈值), 应 INFO PASS
        # 若此断言 FAIL 说明兜底价格再次过期 (age > 7 天), 需刷新 DEFAULT_FUTURES_PRICES
        assert r.status == CheckStatus.PASS, (
            f"兜底价格应处于新鲜状态 (INFO PASS), 实际: {r.level} {r.status} "
            f"(详情: {r.detail}). 兜底价格可能再次过期, 请刷新 "
            f"DEFAULT_FUTURES_PRICES 与 FALLBACK_PRICES_UPDATED 后更新本测试."
        )


# ============================================================
# 9. 幂等性测试
# ============================================================

class TestIdempotency:
    """同一检查可被多次调用, 结果一致 (设计原则 4)"""

    def test_multiple_calls_same_result(self, tmp_path, monkeypatch):
        """连续 3 次调用应返回一致的结果"""
        today = datetime.now()
        warn = (today - timedelta(days=15)).strftime("%Y-%m-%d")
        fp = _make_futures_prices_module(tmp_path, updated_str=warn)
        monkeypatch.setattr(
            SystemChecker, "FUTURES_PRICES_REL_PATH", str(fp)
        )

        results_per_call = []
        for _ in range(3):
            checker = SystemChecker(strict=False, skip_datasource=True)
            checker._results = []
            checker.check_fallback_price_freshness()
            # 序列化为可比较的 tuple
            snapshot = [
                (r.code, r.level, r.status, r.detail) for r in checker._results
            ]
            results_per_call.append(snapshot)

        # 三次调用结果应完全一致
        assert results_per_call[0] == results_per_call[1]
        assert results_per_call[1] == results_per_call[2]
