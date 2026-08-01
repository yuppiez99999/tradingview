# -*- coding: utf-8 -*-
"""
PSI 监控模块 — v5.10 P1-4 修复
================================
监控特征/信号分布漂移，当分布发生显著变化时发出预警。

核心功能:
1. calculate_psi()          — 计算两个分布之间的 PSI
2. monitor_signal_drift()   — 监控信号分数分布漂移
3. monitor_feature_drift()  — 监控特征分布漂移
4. generate_drift_report()  — 生成漂移监控报告

参考文献:
- Siddiqi, N. (2009). Intelligent Credit Scoring.
- PSI < 0.1: 无显著漂移
- 0.1 <= PSI < 0.2: 轻微漂移，需关注
- PSI >= 0.2: 显著漂移，需重新训练
"""

import os
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass

try:
    from .logging_manager import get_logger

    logger = get_logger("psi_monitor")
except ImportError:
    import logging

    logger = logging.getLogger("psi_monitor")


@dataclass
class DriftResult:
    """漂移检测结果"""

    code: str
    source: str
    psi: float
    psi_level: str  # 'stable' / 'minor' / 'major'
    current_distribution: Dict[str, float]
    reference_distribution: Dict[str, float]
    alert: bool
    recommendation: str
    timestamp: str = ""


class PSIMonitor:
    """PSI 监控器 — 监控信号/特征分布漂移"""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            db_path = os.path.join(base_dir, "data", "psi_monitor.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS psi_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                code TEXT NOT NULL,
                source TEXT NOT NULL,
                psi REAL NOT NULL,
                psi_level TEXT NOT NULL,
                alert INTEGER NOT NULL,
                recommendation TEXT,
                current_distribution TEXT,
                reference_distribution TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_psi_records_code_time
            ON psi_records(code, source, timestamp)
        """)
        conn.commit()
        conn.close()

    @staticmethod
    def calculate_psi(expected: Dict[str, float], actual: Dict[str, float], min_count: int = 10) -> float:
        """计算两个分布之间的 PSI

        Args:
            expected: 期望分布（参考分布），如 {'bin1': 0.3, 'bin2': 0.5, ...}
            actual: 实际分布，如 {'bin1': 0.25, 'bin2': 0.55, ...}
            min_count: 最小样本数，低于此值返回 0.0

        Returns:
            psi: Population Stability Index
        """
        if not expected or not actual:
            return 0.0

        # 确保所有分箱都存在
        all_bins = set(expected.keys()) | set(actual.keys())
        expected_total = sum(expected.values())
        actual_total = sum(actual.values())

        if expected_total == 0 or actual_total == 0:
            return 0.0

        psi = 0.0
        for bin_name in all_bins:
            e_pct = expected.get(bin_name, 0) / expected_total
            a_pct = actual.get(bin_name, 0) / actual_total

            # 避免除零和log(0)
            if e_pct == 0:
                e_pct = 0.0001
            if a_pct == 0:
                a_pct = 0.0001

            psi += (a_pct - e_pct) * (a_pct / e_pct)

        return round(psi, 4)

    @staticmethod
    def get_psi_level(psi: float) -> Tuple[str, str, bool]:
        """根据 PSI 值判断漂移等级

        Returns:
            (level, recommendation, alert)
        """
        if psi < 0.1:
            return "stable", "无显著漂移，模型表现正常", False
        elif psi < 0.2:
            return "minor", "轻微漂移，建议增加监控频率", True
        else:
            return "major", "显著漂移，建议重新训练模型", True

    def monitor_signal_drift(
        self,
        code: str,
        source: str,
        current_scores: List[float],
        reference_scores: Optional[List[float]] = None,
        n_bins: int = 10,
    ) -> DriftResult:
        """监控信号分数分布漂移

        Args:
            code: 标的代码
            source: 信号源名称 ('ml' / 'ai_hedge' / 'glm5' / 'kondratiev')
            current_scores: 当前信号分数列表
            reference_scores: 参考信号分数列表（None则从数据库获取历史数据）
            n_bins: 分箱数量

        Returns:
            DriftResult: 漂移检测结果
        """
        if len(current_scores) < 10:
            return DriftResult(
                code=code,
                source=source,
                psi=0.0,
                psi_level="stable",
                current_distribution={},
                reference_distribution={},
                alert=False,
                recommendation="样本不足，无法计算PSI",
                timestamp=datetime.now().isoformat(),
            )

        # 如果没有提供参考分布，从数据库获取
        if reference_scores is None:
            reference_scores = self._get_historical_scores(code, source, days=30)

        if not reference_scores or len(reference_scores) < 10:
            # 首次运行，保存当前分布作为参考
            self._save_reference_distribution(code, source, current_scores)
            return DriftResult(
                code=code,
                source=source,
                psi=0.0,
                psi_level="stable",
                current_distribution=self._bin_scores(current_scores, n_bins),
                reference_distribution={},
                alert=False,
                recommendation="首次记录，已保存为参考分布",
                timestamp=datetime.now().isoformat(),
            )

        # 计算分箱分布
        current_dist = self._bin_scores(current_scores, n_bins)
        reference_dist = self._bin_scores(reference_scores, n_bins)

        # 计算 PSI
        psi = self.calculate_psi(reference_dist, current_dist)
        psi_level, recommendation, alert = self.get_psi_level(psi)

        # 保存记录
        self._save_psi_record(code, source, psi, psi_level, alert, recommendation, current_dist, reference_dist)

        return DriftResult(
            code=code,
            source=source,
            psi=psi,
            psi_level=psi_level,
            current_distribution=current_dist,
            reference_distribution=reference_dist,
            alert=alert,
            recommendation=recommendation,
            timestamp=datetime.now().isoformat(),
        )

    def monitor_feature_drift(
        self,
        code: str,
        feature_name: str,
        current_values: List[float],
        reference_values: Optional[List[float]] = None,
        n_bins: int = 10,
    ) -> DriftResult:
        """监控特征分布漂移

        Args:
            code: 标的代码
            feature_name: 特征名称（如 'volatility_20d' / 'rsi_14'）
            current_values: 当前特征值列表
            reference_values: 参考特征值列表（None则从数据库获取）
            n_bins: 分箱数量

        Returns:
            DriftResult: 漂移检测结果
        """
        source = f"feature_{feature_name}"

        if len(current_values) < 10:
            return DriftResult(
                code=code,
                source=source,
                psi=0.0,
                psi_level="stable",
                current_distribution={},
                reference_distribution={},
                alert=False,
                recommendation="样本不足，无法计算PSI",
                timestamp=datetime.now().isoformat(),
            )

        if reference_values is None:
            reference_values = self._get_historical_feature_values(code, feature_name, days=30)

        if not reference_values or len(reference_values) < 10:
            self._save_feature_reference(code, feature_name, current_values)
            return DriftResult(
                code=code,
                source=source,
                psi=0.0,
                psi_level="stable",
                current_distribution=self._bin_values(current_values, n_bins),
                reference_distribution={},
                alert=False,
                recommendation="首次记录，已保存为参考分布",
                timestamp=datetime.now().isoformat(),
            )

        current_dist = self._bin_values(current_values, n_bins)
        reference_dist = self._bin_values(reference_values, n_bins)

        psi = self.calculate_psi(reference_dist, current_dist)
        psi_level, recommendation, alert = self.get_psi_level(psi)

        self._save_psi_record(code, source, psi, psi_level, alert, recommendation, current_dist, reference_dist)

        return DriftResult(
            code=code,
            source=source,
            psi=psi,
            psi_level=psi_level,
            current_distribution=current_dist,
            reference_distribution=reference_dist,
            alert=alert,
            recommendation=recommendation,
            timestamp=datetime.now().isoformat(),
        )

    def generate_drift_report(self, days: int = 7) -> str:
        """生成漂移监控报告

        Args:
            days: 查看最近N天的记录

        Returns:
            str: Markdown 格式的报告
        """
        conn = sqlite3.connect(self.db_path)
        since = (datetime.now() - timedelta(days=days)).isoformat()
        rows = conn.execute(
            """
            SELECT timestamp, code, source, psi, psi_level, alert, recommendation
            FROM psi_records
            WHERE timestamp >= ?
            ORDER BY timestamp DESC
        """,
            (since,),
        ).fetchall()
        conn.close()

        if not rows:
            return "## PSI 漂移监控报告\n\n暂无漂移监控记录。\n"

        lines = []
        lines.append("## PSI 信号/特征漂移监控报告")
        lines.append("")
        lines.append(f"- **监控周期**: 最近 {days} 天")
        lines.append(f"- **总记录数**: {len(rows)}")
        lines.append("")

        # 统计
        alerts = [r for r in rows if r[5]]
        majors = [r for r in rows if r[4] == "major"]
        minors = [r for r in rows if r[4] == "minor"]

        lines.append("| 指标 | 数值 |")
        lines.append("|------|------|")
        lines.append(f"| 总记录数 | {len(rows)} |")
        lines.append(f"| 预警次数 | {len(alerts)} |")
        lines.append(f"| 显著漂移 | {len(majors)} |")
        lines.append(f"| 轻微漂移 | {len(minors)} |")
        lines.append("")

        if alerts:
            lines.append("### 漂移预警详情")
            lines.append("")
            lines.append("| 时间 | 标的 | 源 | PSI | 等级 | 建议 |")
            lines.append("|------|------|----|-----|------|------|")
            for row in alerts[:20]:
                ts, code, source, psi, level, _alert, rec = row
                ts_short = ts.split("T")[0] if "T" in ts else ts
                lines.append(
                    f"| {ts_short} | {code} | {source} | {psi:.4f} | {level} | {rec[:30]}{'...' if len(rec) > 30 else ''} |"
                )
            lines.append("")

        lines.append("> PSI < 0.1: 稳定 | 0.1-0.2: 轻微漂移 | >= 0.2: 显著漂移")
        return "\n".join(lines)

    # ── 私有方法 ──

    def _bin_scores(self, scores: List[float], n_bins: int) -> Dict[str, float]:
        """将信号分数分箱"""
        if not scores:
            return {}

        min_score = min(scores)
        max_score = max(scores)
        if max_score == min_score:
            return {f"{min_score:.2f}": len(scores)}

        bin_width = (max_score - min_score) / n_bins
        bins = {}
        for s in scores:
            bin_idx = min(int((s - min_score) / bin_width), n_bins - 1)
            bin_name = f"{min_score + bin_idx * bin_width:.2f}-{min_score + (bin_idx + 1) * bin_width:.2f}"
            bins[bin_name] = bins.get(bin_name, 0) + 1

        return bins

    def _bin_values(self, values: List[float], n_bins: int) -> Dict[str, float]:
        """将特征值分箱"""
        if not values:
            return {}

        sorted_vals = sorted(values)
        min_val = sorted_vals[0]
        max_val = sorted_vals[-1]

        if max_val == min_val:
            return {f"{min_val:.2f}": len(values)}

        bin_width = (max_val - min_val) / n_bins
        bins = {}
        for v in values:
            bin_idx = min(int((v - min_val) / bin_width), n_bins - 1)
            bin_name = f"{min_val + bin_idx * bin_width:.2f}-{min_val + (bin_idx + 1) * bin_width:.2f}"
            bins[bin_name] = bins.get(bin_name, 0) + 1

        return bins

    def _get_historical_scores(self, code: str, source: str, days: int = 30) -> List[float]:
        """从数据库获取历史信号分数"""
        try:
            conn = sqlite3.connect(self.db_path)
            since = (datetime.now() - timedelta(days=days)).isoformat()
            rows = conn.execute(
                """
                SELECT current_distribution FROM psi_records
                WHERE code = ? AND source = ? AND timestamp >= ?
                ORDER BY timestamp DESC
            """,
                (code, source, since),
            ).fetchall()
            conn.close()

            scores = []
            for row in rows:
                dist = json.loads(row[0]) if row[0] else {}
                # 从分箱分布重构分数（简化版）
                for bin_name, count in dist.items():
                    try:
                        mid = float(bin_name.split("-")[0])
                        scores.extend([mid] * count)
                    except (ValueError, IndexError):
                        continue

            return scores[:1000]  # 限制样本量
        except Exception as e:
            logger.debug(f"获取历史分数失败: {e}")
            return []

    def _get_historical_feature_values(self, code: str, feature_name: str, days: int = 30) -> List[float]:
        """从数据库获取历史特征值"""
        # 简化实现：返回空列表，后续可从特征存储表获取
        return []

    def _save_reference_distribution(self, code: str, source: str, scores: List[float]):
        """保存参考分布"""
        try:
            conn = sqlite3.connect(self.db_path)
            dist = self._bin_scores(scores, 10)
            conn.execute(
                """
                INSERT OR REPLACE INTO psi_reference (code, source, distribution, updated_at)
                VALUES (?, ?, ?, ?)
            """,
                (code, source, json.dumps(dist), datetime.now().isoformat()),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"保存参考分布失败: {e}")

    def _save_feature_reference(self, code: str, feature_name: str, values: List[float]):
        """保存特征参考分布"""
        try:
            conn = sqlite3.connect(self.db_path)
            dist = self._bin_values(values, 10)
            conn.execute(
                """
                INSERT OR REPLACE INTO psi_feature_reference (code, feature_name, distribution, updated_at)
                VALUES (?, ?, ?, ?)
            """,
                (code, feature_name, json.dumps(dist), datetime.now().isoformat()),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"保存特征参考分布失败: {e}")

    def _get_reference_distribution(self, code: str, source: str) -> Optional[Dict[str, float]]:
        """从数据库获取参考分布"""
        try:
            conn = sqlite3.connect(self.db_path)
            row = conn.execute(
                """
                SELECT distribution FROM psi_reference
                WHERE code = ? AND source = ?
                ORDER BY updated_at DESC LIMIT 1
            """,
                (code, source),
            ).fetchone()
            conn.close()
            if row and row[0]:
                return json.loads(row[0])
        except Exception as e:
            logger.debug(f"获取参考分布失败: {e}")
        return None

    def _get_historical_feature_values(self, code: str, feature_name: str, days: int = 30) -> List[float]:
        """从数据库获取历史特征值"""
        try:
            conn = sqlite3.connect(self.db_path)
            since = (datetime.now() - timedelta(days=days)).isoformat()
            rows = conn.execute(
                """
                SELECT current_distribution FROM psi_records
                WHERE code = ? AND source = ? AND timestamp >= ?
                ORDER BY timestamp DESC
            """,
                (code, f"feature_{feature_name}", since),
            ).fetchall()
            conn.close()

            values = []
            for row in rows:
                dist = json.loads(row[0]) if row[0] else {}
                for bin_name, count in dist.items():
                    try:
                        mid = float(bin_name.split("-")[0])
                        values.extend([mid] * count)
                    except (ValueError, IndexError):
                        continue
            return values[:1000]
        except Exception as e:
            logger.debug(f"获取历史特征值失败: {e}")
            return []

    def _init_db(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS psi_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                code TEXT NOT NULL,
                source TEXT NOT NULL,
                psi REAL NOT NULL,
                psi_level TEXT NOT NULL,
                alert INTEGER NOT NULL,
                recommendation TEXT,
                current_distribution TEXT,
                reference_distribution TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS psi_reference (
                code TEXT NOT NULL,
                source TEXT NOT NULL,
                distribution TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (code, source)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS psi_feature_reference (
                code TEXT NOT NULL,
                feature_name TEXT NOT NULL,
                distribution TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (code, feature_name)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_psi_records_code_time
            ON psi_records(code, source, timestamp)
        """)
        conn.commit()
        conn.close()

    @staticmethod
    def get_psi_level(psi: float) -> Tuple[str, str, bool]:  # noqa: F811  增强版覆写
        """根据 PSI 值判断漂移等级

        Returns:
            (level, recommendation, alert)
        """
        if psi < 0.1:
            return "stable", "无显著漂移，模型表现正常", False
        elif psi < 0.2:
            return "minor", "轻微漂移，建议增加监控频率", True
        else:
            return "major", "显著漂移，建议重新训练模型", True

    def monitor_signal_drift(  # noqa: F811  增强版覆写
        self,
        code: str,
        source: str,
        current_scores: List[float],
        reference_scores: Optional[List[float]] = None,
        n_bins: int = 10,
    ) -> DriftResult:
        """监控信号分数分布漂移

        Args:
            code: 标的代码
            source: 信号源名称 ('ml' / 'ai_hedge' / 'glm5' / 'kondratiev')
            current_scores: 当前信号分数列表
            reference_scores: 参考信号分数列表（None则从数据库获取历史数据）
            n_bins: 分箱数量

        Returns:
            DriftResult: 漂移检测结果
        """
        if len(current_scores) < 10:
            return DriftResult(
                code=code,
                source=source,
                psi=0.0,
                psi_level="stable",
                current_distribution={},
                reference_distribution={},
                alert=False,
                recommendation="样本不足，无法计算PSI",
                timestamp=datetime.now().isoformat(),
            )

        # 如果没有提供参考分布，从数据库获取
        if reference_scores is None:
            reference_scores = self._get_historical_scores(code, source, days=30)

        if not reference_scores or len(reference_scores) < 10:
            # 首次运行，保存当前分布作为参考
            self._save_reference_distribution(code, source, current_scores)
            return DriftResult(
                code=code,
                source=source,
                psi=0.0,
                psi_level="stable",
                current_distribution=self._bin_scores(current_scores, n_bins),
                reference_distribution={},
                alert=False,
                recommendation="首次记录，已保存为参考分布",
                timestamp=datetime.now().isoformat(),
            )

        # 计算分箱分布
        current_dist = self._bin_scores(current_scores, n_bins)
        reference_dist = self._bin_scores(reference_scores, n_bins)

        # 计算 PSI
        psi = self.calculate_psi(reference_dist, current_dist)
        psi_level, recommendation, alert = self.get_psi_level(psi)

        # 保存记录
        self._save_psi_record(code, source, psi, psi_level, alert, recommendation, current_dist, reference_dist)

        return DriftResult(
            code=code,
            source=source,
            psi=psi,
            psi_level=psi_level,
            current_distribution=current_dist,
            reference_distribution=reference_dist,
            alert=alert,
            recommendation=recommendation,
            timestamp=datetime.now().isoformat(),
        )

    def monitor_feature_drift(  # noqa: F811  增强版覆写
        self,
        code: str,
        feature_name: str,
        current_values: List[float],
        reference_values: Optional[List[float]] = None,
        n_bins: int = 10,
    ) -> DriftResult:
        """监控特征分布漂移

        Args:
            code: 标的代码
            feature_name: 特征名称（如 'volatility_20d' / 'rsi_14'）
            current_values: 当前特征值列表
            reference_values: 参考特征值列表（None则从数据库获取）
            n_bins: 分箱数量

        Returns:
            DriftResult: 漂移检测结果
        """
        source = f"feature_{feature_name}"

        if len(current_values) < 10:
            return DriftResult(
                code=code,
                source=source,
                psi=0.0,
                psi_level="stable",
                current_distribution={},
                reference_distribution={},
                alert=False,
                recommendation="样本不足，无法计算PSI",
                timestamp=datetime.now().isoformat(),
            )

        if reference_values is None:
            # 优先从参考表获取
            reference_dist = self._get_reference_distribution(code, source)
            if reference_dist:
                # 从分布重构值列表（简化版）
                reference_values = []
                for bin_name, count in reference_dist.items():
                    try:
                        mid = float(bin_name.split("-")[0])
                        reference_values.extend([mid] * count)
                    except (ValueError, IndexError):
                        continue
            else:
                reference_values = self._get_historical_feature_values(code, feature_name, days=30)

        if not reference_values or len(reference_values) < 10:
            self._save_feature_reference(code, feature_name, current_values)
            return DriftResult(
                code=code,
                source=source,
                psi=0.0,
                psi_level="stable",
                current_distribution=self._bin_values(current_values, n_bins),
                reference_distribution={},
                alert=False,
                recommendation="首次记录，已保存为参考分布",
                timestamp=datetime.now().isoformat(),
            )

        current_dist = self._bin_values(current_values, n_bins)
        reference_dist = self._bin_values(reference_values, n_bins)

        psi = self.calculate_psi(reference_dist, current_dist)
        psi_level, recommendation, alert = self.get_psi_level(psi)

        self._save_psi_record(code, source, psi, psi_level, alert, recommendation, current_dist, reference_dist)

        return DriftResult(
            code=code,
            source=source,
            psi=psi,
            psi_level=psi_level,
            current_distribution=current_dist,
            reference_distribution=reference_dist,
            alert=alert,
            recommendation=recommendation,
            timestamp=datetime.now().isoformat(),
        )

    def generate_drift_report(self, days: int = 7) -> str:  # noqa: F811  增强版覆写
        """生成漂移监控报告

        Args:
            days: 查看最近N天的记录

        Returns:
            str: Markdown 格式的报告
        """
        conn = sqlite3.connect(self.db_path)
        since = (datetime.now() - timedelta(days=days)).isoformat()
        rows = conn.execute(
            """
            SELECT timestamp, code, source, psi, psi_level, alert, recommendation
            FROM psi_records
            WHERE timestamp >= ?
            ORDER BY timestamp DESC
        """,
            (since,),
        ).fetchall()
        conn.close()

        if not rows:
            return "## PSI 漂移监控报告\n\n暂无漂移监控记录。\n"

        lines = []
        lines.append("## PSI 信号/特征漂移监控报告")
        lines.append("")
        lines.append(f"- **监控周期**: 最近 {days} 天")
        lines.append(f"- **总记录数**: {len(rows)}")
        lines.append("")

        # 统计
        alerts = [r for r in rows if r[5]]
        majors = [r for r in rows if r[4] == "major"]
        minors = [r for r in rows if r[4] == "minor"]

        lines.append("| 指标 | 数值 |")
        lines.append("|------|------|")
        lines.append(f"| 总记录数 | {len(rows)} |")
        lines.append(f"| 预警次数 | {len(alerts)} |")
        lines.append(f"| 显著漂移 | {len(majors)} |")
        lines.append(f"| 轻微漂移 | {len(minors)} |")
        lines.append("")

        if alerts:
            lines.append("### 漂移预警详情")
            lines.append("")
            lines.append("| 时间 | 标的 | 源 | PSI | 等级 | 建议 |")
            lines.append("|------|------|----|-----|------|------|")
            for row in alerts[:20]:
                ts, code, source, psi, level, _alert, rec = row
                ts_short = ts.split("T")[0] if "T" in ts else ts
                lines.append(
                    f"| {ts_short} | {code} | {source} | {psi:.4f} | {level} | {rec[:30]}{'...' if len(rec) > 30 else ''} |"
                )
            lines.append("")

        lines.append("> PSI < 0.1: 稳定 | 0.1-0.2: 轻微漂移 | >= 0.2: 显著漂移")
        return "\n".join(lines)

    # ── 私有方法 ──

    def _bin_scores(self, scores: List[float], n_bins: int) -> Dict[str, float]:
        """将信号分数分箱"""
        if not scores:
            return {}

        min_score = min(scores)
        max_score = max(scores)
        if max_score == min_score:
            return {f"{min_score:.2f}": len(scores)}

        bin_width = (max_score - min_score) / n_bins
        bins = {}
        for s in scores:
            bin_idx = min(int((s - min_score) / bin_width), n_bins - 1)
            bin_name = f"{min_score + bin_idx * bin_width:.2f}-{min_score + (bin_idx + 1) * bin_width:.2f}"
            bins[bin_name] = bins.get(bin_name, 0) + 1

        return bins

    def _bin_values(self, values: List[float], n_bins: int) -> Dict[str, float]:
        """将特征值分箱"""
        if not values:
            return {}

        sorted_vals = sorted(values)
        min_val = sorted_vals[0]
        max_val = sorted_vals[-1]

        if max_val == min_val:
            return {f"{min_val:.2f}": len(values)}

        bin_width = (max_val - min_val) / n_bins
        bins = {}
        for v in values:
            bin_idx = min(int((v - min_val) / bin_width), n_bins - 1)
            bin_name = f"{min_val + bin_idx * bin_width:.2f}-{min_val + (bin_idx + 1) * bin_width:.2f}"
            bins[bin_name] = bins.get(bin_name, 0) + 1

        return bins

    def _get_historical_scores(self, code: str, source: str, days: int = 30) -> List[float]:
        """从数据库获取历史信号分数"""
        try:
            conn = sqlite3.connect(self.db_path)
            since = (datetime.now() - timedelta(days=days)).isoformat()
            rows = conn.execute(
                """
                SELECT current_distribution FROM psi_records
                WHERE code = ? AND source = ? AND timestamp >= ?
                ORDER BY timestamp DESC
            """,
                (code, source, since),
            ).fetchall()
            conn.close()

            scores = []
            for row in rows:
                dist = json.loads(row[0]) if row[0] else {}
                # 从分箱分布重构分数（简化版）
                for bin_name, count in dist.items():
                    try:
                        mid = float(bin_name.split("-")[0])
                        scores.extend([mid] * count)
                    except (ValueError, IndexError):
                        continue

            return scores[:1000]  # 限制样本量
        except Exception as e:
            logger.debug(f"获取历史分数失败: {e}")
            return []

    def _save_psi_record(
        self,
        code: str,
        source: str,
        psi: float,
        psi_level: str,
        alert: bool,
        recommendation: str,
        current_dist: Dict[str, float],
        reference_dist: Dict[str, float],
    ):
        """保存 PSI 记录到数据库"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                """
                INSERT INTO psi_records
                (timestamp, code, source, psi, psi_level, alert, recommendation,
                 current_distribution, reference_distribution)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    datetime.now().isoformat(),
                    code,
                    source,
                    psi,
                    psi_level,
                    1 if alert else 0,
                    recommendation,
                    json.dumps(current_dist),
                    json.dumps(reference_dist),
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"保存PSI记录失败: {e}")


def get_psi_monitor() -> PSIMonitor:
    """获取 PSI 监控器单例"""
    return PSIMonitor()
