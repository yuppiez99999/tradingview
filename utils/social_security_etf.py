"""
社保基金ETF追踪增强模块 v2.0
借鉴：社保基金追踪 social_security_tracker + ETF追踪程序 etf_tracker
      + Vibe-Trading macro_strategy_forum 风格轮动框架

功能：
  1. 社保基金投资风格映射到ETF（顺周期/高端制造/资源/防御）
  2. 国家队ETF资金流向检测（中央汇金/证金/社保基金）
  3. 社保风格与持仓风格的交叉比对
  4. 生成社保基金ETF风格追踪报告
"""
from __future__ import annotations

import logging
import os
from typing import Any

from utils.datetime_utils import now_bj

logger = logging.getLogger(__name__)


# ============================================================
# 社保基金投资风格配置（借鉴 social_security_tracker.py）
# ============================================================

SOCIAL_SECURITY_STYLES: dict[str, dict[str, Any]] = {
    "顺周期": {
        "weight": 0.25,
        "description": "金融、有色、钢铁等顺周期板块，受益于经济复苏",
        "representative_stocks": ["南山铝业", "南钢股份", "中稀有色", "藏格矿业"],
        "matching_etfs": [
            {
                "code": "512880",
                "name": "证券ETF国泰",
                "category": "金融主题",
                "match_score": 85,
            },
            {
                "code": "512800",
                "name": "银行ETF华宝",
                "category": "金融主题",
                "match_score": 82,
            },
        ],
        "cycle_signal": "当前康波复苏期，顺周期标配",
        "recommended_action": "标配",
    },
    "高端制造": {
        "weight": 0.35,
        "description": "电气设备、机械、软件服务等先进制造业，十五五核心方向",
        "representative_stocks": ["山推股份", "璞泰来", "天华新能", "中航光电"],
        "matching_etfs": [
            {
                "code": "588000",
                "name": "科创50ETF华夏",
                "category": "成长科技",
                "match_score": 95,
            },
            {
                "code": "512760",
                "name": "半导体ETF国泰",
                "category": "科技主题",
                "match_score": 92,
            },
            {
                "code": "515030",
                "name": "新能源车ETF华夏",
                "category": "新能源主题",
                "match_score": 88,
            },
            {
                "code": "512100",
                "name": "中证1000ETF南方",
                "category": "小盘风格",
                "match_score": 85,
            },
            {
                "code": "159915",
                "name": "创业板ETF易方达",
                "category": "成长科技",
                "match_score": 82,
            },
        ],
        "cycle_signal": "康波复苏期+十五五双重驱动，超配",
        "recommended_action": "超配",
    },
    "资源": {
        "weight": 0.20,
        "description": "黄金、有色、稀土等资源类，受益于全球通胀与资源重估",
        "representative_stocks": ["山金国际", "藏格矿业", "中稀有色", "高能环境"],
        "matching_etfs": [
            {
                "code": "518880",
                "name": "黄金ETF华安",
                "category": "避险资产",
                "match_score": 90,
            },
        ],
        "cycle_signal": "康波复苏期资源需求上升，标配",
        "recommended_action": "标配",
    },
    "防御": {
        "weight": 0.20,
        "description": "医药、消费等确定性增长板块，作为组合压舱石",
        "representative_stocks": ["国药股份", "科伦药业", "同仁堂", "伊利股份"],
        "matching_etfs": [
            {
                "code": "512170",
                "name": "医疗ETF华宝",
                "category": "医药主题",
                "match_score": 88,
            },
            {
                "code": "512010",
                "name": "医药ETF易方达",
                "category": "医药主题",
                "match_score": 86,
            },
            {
                "code": "510300",
                "name": "沪深300ETF华泰柏瑞",
                "category": "宽基核心",
                "match_score": 75,
            },
        ],
        "cycle_signal": "防御配置，提供下行保护",
        "recommended_action": "标配",
    },
}


# 国家队资金流向信号阈值（借鉴 etf_tracker.py 的 Wind MCP 数据）
NATIONAL_TEAM_SIGNAL_CONFIG: dict[str, Any] = {
    "high_threshold_yi": 50,  # 50亿以上 → 强信号
    "medium_threshold_yi": 10,  # 10亿以上 → 中等信号
    "low_threshold_yi": 2,  # 2亿以上 → 关注信号
    "trend_days": 5,  # 5日趋势检测
    "consecutive_days": 3,  # 连续3日同向 → 确认信号
    "state_keywords": ["中央汇金", "证金", "社保", "国家队", "中投", "国新"],
}


# ============================================================
# 风格分类器
# ============================================================


class SocialSecurityStyleClassifier:
    """
    社保基金风格分类器
    将ETF和个股映射到社保基金四大投资风格
    """

    def __init__(self):
        self.styles = SOCIAL_SECURITY_STYLES

    def classify_etf(self, code: str) -> dict | None:
        """将ETF代码分类到社保基金风格"""
        for style_name, style_config in self.styles.items():
            for etf in style_config["matching_etfs"]:
                if etf["code"] == code:
                    return {
                        "code": code,
                        "name": etf["name"],
                        "social_style": style_name,
                        "match_score": etf["match_score"],
                        "style_weight": style_config["weight"],
                        "recommended_action": style_config["recommended_action"],
                        "cycle_signal": style_config["cycle_signal"],
                    }
        return None

    def get_all_etf_classifications(self) -> list[dict]:
        """获取所有ETF的风格分类"""
        results = []
        seen = set()

        for style_name, style_config in self.styles.items():
            for etf in style_config["matching_etfs"]:
                code = etf["code"]
                if code not in seen:
                    seen.add(code)
                    results.append(
                        {
                            "code": code,
                            "name": etf["name"],
                            "social_style": style_name,
                            "match_score": etf["match_score"],
                            "style_weight": style_config["weight"],
                            "recommended_action": style_config["recommended_action"],
                            "cycle_signal": style_config["cycle_signal"],
                        }
                    )

        results.sort(key=lambda x: x["match_score"], reverse=True)
        return results

    def get_style_summary(self) -> dict:
        """获取社保基金风格配置摘要"""
        summary = {}
        for style_name, style_config in self.styles.items():
            summary[style_name] = {
                "weight": style_config["weight"],
                "description": style_config["description"],
                "recommended_action": style_config["recommended_action"],
                "cycle_signal": style_config["cycle_signal"],
                "etf_count": len(style_config["matching_etfs"]),
                "stock_count": len(style_config["representative_stocks"]),
                "top_etfs": [e["name"] for e in style_config["matching_etfs"][:3]],
                "top_stocks": style_config["representative_stocks"][:3],
            }
        return summary


# ============================================================
# 国家队信号检测器
# ============================================================


class NationalTeamSignalDetector:
    """
    国家队资金信号检测器
    借鉴 etf_tracker.py 的 Wind MCP 资金流分析逻辑
    """

    def __init__(self):
        self.config = NATIONAL_TEAM_SIGNAL_CONFIG
        self.classifier = SocialSecurityStyleClassifier()

    def detect_signals(self, flow_data: dict[str, dict]) -> list[dict]:
        """
        检测国家队资金信号

        Args:
            flow_data: ETF资金流数据 {code: {net_flow_yi, trend, category, ...}}

        Returns:
            信号列表，按置信度排序
        """
        signals = []

        for code, data in flow_data.items():
            net_flow = data.get("net_flow_yi", 0)
            trend = data.get("trend", "中性")

            # 信号强度判定
            if net_flow >= self.config["high_threshold_yi"]:
                confidence = "高"
                signal_type = "国家队强加仓信号"
            elif net_flow >= self.config["medium_threshold_yi"]:
                confidence = "中"
                signal_type = "国家队加仓信号"
            elif net_flow >= self.config["low_threshold_yi"]:
                confidence = "低"
                signal_type = "国家队关注信号"
            elif net_flow <= -self.config["high_threshold_yi"]:
                confidence = "高"
                signal_type = "国家队强减仓信号"
            elif net_flow <= -self.config["medium_threshold_yi"]:
                confidence = "中"
                signal_type = "国家队减仓信号"
            elif net_flow <= -self.config["low_threshold_yi"]:
                confidence = "低"
                signal_type = "国家队减持关注"
            else:
                continue

            # 获取社保基金风格分类
            style_info = self.classifier.classify_etf(code)

            signals.append(
                {
                    "code": code,
                    "name": data.get("name", code),
                    "category": data.get("category", "未知"),
                    "net_flow_yi": net_flow,
                    "trend": trend,
                    "signal_type": signal_type,
                    "confidence": confidence,
                    "social_style": (
                        style_info["social_style"] if style_info else "未匹配"
                    ),
                    "style_recommendation": (
                        style_info["recommended_action"] if style_info else "-"
                    ),
                }
            )

        # 排序：置信度 > 净流入金额
        signals.sort(
            key=lambda x: (
                0 if x["confidence"] == "高" else 1 if x["confidence"] == "中" else 2,
                -abs(x["net_flow_yi"]),
            )
        )

        return signals

    def get_style_flow_summary(self, signals: list[dict]) -> dict:
        """按社保基金风格汇总资金流向"""
        style_flows: dict[str, dict[str, Any]] = {}

        for signal in signals:
            style = signal.get("social_style", "未匹配")
            if style not in style_flows:
                style_flows[style] = {
                    "total_flow_yi": 0,
                    "signal_count": 0,
                    "strong_buy": 0,
                    "strong_sell": 0,
                    "etfs": [],
                }

            style_flows[style]["total_flow_yi"] += signal["net_flow_yi"]
            style_flows[style]["signal_count"] += 1

            if "加仓" in signal["signal_type"]:
                style_flows[style]["strong_buy"] += 1
            elif "减仓" in signal["signal_type"]:
                style_flows[style]["strong_sell"] += 1

            style_flows[style]["etfs"].append(signal["name"])

        # 生成风格操作建议
        for _style, data in style_flows.items():
            if data["total_flow_yi"] >= 30:
                data["action"] = "增持"
            elif data["total_flow_yi"] <= -30:
                data["action"] = "减持"
            elif data["total_flow_yi"] > 0:
                data["action"] = "关注"
            else:
                data["action"] = "观望"

        return style_flows


# ============================================================
# 社保基金ETF追踪器（主类）
# ============================================================


class SocialSecurityETFTracker:
    """
    社保基金ETF风格追踪器 v2.0
    整合风格分类 + 信号检测 + 建议生成
    """

    def __init__(self):
        self.classifier = SocialSecurityStyleClassifier()
        self.detector = NationalTeamSignalDetector()

    def analyze(self, flow_data: dict | None = None) -> dict:
        """
        综合分析

        Args:
            flow_data: ETF资金流数据（可选，无数据时仅做静态风格分析）

        Returns:
            综合分析结果
        """
        result: dict[str, Any] = {
            "style_summary": self.classifier.get_style_summary(),
            "etf_classifications": self.classifier.get_all_etf_classifications(),
            "signals": [],
            "style_flows": {},
            "recommendations": [],
        }

        # SC-10 (2026-09-12): 模拟数据不得生成"国家队加仓/减仓"信号 ——
        # 此前 etf_fund_tracker 全链降级到 P6 mock 时, 均匀随机净流足以
        # 触发 50 亿级强加仓假信号并进入本模块的建议与报告。
        mock_degraded = any(
            bool(d.get("mock_degraded")) for d in (flow_data or {}).values()
        )
        if flow_data and mock_degraded:
            result["data_degraded"] = True
            result["degraded_reason"] = (
                "ETF 资金流数据为模拟数据 (Wind MCP/akshare 均不可用), "
                "信号检测已跳过, 仅保留静态风格分析"
            )
        elif flow_data:
            result["signals"] = self.detector.detect_signals(flow_data)
            result["style_flows"] = self.detector.get_style_flow_summary(
                result["signals"]
            )

        # 生成静态配置建议（基于风格权重）
        for style_name, style_config in SOCIAL_SECURITY_STYLES.items():
            result["recommendations"].append(
                {
                    "style": style_name,
                    "target_weight": style_config["weight"],
                    "action": style_config["recommended_action"],
                    "rationale": style_config["cycle_signal"],
                    "matching_etfs": [
                        e["name"] for e in style_config["matching_etfs"][:2]
                    ],
                }
            )

        return result

    def generate_report(
        self, flow_data: dict | None = None, save_dir: str | None = None
    ) -> str:
        """生成社保基金ETF风格追踪报告"""
        analysis = self.analyze(flow_data)

        lines = []
        lines.append("# 社保基金ETF风格追踪报告")
        lines.append("")
        lines.append(f"**生成时间**: {now_bj().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("**分析引擎**: SocialSecurityETFTracker v2.0")
        lines.append("**参考数据源**: 社保基金2025年报持仓 + ETF资金流向")
        lines.append("")
        lines.append("---")
        lines.append("")

        # 一、社保基金投资风格概览
        lines.append("## 一、社保基金投资风格配置")
        lines.append("")
        lines.append("| 风格 | 目标权重 | 操作建议 | 周期信号 | 代表ETF |")
        lines.append("|------|---------|---------|---------|---------|")
        for r in analysis["recommendations"]:
            etfs = "、".join(r["matching_etfs"])
            lines.append(
                f"| **{r['style']}** | {r['target_weight']:.0%} | **{r['action']}** | {r['rationale']} | {etfs} |"
            )
        lines.append("")

        # 二、ETF风格分类
        lines.append("---")
        lines.append("## 二、ETF与社保基金风格映射")
        lines.append("")
        lines.append("| ETF名称 | 代码 | 社保风格 | 匹配度 | 风格权重 |")
        lines.append("|---------|------|---------|--------|---------|")
        for etf in analysis["etf_classifications"][:15]:
            lines.append(
                f"| {etf['name']} | {etf['code']} | {etf['social_style']} | {etf['match_score']} | {etf['style_weight']:.0%} |"  # noqa: E501
            )
        lines.append("")

        # 三、国家队资金信号（如有数据）
        if analysis["signals"]:
            lines.append("---")
            lines.append("## 三、国家队资金流向信号")
            lines.append("")
            lines.append(
                "| ETF名称 | 代码 | 净流入(亿) | 信号类型 | 置信度 | 社保风格 |"
            )
            lines.append("|---------|------|-----------|---------|--------|---------|")
            for s in analysis["signals"][:10]:
                lines.append(
                    f"| {s['name']} | {s['code']} | {s['net_flow_yi']:+.1f} | {s['signal_type']} | {s['confidence']} | {s['social_style']} |"  # noqa: E501
                )
            lines.append("")

        # 四、投资建议
        lines.append("---")
        lines.append("## 四、投资建议")
        lines.append("")

        for r in analysis["recommendations"]:
            icon = (
                "📈"
                if r["action"] == "超配"
                else "📊" if r["action"] == "标配" else "📉"
            )
            lines.append(
                f"{icon} **{r['style']}**（{r['target_weight']:.0%}）：{r['action']}"
            )
            lines.append(f"  - {r['rationale']}")
            lines.append(f"  - 推荐ETF：{', '.join(r['matching_etfs'])}")
            lines.append("")

        lines.append("---")
        lines.append("*本报告由社保基金ETF追踪引擎 v2.0 自动生成*")
        lines.append("*数据参考: 社保基金2025年报 / ETF追踪程序 / Vibe-Trading*")

        report = "\n".join(lines)

        if save_dir:
            os.makedirs(save_dir, exist_ok=True)
            filepath = os.path.join(
                save_dir, f"社保基金ETF追踪_{now_bj().strftime('%Y%m%d')}.md"
            )
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(report)
            logger.info(f"[SocialSecurityETF] 报告已保存: {filepath}")

        return report


# ============================================================
# 快速测试
# ============================================================

if __name__ == "__main__":
    tracker = SocialSecurityETFTracker()

    logger.info("\n=== 社保基金风格分类 ===")
    for etf in tracker.classifier.get_all_etf_classifications():
        logger.info(
            f"  {etf['name']} → {etf['social_style']} (匹配度={etf['match_score']})"
        )

    logger.info("\n=== 风格配置摘要 ===")
    summary = tracker.classifier.get_style_summary()
    for style, info in summary.items():
        logger.info(
            f"  {style}: 权重={info['weight']:.0%}, 建议={info['recommended_action']}"
        )
        logger.info(f"    ETF: {info['top_etfs']}")
        logger.info(f"    个股: {info['top_stocks']}")

    logger.info("\n=== 投资建议 ===")
    analysis = tracker.analyze()
    for r in analysis["recommendations"]:
        logger.info(f"  {r['style']}: {r['action']} - {r['rationale']}")
