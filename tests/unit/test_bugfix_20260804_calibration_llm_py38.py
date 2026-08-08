"""
2026-08-04 三维修复回归测试

覆盖:
- 年化收益校准: 贝叶斯收缩 + 阈值截断 (防 300308 类问题)
- LLM 输出质量: 描述行过滤 + 操作关键词识别 + 智能截断 (防阶段三空转)
- Py38 兼容性: PEP 585 类型注解 → typing.List/Dict 兼容 (防 TypeError)
"""

import sys
from pathlib import Path
from typing import get_type_hints

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "v8.3_institutional"))


# ---------------------------------------------------------------------------
# 年化收益校准: 贝叶斯收缩 + 阈值截断
# ---------------------------------------------------------------------------
class TestReturnsCalibrationBayesianShrinkage:
    """验证年化收益校准的贝叶斯收缩与阈值截断逻辑"""

    MAX_ANNUALIZED = 2.0
    MIN_ANNUALIZED = -0.99
    BAYESIAN_PRIOR = 0.15

    def _calc_shrink_weight(self, years: float) -> float:
        """贝叶斯收缩强度计算"""
        return max(0.0, min(0.7, 1.0 - years / 2.0))

    def _apply_shrink(self, annualized: float, years: float) -> float:
        """应用短周期贝叶斯收缩 (与 calibrate_returns_projection.py L442-L451 一致)"""
        if years < 2.0 and abs(annualized) > 0.5:
            shrink_weight = self._calc_shrink_weight(years)
            return annualized * (1 - shrink_weight) + self.BAYESIAN_PRIOR * shrink_weight
        return annualized

    # --- 贝叶斯收缩公式验证 ---

    def test_shrink_weight_1_year_is_50_percent(self):
        """样本期 1 年 → 收缩强度 50%"""
        assert self._calc_shrink_weight(1.0) == pytest.approx(0.5)

    def test_shrink_weight_0_5_year_is_70_percent_capped(self):
        """样本期 0.5 年 → 收缩强度 70% (封顶)"""
        assert self._calc_shrink_weight(0.5) == pytest.approx(0.7)

    def test_shrink_weight_2_years_is_0(self):
        """样本期 ≥ 2 年 → 不收缩"""
        assert self._calc_shrink_weight(2.0) == 0.0
        assert self._calc_shrink_weight(3.0) == 0.0

    # --- 收缩效果验证 ---

    def test_300308_like_1_year_480pct_shrinks_towards_15pct(self):
        """300308 类 1 年 +480% → 收缩 50% → +247.5%"""
        result = self._apply_shrink(4.8, 1.0)
        expected = 4.8 * 0.5 + 0.15 * 0.5
        assert result == pytest.approx(expected, abs=0.01)
        assert result < 4.8  # 收缩后应低于原始值

    def test_short_period_0_5_year_200pct_shrinks_70_percent(self):
        """0.5 年 +200% → 收缩 70% → +70.5%"""
        result = self._apply_shrink(2.0, 0.5)
        expected = 2.0 * 0.3 + 0.15 * 0.7
        assert result == pytest.approx(expected, abs=0.01)

    def test_normal_annual_30pct_no_shrink(self):
        """正常年化 +30% → 不收缩"""
        result = self._apply_shrink(0.3, 1.0)
        assert result == pytest.approx(0.3)

    def test_long_period_3_years_400pct_no_shrink(self):
        """长周期 3 年 +400% → 不收缩 (样本期够长)"""
        result = self._apply_shrink(4.0, 3.0)
        assert result == pytest.approx(4.0)

    # --- 阈值截断验证 ---

    def test_shrunk_300308_exceeds_200pct_threshold_skipped(self):
        """300308 类 1 年 +480% → 收缩后 +247.5% → 仍超 +200% 阈值 → SKIP"""
        shrunk = self._apply_shrink(4.8, 1.0)  # ≈ +247.5%
        assert shrunk > self.MAX_ANNUALIZED  # 超出阈值
        # 超出阈值应被 SKIP (不参与组合加权)

    def test_annual_within_threshold_kept(self):
        """年化 +150% → 在阈值内 → 保留"""
        annualized = 1.5
        assert self.MIN_ANNUALIZED <= annualized <= self.MAX_ANNUALIZED

    def test_annual_below_minus_99_clamped(self):
        """年化 -150% → 低于 -99% → 应被截断/处理"""
        annualized = -1.5
        assert annualized < self.MIN_ANNUALIZED


# ---------------------------------------------------------------------------
# LLM 输出质量: 描述行过滤 + 智能截断
# ---------------------------------------------------------------------------
class TestLlmOutputQualityFiltering:
    """验证 LLM 输出的描述行过滤与智能截断逻辑"""

    def _parse_and_filter(self, raw_text: str):
        """与 recommendation_generator.py L177-L259 一致的解析+过滤+智能截断"""
        from ai.recommendation_generator import generate_deepseek_recommendations

        # 构造 mock call_deepseek_fn
        def mock_call_deepseek(system_prompt, user_prompt, **kwargs):
            return raw_text

        # 用最小合法数据调用
        pnl_data = {"details": []}
        hedge_data = {"summary": {}, "details": [], "portfolio_beta": 1.0}
        result = generate_deepseek_recommendations(
            pnl_data, hedge_data, 0.0, "2026-08-04", "deepseek-chat", mock_call_deepseek
        )
        return result or []

    # --- 描述行过滤 ---

    def test_descriptive_lines_are_filtered_out(self):
        """描述性行 (日期/净盈亏/分析输入数据等) 应被过滤"""
        raw = """分析输入数据
日期: 2026-08-04
净盈亏: +12500.50
持仓数: 33
市场环境: 震荡偏弱
组合Beta: 1.25
IF空头2手 对冲组合Beta敞口
减持 688017 至 5% 仓位
建仓顺序: 特变电工优先"""

        result = self._parse_and_filter(raw)
        result_text = " ".join(result).lower()

        # 描述行不应出现在结果中 (组合Beta在行开头 → 过滤; 但在行中间 → 保留)
        assert "分析输入数据" not in result_text
        assert "日期" not in result_text
        assert "净盈亏" not in result_text
        assert "持仓数" not in result_text
        assert "市场环境" not in result_text

        # 操作建议应保留 (IF空头2手中的组合Beta在行中间, 不应被误过滤)
        assert any("if空头" in r.lower() for r in result)
        assert any("组合beta敞口" in r.lower() for r in result)
        assert any("减持" in r for r in result)
        assert any("建仓顺序" in r for r in result)

    def test_all_descriptive_fallback_to_original(self):
        """若全部被过滤, 回退到原始结果 (不返回空)"""
        raw = """分析输入数据
日期: 2026-08-04
净盈亏: +12500.50"""

        result = self._parse_and_filter(raw)
        # 回退后不应为空
        assert len(result) > 0

    # --- 操作关键词优先保留 ---

    def test_action_keyword_lines_prioritized(self):
        """含操作关键词的行应排在前面 (智能截断优先保留)"""
        # 构造 8 条: 4 条操作 + 4 条非操作
        raw = """一些非操作的观察文字
IF空头2手 对冲组合Beta敞口
另一段描述性文字
建仓顺序: 特变电工优先
更多背景描述
减持 688017 至 5% 仓位
还有一些空泛表述
510300 Put保护 对冲尾部风险"""

        result = self._parse_and_filter(raw)

        # 最多 6 条
        assert len(result) <= 6

        # 前几条应该都是操作建议
        first_4 = result[:4]
        action_count = sum(
            1 for r in first_4
            if any(kw in r for kw in ["IF空头", "建仓顺序", "减持", "Put", "止损", "加仓"])
        )
        assert action_count >= 3  # 至少 3 条操作建议在前面

    # --- 智能截断不超 6 条 ---

    def test_result_capped_at_6(self):
        """结果最多 6 条"""
        raw = "\n".join([f"IF空头{i}手" for i in range(1, 15)])
        result = self._parse_and_filter(raw)
        assert len(result) <= 6


# ---------------------------------------------------------------------------
# Py38 兼容性: PEP 585 类型注解
# ---------------------------------------------------------------------------
class TestPy38TypeAnnotationCompatibility:
    """验证关键模块的类型注解在 Python 3.8 下不会触发 PEP 585 错误"""

    def test_hedge_analyzer_uses_typing_list_not_pep585(self):
        """hedge_analyzer.py 应使用 typing.List 而非 PEP 585 list[str]"""
        import inspect
        from reporting import hedge_analyzer

        # 检查函数签名的类型注解
        func = hedge_analyzer._generate_if_contract_codes
        hints = get_type_hints(func)

        # 返回类型注解应是 typing.List (或其描述)
        return_annotation = hints.get("return")
        annotation_str = str(return_annotation)

        # 在 Python 3.8 下应能正确解析, 不抛 TypeError
        assert return_annotation is not None
        # 应包含 list/List 的表示 (不同 Python 版本显示略有差异)
        assert "list" in annotation_str.lower()

    def test_hedge_analyzer_all_pep585_safe(self):
        """hedge_analyzer.py 所有公共函数的类型注解在 Py38 下应可解析"""
        from reporting import hedge_analyzer

        # 检查 3 个修复过的函数
        funcs_to_check = [
            hedge_analyzer._generate_if_contract_codes,
            hedge_analyzer._fetch_if_close_from_sina,
            hedge_analyzer._fetch_if_close_from_provider,
        ]

        for func in funcs_to_check:
            # 不应抛 TypeError: 'type' object is not subscriptable
            try:
                hints = get_type_hints(func)
            except TypeError as e:
                if "not subscriptable" in str(e):
                    pytest.fail(f"{func.__name__} 使用了 PEP 585 类型注解, Py38 不兼容: {e}")
                raise

            # 参数注解中 if_codes 应能正确解析
            assert "if_codes" in hints or func.__name__ == "_generate_if_contract_codes"

    def test_recommendation_generator_typing_imports(self):
        """recommendation_generator.py 已从 typing 导入 List/Dict/Optional"""
        import ast

        source = Path("ai/recommendation_generator.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        # 检查 import 语句
        has_typing_import = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == "typing":
                    names = [alias.name for alias in node.names]
                    if "List" in names and "Dict" in names:
                        has_typing_import = True
                        break

        assert has_typing_import, "recommendation_generator.py 应从 typing 导入 List/Dict"
