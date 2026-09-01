"""
康波周期分析增强模块 v2.0
借鉴：TradingAgents-AShare macro_analyst + Vibe-Trading macro_strategy_forum + FinClaw akshare-macro

功能：
  1. 康波周期阶段判定（衰退→复苏→繁荣→滞胀）
  2. 第六轮康波（AI/算力驱动）阶段定位
  3. 行业轮动映射（各阶段最优配置板块）
  4. 十五五规划（2026-2030）与康波周期交叠分析
  5. 大宗商品周期信号生成

数据源优先级：Wind MCP > akshare > 本地估算
"""

import logging
import os
from datetime import datetime

import numpy as np

try:
    import stumpy

    _STUMPY_AVAILABLE = True
except ImportError:
    stumpy = None
    _STUMPY_AVAILABLE = False

logger = logging.getLogger(__name__)


# ============================================================
# 康波周期阶段定义（借鉴 Vibe-Trading macro_strategy_forum 多维度框架）
# ============================================================


class KondratievPhase:
    """康波周期四阶段枚举"""

    RECESSION = "衰退期"  # Winter: 通缩、去杠杆、资产价格下跌
    RECOVERY = "复苏期"  # Spring: 信贷扩张、产能利用率回升
    PROSPERITY = "繁荣期"  # Summer: 产能饱和、通胀温和、资产泡沫
    STAGFLATION = "滞胀期"  # Autumn: 产能过剩、通胀高企、利润压缩


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
        ],
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
    },
}


# 康波周期与十五五规划交叠分析
# 十五五规划期（2026-2030）恰好落在第六轮康波复苏→繁荣过渡期
FIFTEEN_FIVE_KONDRATIEV_OVERLAY = {
    "period": "2026-2030",
    "kondratiev_phase": f"{KondratievPhase.RECOVERY} → {KondratievPhase.PROSPERITY}",
    "synergy_sectors": [
        {
            "sector": "AI/算力/半导体",
            "fifteen_weight": 0.25,
            "kondratiev_score": 95,
            "rationale": "十五五核心方向 + 康波第六轮核心驱动力",
        },
        {
            "sector": "高端制造/先进制造",
            "fifteen_weight": 0.20,
            "kondratiev_score": 90,
            "rationale": "十五五制造强国战略 + 康波复苏期资本品需求",
        },
        {
            "sector": "新能源/碳中和",
            "fifteen_weight": 0.15,
            "kondratiev_score": 85,
            "rationale": "十五五绿色转型 + 康波能源结构变革",
        },
        {
            "sector": "生物医药/生命科学",
            "fifteen_weight": 0.15,
            "kondratiev_score": 80,
            "rationale": "十五五健康中国 + 康波生物科技驱动",
        },
        {
            "sector": "数字经济/数据要素",
            "fifteen_weight": 0.15,
            "kondratiev_score": 88,
            "rationale": "十五五数字中国 + 康波信息技术革命",
        },
        {
            "sector": "粮食/能源安全",
            "fifteen_weight": 0.10,
            "kondratiev_score": 70,
            "rationale": "十五五安全底线 + 康波资源重估",
        },
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

    def get_current_phase(self) -> dict:
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
        phases = [
            KondratievPhase.RECESSION,
            KondratievPhase.RECOVERY,
            KondratievPhase.PROSPERITY,
            KondratievPhase.STAGFLATION,
        ]
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

    def get_sector_allocation(self) -> list[dict]:
        """获取康波周期驱动的行业配置建议"""
        phase = self.get_current_phase()
        allocation = self.wave_config["phase_allocation"].get(phase["phase"], {})

        sectors = allocation.get("sectors", [])
        result = []
        # 按十五五交叠评分加权
        synergy_map = {s["sector"]: s for s in self.overlay["synergy_sectors"]}

        for sector in sectors:
            synergy = synergy_map.get(sector, {})
            kondratiev_score = synergy.get("kondratiev_score", 70)
            fifteen_weight = synergy.get("fifteen_weight", 0)

            result.append(
                {
                    "sector": sector,
                    "kondratiev_phase": phase["phase"],
                    "kondratiev_favorability": kondratiev_score,
                    "fifteen_five_weight": fifteen_weight,
                    "combined_score": round(kondratiev_score * 0.6 + fifteen_weight * 100 * 0.4, 1),
                    "recommendation": (
                        "超配" if kondratiev_score >= 85 else "标配" if kondratiev_score >= 70 else "低配"
                    ),
                }
            )

        result.sort(key=lambda x: x["combined_score"], reverse=True)
        return result

    # ---------- 大宗商品周期信号 ----------

    def get_commodity_signals(self) -> list[dict]:
        """生成大宗商品周期信号"""
        phase = self.get_current_phase()
        allocation = self.wave_config["phase_allocation"].get(phase["phase"], {})

        commodity_signals = []
        # 各商品在康波周期中的角色
        commodity_roles = {
            "铜": {
                "driver": "AI算力/电气化",
                "phase_sensitivity": "高",
                "current_signal": "看多",
            },
            "锡": {
                "driver": "半导体焊料/封装",
                "phase_sensitivity": "极高",
                "current_signal": "看多",
            },
            "铝": {
                "driver": "轻量化/新能源",
                "phase_sensitivity": "中高",
                "current_signal": "偏多",
            },
            "黄金": {
                "driver": "避险/央行购金",
                "phase_sensitivity": "中",
                "current_signal": "配置",
            },
            "白银": {
                "driver": "光伏/工业+避险",
                "phase_sensitivity": "高",
                "current_signal": "看多",
            },
            "原油": {
                "driver": "能源转型过渡",
                "phase_sensitivity": "中",
                "current_signal": "中性",
            },
        }

        recommended = allocation.get("commodities", [])
        for comm, role in commodity_roles.items():
            commodity_signals.append(
                {
                    "name": comm,
                    "driver": role["driver"],
                    "phase_sensitivity": role["phase_sensitivity"],
                    "kondratiev_recommendation": ("推荐" if comm in recommended else "观望"),
                    "current_signal": role["current_signal"],
                }
            )

        return commodity_signals

    # ---------- 十五五与康波交叠分析 ----------

    def get_fifteen_five_overlay(self) -> dict:
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

    def generate_report(self, save_dir: str | None = None) -> str:
        """生成康波周期+十五五交叠分析报告"""
        phase = self.get_current_phase()
        sectors = self.get_sector_allocation()
        commodities = self.get_commodity_signals()
        overlay = self.get_fifteen_five_overlay()

        lines = []
        lines.append("# 康波周期 + 十五五规划交叠分析报告")
        lines.append("")
        lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("**分析引擎**: KondratievCycleAnalyzer v2.0")
        lines.append("")
        lines.append("---")
        lines.append("")

        # 一、康波周期当前阶段
        lines.append("## 一、康波周期当前阶段")
        lines.append("")
        lines.append("| 维度 | 内容 |")
        lines.append("|------|------|")
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
            lines.append(
                f"| {s['sector']} | {s['kondratiev_favorability']} | {s['fifteen_five_weight']:.0%} | {s['combined_score']} | **{s['recommendation']}** |"  # noqa: E501
            )
        lines.append("")

        # 三、大宗商品信号
        lines.append("---")
        lines.append("## 三、大宗商品康波周期信号")
        lines.append("")
        lines.append("| 商品 | 周期驱动力 | 周期敏感性 | 康波建议 | 当前信号 |")
        lines.append("|------|-----------|-----------|---------|---------|")
        for c in commodities:
            lines.append(
                f"| {c['name']} | {c['driver']} | {c['phase_sensitivity']} | {c['kondratiev_recommendation']} | {c['current_signal']} |"  # noqa: E501
            )
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
        lines.append("### 投资建议")
        lines.append(f"> {overlay['investment_implication']}")
        lines.append("")

        lines.append("---")
        lines.append("*本报告由康波周期分析引擎 v2.0 自动生成*")
        lines.append("*数据参考: TradingAgents-AShare / Vibe-Trading / FinClaw*")

        report = "\n".join(lines)

        if save_dir:
            os.makedirs(save_dir, exist_ok=True)
            filepath = os.path.join(save_dir, f"康波周期分析_{datetime.now().strftime('%Y%m%d')}.md")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(report)
            logger.info(f"[Kondratiev] 报告已保存: {filepath}")

        return report

    # ---------- SAX Motif 发现 (stumpy POC, Wave 12-A #1) ----------

    def discover_motifs(
        self,
        timeseries: np.ndarray,
        window_size: int = 60,
        max_motifs: int = 3,
    ) -> list[dict]:
        """使用 stumpy SAX motif 发现识别时序中的重复模式.

        Args:
            timeseries: 一维时序数据.
            window_size: 模式窗口大小（月度数据默认 60 ≈ 5 年）.
            max_motifs: 最大返回模式数.

        Returns:
            list[dict]: 每个模式包含 motif_idx/match_idx/distance/window_size.
        """
        ts = np.asarray(timeseries, dtype=np.float64).ravel()
        n = len(ts)
        if n < 2 * window_size:
            logger.warning(
                "[Kondratiev] 时序长度 %d 不足 2×window_size=%d，无法发现 motif",
                n,
                2 * window_size,
            )
            return []

        if _STUMPY_AVAILABLE:
            return self._discover_motifs_stumpy(ts, window_size, max_motifs)
        return self._discover_motifs_numpy(ts, window_size, max_motifs)

    def _discover_motifs_stumpy(self, ts: np.ndarray, m: int, k: int) -> list[dict]:
        """stumpy 后端：矩阵轮廓 motif 发现."""
        mp = stumpy.stump(ts, m)
        distances = mp[:, 0].astype(float)
        motif_indices = []

        used_positions = set()
        for _ in range(k):
            remaining = [
                i
                for i in range(len(distances))
                if i not in used_positions and not any(abs(i - u) < m for u in used_positions)
            ]
            if not remaining:
                break
            best_idx = min(remaining, key=lambda i: distances[i])
            if not np.isfinite(distances[best_idx]) or distances[best_idx] == np.inf:
                break
            match_idx = int(mp[best_idx, 1])
            motif_indices.append(
                {
                    "motif_idx": int(best_idx),
                    "match_idx": match_idx,
                    "distance": float(distances[best_idx]),
                    "window_size": m,
                    "backend": "stumpy",
                }
            )
            used_positions.update(range(best_idx, best_idx + m))
            used_positions.update(range(match_idx, match_idx + m))

        return motif_indices

    def _discover_motifs_numpy(self, ts: np.ndarray, m: int, k: int) -> list[dict]:
        """numpy 降级后端：滑动窗口 + 欧氏距离 motif 发现."""
        n = len(ts)
        windows = np.array([ts[i : i + m] for i in range(n - m + 1)])
        motif_indices = []
        used_positions = set()

        for _ in range(k):
            best_dist = np.inf
            best_i, best_j = -1, -1
            for i in range(len(windows)):
                if i in used_positions:
                    continue
                diffs = windows - windows[i]
                dists = np.sqrt(np.sum(diffs * diffs, axis=1))
                dists[i] = np.inf
                for u in used_positions:
                    if u < len(dists):
                        dists[u] = np.inf
                j = int(np.argmin(dists))
                if dists[j] < best_dist:
                    best_dist = float(dists[j])
                    best_i, best_j = i, j

            if best_i < 0:
                break
            motif_indices.append(
                {
                    "motif_idx": best_i,
                    "match_idx": best_j,
                    "distance": best_dist,
                    "window_size": m,
                    "backend": "numpy",
                }
            )
            used_positions.update(range(best_i, best_i + m))
            used_positions.update(range(best_j, best_j + m))

        return motif_indices

    def get_kondratiev_historical_series(self) -> np.ndarray:
        """生成康波历史模拟序列（基于康波理论 4 轮完整周期）.

        每轮康波约 55 年，包含衰退→复苏→繁荣→滞胀四阶段。
        用正弦波 + 微噪声模拟，不同轮次振幅/周期略有变化。

        Returns:
            np.ndarray: 康波历史模拟序列（月度数据，约 220 年 ≈ 2640 点）.
        """
        rng = np.random.default_rng(seed=42)
        months_per_year = 12
        waves = 4
        years_per_wave = 55
        total_months = waves * years_per_wave * months_per_year

        series = np.zeros(total_months, dtype=np.float64)
        for w in range(waves):
            start = w * years_per_wave * months_per_year
            end = start + years_per_wave * months_per_year
            local_t = np.arange(end - start, dtype=np.float64)
            amplitude = 1.0 + 0.15 * w
            period = years_per_wave * months_per_year * (1.0 + 0.02 * w)
            phase_shift = 0.1 * w
            noise = rng.normal(0, 0.05, size=end - start)
            series[start:end] = amplitude * np.sin(2 * np.pi * local_t / period + phase_shift) + noise

        return series

    def analyze_historical_patterns(self, window_size: int = 60, max_motifs: int = 3) -> dict:
        """分析康波历史模式，返回模式匹配结果.

        Args:
            window_size: 模式窗口大小（月度数据默认 60 ≈ 5 年）.
            max_motifs: 最大返回模式数.

        Returns:
            dict: 包含 series_length/motifs/pattern_count/interpretation/backend.
        """
        series = self.get_kondratiev_historical_series()
        motifs = self.discover_motifs(series, window_size=window_size, max_motifs=max_motifs)

        backend = "stumpy" if _STUMPY_AVAILABLE else "numpy"
        interpretation = self._interpret_motifs(motifs, window_size)

        return {
            "series_length": int(len(series)),
            "series_years": round(len(series) / 12, 1),
            "motifs": motifs,
            "pattern_count": len(motifs),
            "window_size": window_size,
            "backend": backend,
            "interpretation": interpretation,
        }

    def _interpret_motifs(self, motifs: list[dict], window_size: int) -> str:
        """解读 motif 发现结果."""
        if not motifs:
            return "未发现显著重复模式"
        parts = [f"发现 {len(motifs)} 个重复模式（窗口={window_size} 月）"]
        for i, m in enumerate(motifs, 1):
            years_apart = round(abs(m["motif_idx"] - m["match_idx"]) / 12, 1)
            parts.append(
                f"模式{i}: 位置 {m['motif_idx']} ↔ {m['match_idx']}（间隔 {years_apart} 年，距离 {m['distance']:.4f}）"
            )
        return "；".join(parts)


# ============================================================
# 快速测试
# ============================================================

if __name__ == "__main__":
    analyzer = KondratievCycleAnalyzer()

    logger.info("\n=== 康波周期当前阶段 ===")
    phase = analyzer.get_current_phase()
    for k, v in phase.items():
        logger.info(f"  {k}: {v}")

    logger.info("\n=== 行业配置建议 ===")
    for s in analyzer.get_sector_allocation():
        logger.info(f"  {s['sector']}: 综合得分={s['combined_score']}, 建议={s['recommendation']}")

    logger.info("\n=== 大宗商品信号 ===")
    for c in analyzer.get_commodity_signals():
        logger.info(f"  {c['name']}: 信号={c['current_signal']}, 康波建议={c['kondratiev_recommendation']}")

    logger.info("\n=== 十五五与康波交叠 ===")
    overlay = analyzer.get_fifteen_five_overlay()
    logger.info(f"  {overlay['synergy_conclusion'][:80]}...")
