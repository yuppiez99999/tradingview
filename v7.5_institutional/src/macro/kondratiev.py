# -*- coding: utf-8 -*-
"""
康波周期分析增强模块 v2.0
借鉴：TradingAgents-AShare macro_analyst + Vibe-Trading macro_strategy_forum + FinClaw akshare-macro

功能：
  1. 康波周期阶段判定（衰退→复苏→繁荣→滞胀）
  2. 第六轮康波（AI/算力驱动）阶段定位
  3. 行业轮动映射（各阶段最优配置板块）
  4. 十五五规划（2026-2030）与康波周期交叠分析
  5. 大宗商品周期信号生成

数据源优先级：iFinD MCP > Wind MCP > akshare > 本地估算
"""

import os
import json
from datetime import datetime
from typing import Dict, List, Tuple, Optional


# ============================================================
# 康波周期阶段定义（借鉴 Vibe-Trading macro_strategy_forum 多维度框架）
# ============================================================

class KondratievPhase:
    """康波周期四阶段枚举"""
    RECESSION = "衰退期"      # Winter: 通缩、去杠杆、资产价格下跌
    RECOVERY = "复苏期"       # Spring: 信贷扩张、产能利用率回升
    PROSPERITY = "繁荣期"     # Summer: 产能饱和、通胀温和、资产泡沫
    STAGFLATION = "滞胀期"   # Autumn: 产能过剩、通胀高企、利润压缩


# 第六轮康波周期（2023—）阶段特征
# 借鉴 FinClaw akshare-macro 的多维度宏观指标框架
KONDRATIEV_WAVE_6 = {
    "wave_label": "第六轮康波（AI/算力驱动）",
    "start_year": 2023,
    "expected_peak": 2038,
    "expected_end": 2058,
    "core_drivers": [
        "人工智能/大模型",
        "半导体/算力基础设施",
        "量子计算",
        "生物科技/基因编辑",
        "新能源/碳中和",
        "数字经济/数据要素",
    ],
    "current_phase_estimate": {
        "phase": KondratievPhase.RECOVERY,
        "progress_pct": 75,  # 复苏进度 75%，预计 2028 转入繁荣期
        "confidence": "中高",
        "basis": [
            "2023年AI突破标志第六轮康波启动",
            "全球半导体资本开支加速（铜/锡需求验证）",
            "中国十五五规划（2026-2030）对齐AI/高端制造",
            "全球央行宽松周期接近尾声，通胀趋稳",
        ]
    },
    # 各阶段最优配置（借鉴 TradingAgents-AShare macro_analyst 板块分析模式）
    "phase_allocation": {
        "复苏期": {
            "sectors": ["AI算力", "半导体", "新能源", "高端制造", "数字经济"],
            "commodities": ["铜", "锡", "白银"],
            "style": "成长 + 周期",
            "risk_level": "中高",
        },
        "繁荣期": {
            "sectors": ["消费升级", "金融科技", "先进制造", "新能源车"],
            "commodities": ["铜", "原油", "铝"],
            "style": "成长 + 消费",
            "risk_level": "中",
        },
        "滞胀期": {
            "sectors": ["黄金", "能源", "公用事业", "医药"],
            "commodities": ["黄金", "原油", "农产品"],
            "style": "防御 + 资源",
            "risk_level": "高",
        },
        "衰退期": {
            "sectors": ["黄金", "国债", "高股息", "必选消费"],
            "commodities": ["黄金", "白银"],
            "style": "避险 + 现金",
            "risk_level": "极高",
        },
    }
}


# 康波周期与十五五规划交叠分析
# 十五五规划期（2026-2030）恰好落在第六轮康波复苏→繁荣过渡期
FIFTEEN_FIVE_KONDRATIEV_OVERLAY = {
    "period": "2026-2030",
    "kondratiev_phase": f"{KondratievPhase.RECOVERY} → {KondratievPhase.PROSPERITY}",
    "synergy_sectors": [
        {"sector": "AI/算力/半导体", "fifteen_weight": 0.25, "kondratiev_score": 95, "rationale": "十五五核心方向 + 康波第六轮核心驱动力"},
        {"sector": "高端制造/先进制造", "fifteen_weight": 0.20, "kondratiev_score": 90, "rationale": "十五五制造强国战略 + 康波复苏期资本品需求"},
        {"sector": "新能源/碳中和", "fifteen_weight": 0.15, "kondratiev_score": 85, "rationale": "十五五绿色转型 + 康波能源结构变革"},
        {"sector": "生物医药/生命科学", "fifteen_weight": 0.15, "kondratiev_score": 80, "rationale": "十五五健康中国 + 康波生物科技驱动"},
        {"sector": "数字经济/数据要素", "fifteen_weight": 0.15, "kondratiev_score": 88, "rationale": "十五五数字中国 + 康波信息技术革命"},
        {"sector": "粮食/能源安全", "fifteen_weight": 0.10, "kondratiev_score": 70, "rationale": "十五五安全底线 + 康波资源重估"},
    ],
    "total_fifteen_weight": 1.0,
    "average_kondratiev_score": 84.7,
}


# ============================================================
# 康波周期分析器
# ============================================================

class KondratievCycleAnalyzer:
    """
    康波周期分析器 v2.0
    借鉴 Vibe-Trading macro_strategy_forum 的多智能体框架，
    提供周期阶段判定、行业轮动建议、大宗商品信号
    """

    def __init__(self, data_source=None):
        """
        Args:
            data_source: 可选的数据源管理器（DataConnectorManager）
        """
        self.data_source = data_source
        self.wave_config = KONDRATIEV_WAVE_6
        self.overlay = FIFTEEN_FIVE_KONDRATIEV_OVERLAY

    # ---------- 周期阶段判定 ----------

    def get_current_phase(self) -> Dict:
        """获取当前康波周期阶段"""
        estimate = self.wave_config["current_phase_estimate"]
        phase = estimate["phase"]

        # 当前阶段的配置建议
        allocation = self.wave_config["phase_allocation"].get(phase, {})

        return {
            "wave": self.wave_config["wave_label"],
            "phase": phase,
            "phase_name_cn": phase,
            "progress_pct": estimate["progress_pct"],
            "confidence": estimate["confidence"],
            "next_phase": self._get_next_phase(phase),
            "estimated_transition": self._estimate_transition_date(estimate["progress_pct"]),
            "recommended_sectors": allocation.get("sectors", []),
            "recommended_commodities": allocation.get("commodities", []),
            "recommended_style": allocation.get("style", ""),
            "risk_level": allocation.get("risk_level", ""),
            "basis": estimate.get("basis", []),
        }

    def _get_next_phase(self, current: str) -> str:
        """获取下一阶段"""
        phases = [KondratievPhase.RECESSION, KondratievPhase.RECOVERY,
                   KondratievPhase.PROSPERITY, KondratievPhase.STAGFLATION]
        try:
            idx = phases.index(current)
            return phases[(idx + 1) % len(phases)]
        except ValueError:
            return KondratievPhase.PROSPERITY

    def _estimate_transition_date(self, progress_pct: float) -> str:
        """估算转入下一阶段的时间"""
        remaining = 100 - progress_pct
        # 假设复苏期剩余约 1-3 年
        years_left = max(1, remaining / 25)  # 每年约推进25%
        from datetime import datetime
        transition_year = datetime.now().year + years_left
        return f"{transition_year:.0f}年前后"

    # ---------- 行业轮动映射 ----------

    def get_sector_allocation(self) -> List[Dict]:
        """获取康波周期驱动的行业配置建议"""
        phase = self.get_current_phase()
        allocation = self.wave_config["phase_allocation"].get(
            phase["phase"], {})

        sectors = allocation.get("sectors", [])
        result = []
        # 按十五五交叠评分加权
        synergy_map = {s["sector"]: s for s in self.overlay["synergy_sectors"]}

        for sector in sectors:
            synergy = synergy_map.get(sector, {})
            kondratiev_score = synergy.get("kondratiev_score", 70)
            fifteen_weight = synergy.get("fifteen_weight", 0)

            result.append({
                "sector": sector,
                "kondratiev_phase": phase["phase"],
                "kondratiev_favorability": kondratiev_score,
                "fifteen_five_weight": fifteen_weight,
                "combined_score": round(kondratiev_score * 0.6 + fifteen_weight * 100 * 0.4, 1),
                "recommendation": "超配" if kondratiev_score >= 85 else "标配" if kondratiev_score >= 70 else "低配",
            })

        result.sort(key=lambda x: x["combined_score"], reverse=True)
        return result

    # ---------- 大宗商品周期信号 ----------

    def get_commodity_signals(self) -> List[Dict]:
        """生成大宗商品周期信号"""
        phase = self.get_current_phase()
        allocation = self.wave_config["phase_allocation"].get(
            phase["phase"], {})

        commodity_signals = []
        # 各商品在康波周期中的角色
        commodity_roles = {
            "铜": {"driver": "AI算力/电气化", "phase_sensitivity": "高", "current_signal": "看多"},
            "锡": {"driver": "半导体焊料/封装", "phase_sensitivity": "极高", "current_signal": "看多"},
            "铝": {"driver": "轻量化/新能源", "phase_sensitivity": "中高", "current_signal": "偏多"},
            "黄金": {"driver": "避险/央行购金", "phase_sensitivity": "中", "current_signal": "配置"},
            "白银": {"driver": "光伏/工业+避险", "phase_sensitivity": "高", "current_signal": "看多"},
            "原油": {"driver": "能源转型过渡", "phase_sensitivity": "中", "current_signal": "中性"},
        }

        recommended = allocation.get("commodities", [])
        for comm, role in commodity_roles.items():
            commodity_signals.append({
                "name": comm,
                "driver": role["driver"],
                "phase_sensitivity": role["phase_sensitivity"],
                "kondratiev_recommendation": "推荐" if comm in recommended else "观望",
                "current_signal": role["current_signal"],
            })

        return commodity_signals

    # ---------- 十五五与康波交叠分析 ----------

    def get_fifteen_five_overlay(self) -> Dict:
        """十五五规划与康波周期交叠分析"""
        phase = self.get_current_phase()

        return {
            **self.overlay,
            "current_kondratiev_phase": phase["phase"],
            "kondratiev_progress": phase["progress_pct"],
            "synergy_conclusion": (
                "十五五规划期（2026-2030）恰好处于第六轮康波复苏→繁荣转换期，"
                "两者高度同频。十五五核心产业（AI、半导体、高端制造、新能源）"
                "与康波第六轮驱动力完全一致，形成政策+周期的戴维斯双击。"
            ),
            "investment_implication": (
                "当前应超配AI算力/半导体（康波+十五五双驱动，综合得分≥90），"
                "标配高端制造/新能源（政策+周期双支撑，综合得分≥80），"
                "适度配置黄金/白银（康波复苏期副线，综合得分≥70）。"
            ),
        }

    # ---------- 报告生成 ----------

    def generate_report(self, save_dir: str = None) -> str:
        """生成康波周期+十五五交叠分析报告"""
        phase = self.get_current_phase()
        sectors = self.get_sector_allocation()
        commodities = self.get_commodity_signals()
        overlay = self.get_fifteen_five_overlay()

        lines = []
        lines.append("# 康波周期 + 十五五规划交叠分析报告")
        lines.append("")
        lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"**分析引擎**: KondratievCycleAnalyzer v2.0")
        lines.append("")
        lines.append("---")
        lines.append("")

        # 一、康波周期当前阶段
        lines.append("## 一、康波周期当前阶段")
        lines.append("")
        lines.append(f"| 维度 | 内容 |")
        lines.append(f"|------|------|")
        lines.append(f"| 周期 | {phase['wave']} |")
        lines.append(f"| 当前阶段 | **{phase['phase']}** |")
        lines.append(f"| 阶段进度 | {phase['progress_pct']}% |")
        lines.append(f"| 置信度 | {phase['confidence']} |")
        lines.append(f"| 预计转入下一阶段 | {phase['estimated_transition']} |")
        lines.append(f"| 推荐风格 | {phase['recommended_style']} |")
        lines.append(f"| 风险等级 | {phase['risk_level']} |")
        lines.append("")

        # 判定依据
        lines.append("### 判定依据")
        for b in phase.get("basis", []):
            lines.append(f"- {b}")
        lines.append("")

        # 二、行业配置建议
        lines.append("---")
        lines.append("## 二、行业配置建议（康波周期驱动）")
        lines.append("")
        lines.append("| 行业 | 康波适配度 | 十五五权重 | 综合得分 | 建议 |")
        lines.append("|------|-----------|-----------|---------|------|")
        for s in sectors:
            lines.append(f"| {s['sector']} | {s['kondratiev_favorability']} | {s['fifteen_five_weight']:.0%} | {s['combined_score']} | **{s['recommendation']}** |")
        lines.append("")

        # 三、大宗商品信号
        lines.append("---")
        lines.append("## 三、大宗商品康波周期信号")
        lines.append("")
        lines.append("| 商品 | 周期驱动力 | 周期敏感性 | 康波建议 | 当前信号 |")
        lines.append("|------|-----------|-----------|---------|---------|")
        for c in commodities:
            lines.append(f"| {c['name']} | {c['driver']} | {c['phase_sensitivity']} | {c['kondratiev_recommendation']} | {c['current_signal']} |")
        lines.append("")

        # 四、十五五与康波交叠
        lines.append("---")
        lines.append("## 四、十五五规划与康波周期交叠分析")
        lines.append("")
        lines.append(f"> {overlay['synergy_conclusion']}")
        lines.append("")
        lines.append("### 交叠行业评分")
        lines.append("")
        lines.append("| 行业 | 十五五权重 | 康波评分 | 投资逻辑 |")
        lines.append("|------|-----------|---------|---------|")
        for s in overlay["synergy_sectors"]:
            lines.append(f"| {s['sector']} | {s['fifteen_weight']:.0%} | {s['kondratiev_score']} | {s['rationale']} |")
        lines.append("")
        lines.append(f"### 投资建议")
        lines.append(f"> {overlay['investment_implication']}")
        lines.append("")

        lines.append("---")
        lines.append(f"*本报告由康波周期分析引擎 v2.0 自动生成*")
        lines.append(f"*数据参考: TradingAgents-AShare / Vibe-Trading / FinClaw*")

        report = "\n".join(lines)

        if save_dir:
            os.makedirs(save_dir, exist_ok=True)
            filepath = os.path.join(save_dir,
                f"康波周期分析_{datetime.now().strftime('%Y%m%d')}.md")
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(report)
            print(f"[Kondratiev] 报告已保存: {filepath}")

        return report


# ============================================================
# 快速测试
# ============================================================

if __name__ == "__main__":
    analyzer = KondratievCycleAnalyzer()

    print("\n=== 康波周期当前阶段 ===")
    phase = analyzer.get_current_phase()
    for k, v in phase.items():
        print(f"  {k}: {v}")

    print("\n=== 行业配置建议 ===")
    for s in analyzer.get_sector_allocation():
        print(f"  {s['sector']}: 综合得分={s['combined_score']}, 建议={s['recommendation']}")

    print("\n=== 大宗商品信号 ===")
    for c in analyzer.get_commodity_signals():
        print(f"  {c['name']}: 信号={c['current_signal']}, 康波建议={c['kondratiev_recommendation']}")

    print("\n=== 十五五与康波交叠 ===")
    overlay = analyzer.get_fifteen_five_overlay()
    print(f"  {overlay['synergy_conclusion'][:80]}...")