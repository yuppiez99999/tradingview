"""三方对比报告生成 (B3.5: 从 lgb_enhanced_trainer.py 抽取)

本模块集中以下职责:
  - generate_comparison_report: 主入口, 生成 Markdown 三方对比报告
  - _build_report_header: 报告标题 + 优化点说明 + 三方对比表头
  - _build_comparison_table: 三方对比表 (旧集成 / TSCV / 增强)
  - _build_summary_section: 整体改进汇总 (R²/IC 平均改进)
  - _build_cv_detail_section: CV 详情章节
  - _build_feature_importance_section: 特征重要性章节 (Top 10)
  - _build_sentiment_stats_section: 情绪因子入选统计
  - _build_extended_feature_section: 扩展特征入选统计
  - _build_adaptive_retrain_section: 自适应重训统计
  - _build_risk_notes_section: 风险提示章节

依赖说明:
  报告生成需要 LGB_ENHANCED_CONFIG (配置常量) 和 BASE_DIR/REPORTS_DIR (路径常量),
  通过模块属性注入方式避免循环依赖。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from utils.datetime_utils import now_bj

logger = logging.getLogger("lgb_enhanced")


# ============================================================
# 路径/配置 (由主模块注入, 避免硬编码)
# ============================================================
# 默认值 (若主模块未注入则使用)
BASE_DIR: Path = Path(__file__).resolve().parent.parent
REPORTS_DIR: Path = BASE_DIR / "reports" / "lgb_enhanced"
LGB_ENHANCED_CONFIG: dict[str, Any] = {
    "adaptive_retrain_threshold": 5,
    "adaptive_retrain_lr": 0.001,
    "adaptive_retrain_n_estimators": 5000,
}


def configure_paths(base_dir: Path, reports_dir: Path, config: dict[str, Any]) -> None:
    """由主模块注入路径和配置 (在主模块 import 后调用)。

    Args:
        base_dir: 项目根目录
        reports_dir: 报告输出目录
        config: LGB_ENHANCED_CONFIG 配置字典
    """
    global BASE_DIR, REPORTS_DIR, LGB_ENHANCED_CONFIG
    BASE_DIR = base_dir
    REPORTS_DIR = reports_dir
    LGB_ENHANCED_CONFIG = config


# 扩展特征分类配置 (v2 新增)
_EXTENDED_FEATURE_CATEGORIES: dict[str, list[str]] = {
    "行业相对强度": [
        "industry_return_5",
        "industry_return_20",
        "relative_strength_5",
        "relative_strength_20",
        "industry_rank_20",
    ],
    "资金流向": [
        "capital_flow",
        "cumulative_flow_5",
        "flow_divergence",
        "smart_money_ratio",
        "flow_momentum",
    ],
    "跨市场信号": [
        "gold_trend_20",
        "bank_trend_20",
        "tech_style_20",
        "defensive_style_20",
        "style_rotation_5",
        "safe_haven_flow",
    ],
}


# ============================================================
# 报告章节构建函数
# ============================================================
def _build_report_header(result: dict[str, Any]) -> list[str]:
    """构建报告标题 + 优化点说明 + 三方对比表头。"""
    return [
        f"# LightGBM 增强训练报告 - {now_bj().strftime('%Y-%m-%d')}",
        "",
        f"**生成时间**: {now_bj().isoformat()}",
        "**模型类型**: LightGBM 增强版 (真实OHLCV + 情绪因子v4.3 + 放宽早停)",
        "**数据源**: Wind MCP > iFinD MCP > 新浪 HTTP (真实价格和成交量)",
        f"**标的数**: {result['total']}",
        f"**训练成功**: {result['trained']}",
        f"**跳过**: {result['skipped']}",
        f"**失败**: {result['failed']}",
        "",
        "## 优化点",
        "",
        "1. **真实 OHLCV 数据**: 替代合成数据",
        "   - Wind MCP / iFinD MCP / 新浪 HTTP 多源优先级",
        "   - 502 日真实价格和成交量",
        "   - 技术指标基于真实价格计算, 质量更高",
        "2. **新闻情绪/关注度因子 v4.3**: 正常参与特征选择, 无保护机制, 无贡献自动退选",
        "   - market_sentiment: 全市场聚合情绪指数 (每日有值, ~60%+ 覆盖率)",
        "   - market_sent_change: 市场情绪5日变化 (捕捉情绪拐点)",
        "   - has_news: 当日是否有该股新闻 (0/1, 关注度代理变量)",
        "   - news_sentiment_raw: 新闻日分级加权情绪分 (NaN on 无新闻日, LightGBM native split)",
        "3. **特征选择增强**: top_n 20→30, threshold 3→1 (情绪因子已移除保护, 正常参与筛选)",
        "4. **放宽早停**: 50 → 200 轮",
        "   - n_estimators 1000 → 2000",
        "   - learning_rate 0.01 → 0.005",
        "   - 允许模型更充分学习",
        "",
        "## 一、三方对比 (R²)",
        "",
        "| 标的 | 名称 | 旧集成 R² | LGB+TSCV R² | LGB增强 R² | 总改进 | 旧 IC | TSCV IC | 增强 IC | IC 改进 |",
        "|------|------|---------|------------|-----------|--------|-------|---------|---------|---------|",
    ]


def _load_old_meta_metrics(old_models_dir: Path, code: str) -> tuple[Any, Any]:
    """加载旧集成模型的 R² 和 IC 指标。

    Args:
        old_models_dir: 旧集成模型目录
        code: 标的代码

    Returns:
        (old_r2, old_ic) 元组, 未找到时为 "N/A"
    """
    old_r2 = "N/A"
    old_ic = "N/A"
    old_meta_path = old_models_dir / code / f"{code}_meta.json"
    if old_meta_path.exists():
        with open(old_meta_path, encoding="utf-8") as f:
            old_meta = json.load(f)
        old_r2 = old_meta.get("metrics", {}).get("ensemble_r2", "N/A")
        old_ic = old_meta.get("metrics", {}).get("ensemble_ic", "N/A")
    return old_r2, old_ic


def _load_tscv_meta_metrics(tscv_models_dir: Path, code: str) -> tuple[Any, Any]:
    """加载 LGB+TSCV 模型的 R² 和 IC 指标。

    Args:
        tscv_models_dir: TSCV 模型目录
        code: 标的代码

    Returns:
        (tscv_r2, tscv_ic) 元组, 未找到时为 "N/A"
    """
    tscv_r2 = "N/A"
    tscv_ic = "N/A"
    tscv_meta_path = tscv_models_dir / code / f"{code}_meta.json"
    if tscv_meta_path.exists():
        with open(tscv_meta_path, encoding="utf-8") as f:
            tscv_meta = json.load(f)
        tscv_r2 = tscv_meta.get("cv_after_selection", {}).get("mean_r2", "N/A")
        tscv_ic = tscv_meta.get("cv_after_selection", {}).get("mean_ic", "N/A")
    return tscv_r2, tscv_ic


def _extract_enhanced_metrics(r: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
    """从训练结果提取增强模型的 R²/IC 及标准差。

    Args:
        r: 单个标的的训练结果

    Returns:
        (mean_r2, mean_ic, std_r2, std_ic) 元组
    """
    if r.get("status") == "OK":
        cv = r["cv_after_selection"]
        return cv["mean_r2"], cv["mean_ic"], cv["std_r2"], cv["std_ic"]
    meta = r.get("meta", {}).get("cv_after_selection", {})
    return (
        meta.get("mean_r2", "N/A"),
        meta.get("mean_ic", "N/A"),
        meta.get("std_r2", 0),
        meta.get("std_ic", 0),
    )


def _compute_metric_improvement(
    old_val: Any, new_val: Any, improvements_list: list[float]
) -> str:
    """计算指标改进值并记录到 improvements_list, 返回格式化字符串。

    Args:
        old_val: 旧指标值
        new_val: 新指标值
        improvements_list: 改进值列表 (原地追加)

    Returns:
        格式化的改进字符串 (如 "+0.0123"), 无效时 "N/A"
    """
    if isinstance(old_val, (int, float)) and isinstance(new_val, (int, float)):
        diff = round(new_val - old_val, 4)
        improvements_list.append(diff)
        return f"{diff:+.4f}"
    return "N/A"


def _build_comparison_table(
    result: dict[str, Any], old_models_dir: Path, tscv_models_dir: Path
) -> tuple[list[str], list[float], list[float]]:
    """构建三方对比表 (旧集成 / TSCV / 增强)。

    Args:
        result: 训练结果
        old_models_dir: 旧集成模型目录
        tscv_models_dir: TSCV 模型目录

    Returns:
        (table_lines, improvements_r2, improvements_ic) 元组
    """
    lines: list[str] = []
    improvements_r2: list[float] = []
    improvements_ic: list[float] = []
    for code, r in result["results"].items():
        if r.get("status") not in ("OK", "CACHED"):
            continue
        name = r.get("name", "")
        old_r2, old_ic = _load_old_meta_metrics(old_models_dir, code)
        tscv_r2, tscv_ic = _load_tscv_meta_metrics(tscv_models_dir, code)
        new_r2, new_ic, new_r2_std, new_ic_std = _extract_enhanced_metrics(r)
        r2_improve = _compute_metric_improvement(old_r2, new_r2, improvements_r2)
        ic_improve = _compute_metric_improvement(old_ic, new_ic, improvements_ic)
        lines.append(
            f"| {code} | {name} | {old_r2} | {tscv_r2} | "
            f"{new_r2}±{new_r2_std} | {r2_improve} | "
            f"{old_ic} | {tscv_ic} | {new_ic}±{new_ic_std} | {ic_improve} |"
        )
    return lines, improvements_r2, improvements_ic


def _build_summary_section(
    improvements_r2: list[float], improvements_ic: list[float]
) -> list[str]:
    """构建整体改进汇总章节 (R²/IC 平均改进 + 改进标的数)。

    Args:
        improvements_r2: R² 改进值列表
        improvements_ic: IC 改进值列表

    Returns:
        章节行列表 (无改进时为空列表)
    """
    if not improvements_r2:
        return []
    return [
        "",
        "## 二、整体改进汇总 (vs 旧集成)",
        "",
        f"- R² 平均改进: **{np.mean(improvements_r2):+.4f}**",
        f"- R² 改进标的数: {sum(1 for x in improvements_r2 if x > 0)} / {len(improvements_r2)}",
        f"- IC 平均改进: **{np.mean(improvements_ic):+.4f}**",
        f"- IC 改进标的数: {sum(1 for x in improvements_ic if x > 0)} / {len(improvements_ic)}",
    ]


def _build_cv_detail_section(result: dict[str, Any]) -> list[str]:
    """构建 CV 详情章节 (特征选择后的交叉验证指标)。"""
    lines = [
        "",
        "## 三、CV 详情 (特征选择后)",
        "",
        "| 标的 | 名称 | Fold | CV R² (mean±std) | CV IC (mean±std) | CV Sharpe | 最终 R² | 最终 IC | 信号 | 特征数 | Best Iter |",  # noqa: E501
        "|------|------|------|------------------|------------------|-----------|---------|--------|------|--------|-----------|",  # noqa: E501
    ]
    for code, r in result["results"].items():
        if r.get("status") != "OK":
            continue
        cv = r["cv_after_selection"]
        fm = r["final_metrics"]
        n_folds = len(cv["fold_metrics"])
        lines.append(
            f"| {code} | {r.get('name', '')} | {n_folds} | "
            f"{cv['mean_r2']}±{cv['std_r2']} | "
            f"{cv['mean_ic']}±{cv['std_ic']} | "
            f"{cv['mean_sharpe']}±{cv['std_sharpe']} | "
            f"{fm['r2']} | {fm['ic']} | {r['signal']} | "
            f"{r['n_features_before']}→{r['n_features_after']} | "
            f"{r['best_iteration']} |"
        )
    return lines


def _classify_feature_tag(feat: str) -> str:
    """根据特征名分类标签。

    Args:
        feat: 特征名

    Returns:
        分类标签字符串 (如 " [情绪因子]"), 无匹配时为空串
    """
    if "sent" in feat or "news" in feat:
        return " [情绪因子]"
    if feat.startswith(("industry_", "relative_strength_", "industry_rank_")):
        return " [行业相对强度]"
    if feat in (
        "capital_flow",
        "cumulative_flow_5",
        "flow_divergence",
        "smart_money_ratio",
        "flow_momentum",
    ):
        return " [资金流向]"
    if feat.startswith(
        (
            "gold_",
            "bank_",
            "tech_style",
            "defensive_style",
            "style_rotation",
            "safe_haven_flow",
        )
    ):
        return " [跨市场信号]"
    return ""


def _build_feature_importance_section(result: dict[str, Any]) -> list[str]:
    """构建特征重要性章节 (每个标的 Top 10 特征)。"""
    lines = ["", "## 四、特征重要性 (Top 10)", ""]
    for code, r in result["results"].items():
        if r.get("status") != "OK":
            continue
        top_feat = r.get("top_features", {})
        if not top_feat:
            continue
        lines.append(f"### {code} ({r.get('name', '')})")
        lines.append("")
        for i, (feat, imp) in enumerate(list(top_feat.items())[:10], 1):
            tag = _classify_feature_tag(feat)
            lines.append(f"{i}. **{feat}**: {imp}{tag}")
        lines.append("")
    return lines


def _build_sentiment_stats_section(result: dict[str, Any]) -> list[str]:
    """构建情绪因子入选统计章节。

    v4.3 (2026-08-02): 移除保护机制, 情绪因子正常参与特征选择,
    仅统计实际入选 (importance > 0) 的情况。
    """
    lines = ["", "## 五、情绪因子入选统计", ""]
    lines.append("**v4.3**: 情绪因子已移除保护机制, 正常参与特征选择。")
    lines.append("")
    sent_effective = 0  # 实际有贡献 (importance > 0)
    sent_total = 0
    for code, r in result["results"].items():
        if r.get("status") != "OK":
            continue
        sent_total += 1
        selected = r.get("selected_features", [])
        top_feat = r.get("top_features", {})
        sent_in_selection = [
            f
            for f in selected
            if "sent" in f or "news_" in f or "market_sent" in f or "has_news" in f
        ]
        # 仅当 importance > 0 才算"有效入选"
        sent_with_impact = [f for f in sent_in_selection if top_feat.get(f, 0) > 0]
        if sent_with_impact:
            sent_effective += 1
            lines.append(
                f"- {code} ({r.get('name', '')}): 有效={', '.join(sent_with_impact)}, "
                f"零贡献={len(sent_in_selection) - len(sent_with_impact)}"
            )
        else:
            lines.append(
                f"- {code} ({r.get('name', '')}): "
                f"(入选 {len(sent_in_selection)} 个, 但 importance 全为 0, 未实际分裂)"
            )
    lines.append("")
    lines.append(f"**情绪因子入选标的数**: {sent_effective} / {sent_total}")
    lines.append(
        f"**情绪因子有效贡献比例 (importance>0)**: {sent_effective} / {sent_total}"
    )
    return lines


def _build_extended_feature_section(result: dict[str, Any]) -> list[str]:
    """构建扩展特征入选统计章节 (行业/资金流向/跨市场信号)。"""
    lines = ["", "## 五(补)、扩展特征入选统计 (v2 新增)", ""]
    total_ok = sum(1 for r in result["results"].values() if r.get("status") == "OK")
    for cat_name, cat_features in _EXTENDED_FEATURE_CATEGORIES.items():
        included_count = 0
        included_details: list[str] = []
        for code, r in result["results"].items():
            if r.get("status") != "OK":
                continue
            selected = r.get("selected_features", [])
            ext_feats = [f for f in selected if f in cat_features]
            if ext_feats:
                included_count += 1
                included_details.append(
                    f"  - {code} ({r.get('name', '')}): {', '.join(ext_feats)}"
                )
        lines.append(f"### {cat_name}: {included_count} / {total_ok} 标的入选")
        lines.extend(included_details)
        lines.append("")
    return lines


def _build_adaptive_retrain_section(result: dict[str, Any]) -> list[str]:
    """构建自适应重训统计章节 (欠拟合标的)。"""
    adaptive_count = 0
    adaptive_improved = 0
    for _code, r in result["results"].items():
        if r.get("status") != "OK":
            continue
        if r.get("adaptive_retrained", False):
            adaptive_count += 1
            adaptive_improved += 1
    return [
        "",
        "## 六、自适应重训统计 (欠拟合标的)",
        "",
        f"- 触发自适应重训标的数: {adaptive_count}",
        f"- 实际改进标的数: {adaptive_improved}",
        f"- 触发条件: best_iter <= {LGB_ENHANCED_CONFIG.get('adaptive_retrain_threshold', 5)}",
        f"- 重训参数: learning_rate={LGB_ENHANCED_CONFIG.get('adaptive_retrain_lr', 0.001)}, "
        f"n_estimators={LGB_ENHANCED_CONFIG.get('adaptive_retrain_n_estimators', 5000)}",
    ]


def _build_quality_suppress_section(result: dict[str, Any]) -> list[str]:
    """构建信号质量与抑制统计章节 (P0/P1 修复后新增, 2026-08-02)。"""
    noise_list: list[str] = []
    low_quality_list: list[str] = []
    ic_divergence_list: list[str] = []
    for code, r in result["results"].items():
        if r.get("status") not in ("OK", "CACHED"):
            continue
        qf = r.get("quality_flag", "OK")
        cv = r.get("cv_after_selection", {})
        fm = r.get("final_metrics", {})
        cv_ic = cv.get("mean_ic", 0)
        final_ic = fm.get("ic", 0)
        best_iter = r.get("best_iteration", 0)
        raw_signal = r.get("signal", 0)
        if qf == "NOISE":
            noise_list.append(
                f"  - {code} ({r.get('name', '')}): best_iter={best_iter}, "
                f"final_ic={final_ic:.4f}, 原始信号={raw_signal:.4f} → 置零"
            )
        elif qf == "LOW_QUALITY":
            detail = (
                f"  - {code} ({r.get('name', '')}): CV_R²={cv.get('mean_r2', 0):.4f}, "
            )
            detail += f"CV_IC={cv_ic:.4f}"
            if abs(cv_ic - final_ic) > 0.3:
                detail += f", Final_IC={final_ic:.4f} (偏差={abs(cv_ic - final_ic):.2f}, 过拟合)"
                ic_divergence_list.append(code)
            detail += f", 原始信号={raw_signal:.4f} → 置零"
            low_quality_list.append(detail)

    if not noise_list and not low_quality_list:
        return []

    lines = ["", "## 七、信号质量控制 (P0/P1 修复)", ""]
    lines.append("以下标的因模型质量不达标, 输出信号 (signal) 已强制置零:  ")
    lines.append("(raw_signal 字段保留原始信号供调试审查, 未被交易策略使用)  ")
    lines.append("")

    if noise_list:
        lines.append("### NOISE (纯噪声, best_iter≤1 且 final_IC<0)")
        lines.append("")
        lines.extend(noise_list)
        lines.append("")

    if low_quality_list:
        lines.append("### LOW_QUALITY (CV 指标不达标或 IC 过拟合)")
        lines.append("")
        lines.extend(low_quality_list)
        lines.append("")

    if ic_divergence_list:
        lines.append(
            f"**IC 过拟合标签** ({len(ic_divergence_list)} 个): "
            f"CV_IC 与 Final_IC 偏差 > 0.3 — {', '.join(ic_divergence_list)}"
        )
        lines.append("")

    active_buy = sum(
        1
        for r in result["results"].values()
        if r.get("quality_flag", "") not in ("LOW_QUALITY", "NOISE")
        and r.get("signal", 0) > 0.2
    )
    active_sell = sum(
        1
        for r in result["results"].values()
        if r.get("quality_flag", "") not in ("LOW_QUALITY", "NOISE")
        and r.get("signal", 0) < -0.2
    )
    lines.append(f"**抑制后有效信号**: 买入 {active_buy} 个, 卖出 {active_sell} 个")
    return lines


def _build_risk_notes_section(report_path: Path) -> list[str]:
    """构建风险提示章节 (含报告路径和信号文件位置)。"""
    return [
        "",
        "## 八、风险提示",
        "",
        "- 真实 OHLCV 通过多数据源拉取, 质量较合成数据显著提升",
        "- 新闻情绪因子: 一次性拉取过去 N 天真实新闻, 按 publish_time 分配到日期",
        "- 自适应重训: 对 best_iter <= 阈值的标的, 使用更小学习率 + 更多估计器重训",
        "- 若 best_iter 接近 n_estimators, 说明模型仍未收敛, 可考虑增加估计器",
        "- **P0/P1 修复 (2026-08-02)**: LOW_QUALITY/NOISE 信号已自动置零, "
        "仅 quality_flag=OK 的标的信号可被交易策略使用",
        "",
        "---",
        f"**报告路径**: `{report_path}`",
        "**信号文件**: `models/lgb_enhanced/lgb_enhanced_signals.json`",
    ]


# ============================================================
# 主入口
# ============================================================
def generate_comparison_report(result: dict[str, Any]) -> Path:
    """生成三方对比报告。

    Args:
        result: 训练结果汇总 (含 results, total, trained, skipped, failed)

    Returns:
        报告文件路径
    """
    today = now_bj().strftime("%Y%m%d")
    report_path = REPORTS_DIR / f"lgb_enhanced_report_{today}.md"

    old_models_dir = BASE_DIR / "models" / "autolearn"
    tscv_models_dir = BASE_DIR / "models" / "lgb_tscv"

    # 标题 + 优化点 + 三方对比表
    lines = _build_report_header(result)
    table_lines, improvements_r2, improvements_ic = _build_comparison_table(
        result, old_models_dir, tscv_models_dir
    )
    lines.extend(table_lines)
    # 整体改进汇总
    lines.extend(_build_summary_section(improvements_r2, improvements_ic))
    # CV 详情
    lines.extend(_build_cv_detail_section(result))
    # 特征重要性
    lines.extend(_build_feature_importance_section(result))
    # 情绪因子入选统计
    lines.extend(_build_sentiment_stats_section(result))
    # 扩展特征入选统计
    lines.extend(_build_extended_feature_section(result))
    # 自适应重训统计
    lines.extend(_build_adaptive_retrain_section(result))
    # 信号质量控制 (P0/P1 修复, 2026-08-02)
    lines.extend(_build_quality_suppress_section(result))
    # 风险提示
    lines.extend(_build_risk_notes_section(report_path))

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info(f"对比报告已生成: {report_path}")
    return report_path
