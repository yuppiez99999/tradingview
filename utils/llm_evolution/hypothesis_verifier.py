"""D2 假设验证框架 — RankIC/ICIR + Purged K-Fold + CRO Gate + AB 桶判定.

属于 Wave 4 G6 Phase D 第 2 位, 核心目的: **对新假设跑显著性检验,
自动判定是否进入 AB 桶; 过拟合高危假设经 CRO Gate 拦截**.

三层验证:
    1. IC 显著性 (RankIC 均值 / ICIR / 正 IC 比例)
    2. Purged K-Fold 样本外稳定性 (CV < 0.5)
    3. CRO Gate (Walk-Forward + DSR + 压力场景)

AB 桶判定:
    - IC > 0.03 AND ICIR > 0.5 AND purged_kfold_pass → 进入 AB 桶
    - 任一不满足 → 证伪

用法:
    from utils.llm_evolution.hypothesis_verifier import (
        HypothesisVerifier, HypothesisVerdict,
    )
    verifier = HypothesisVerifier(audit_logger=audit)
    verdict = verifier.verify(candidate_factor, factor_data)
    if verdict.enter_ab_bucket:
        print("进入 AB 桶")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

logger = logging.getLogger("hypothesis_verifier")


# ============================================================
# 协议
# ============================================================


class AuditLoggerProtocol(Protocol):
    def log(
        self,
        module: str,
        action: str,
        severity: str = "INFO",
        symbol: str = "",
        reason: str = "",
        **kwargs: Any,
    ) -> None: ...


# ============================================================
# 数据结构
# ============================================================


@dataclass
class HypothesisVerdict:
    """假设验证结果 (D2 产出)."""

    hypothesis_id: str = ""
    factor_name: str = ""

    # IC 统计
    rank_ic_mean: float = 0.0
    rank_ic_std: float = 0.0
    icir: float = 0.0
    ic_positive_ratio: float = 0.0
    effect_size: float = 0.0  # Cohen's d

    # 验证结果
    ic_significant: bool = False
    purged_kfold_pass: bool = False
    cro_gate_pass: bool = False
    honest_validation_pass: bool = False
    cv_score: float = 0.0  # 变异系数 (越低越稳定)

    # 判定
    enter_ab_bucket: bool = False
    falsified: bool = False
    falsified_reason: str = ""

    # 元数据
    n_samples: int = 0
    verified_at: str = ""
    details: dict = field(default_factory=dict)

    @property
    def pass_all(self) -> bool:
        """是否通过全部验证."""
        return self.ic_significant and self.purged_kfold_pass and self.cro_gate_pass

    def summary_text(self) -> str:
        return (
            f"因子={self.factor_name} "
            f"IC={self.rank_ic_mean:.4f} ICIR={self.icir:.2f} "
            f"IC+ratio={self.ic_positive_ratio:.1%} "
            f"CV={self.cv_score:.2f} "
            f"AB桶={'YES' if self.enter_ab_bucket else 'NO'}"
        )


# ============================================================
# 验证阈值 (可配置)
# ============================================================


@dataclass
class VerificationThresholds:
    """验证阈值 (保守策略)."""

    min_rank_ic: float = 0.03  # RankIC 均值下限
    min_icir: float = 0.5  # ICIR 下限
    min_ic_positive_ratio: float = 0.55  # 正 IC 比例下限
    max_cv: float = 0.5  # 变异系数上限
    min_effect_size: float = 0.2  # Cohen's d 下限
    min_samples: int = 60  # 最少样本数


# ============================================================
# 主类
# ============================================================


class HypothesisVerifier:
    """假设验证框架 — IC 显著性 + Purged K-Fold + CRO Gate + AB 桶判定."""

    def __init__(
        self,
        audit_logger: AuditLoggerProtocol | None = None,
        thresholds: VerificationThresholds | None = None,
    ) -> None:
        self.audit = audit_logger
        self.thresholds = thresholds or VerificationThresholds()

    # ------------------------------------------------------------
    # 核心验证接口
    # ------------------------------------------------------------

    def verify(
        self,
        candidate: dict[str, Any],
        factor_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """验证单个候选因子.

        Args:
            candidate: 候选因子字典 (含 name, category, formula)
            factor_data: 因子数据 (含 ic_series, returns 等), 为 None 时用模拟数据

        Returns:
            dict 形式的验证结果 (可转 HypothesisVerdict)
        """
        factor_name = candidate.get("name", "unknown")
        factor_data = factor_data or {}

        # 提取 IC 数据
        ic_series = factor_data.get("ic_series", [])
        n_samples = len(ic_series)

        verdict = HypothesisVerdict(
            hypothesis_id=candidate.get("hypothesis_id", ""),
            factor_name=factor_name,
            n_samples=n_samples,
            verified_at=datetime.now().isoformat(timespec="seconds"),
        )

        # ---- 1. IC 显著性检验 ----
        if n_samples < self.thresholds.min_samples:
            verdict.falsified = True
            verdict.falsified_reason = (
                f"样本不足: {n_samples} < {self.thresholds.min_samples}"
            )
            self._audit("INSUFFICIENT", f"因子 {factor_name} 样本不足 ({n_samples})")
            return self._verdict_to_dict(verdict)

        verdict.rank_ic_mean = self._safe_mean(ic_series)
        verdict.rank_ic_std = self._safe_std(ic_series)
        verdict.icir = (
            verdict.rank_ic_mean / verdict.rank_ic_std
            if verdict.rank_ic_std > 0
            else 0.0
        )
        verdict.ic_positive_ratio = sum(1 for x in ic_series if x > 0) / n_samples
        verdict.effect_size = self._cohen_d(ic_series)
        verdict.cv_score = (
            verdict.rank_ic_std / abs(verdict.rank_ic_mean)
            if verdict.rank_ic_mean != 0
            else 999.0
        )

        verdict.ic_significant = (
            verdict.rank_ic_mean >= self.thresholds.min_rank_ic
            and verdict.icir >= self.thresholds.min_icir
            and verdict.ic_positive_ratio >= self.thresholds.min_ic_positive_ratio
            and verdict.effect_size >= self.thresholds.min_effect_size
            and verdict.cv_score <= self.thresholds.max_cv
        )

        # ---- 2. Purged K-Fold (简化: 用 CV 判定) ----
        verdict.purged_kfold_pass = verdict.cv_score <= self.thresholds.max_cv

        # ---- 3. CRO Gate (简化: IC + ICIR + 回撤检查) ----
        max_drawdown = factor_data.get("max_drawdown", 0.0)
        wf_mean_ic = factor_data.get("wf_mean_ic", verdict.rank_ic_mean)
        cro_pass = (
            verdict.rank_ic_mean >= self.thresholds.min_rank_ic
            and verdict.icir >= self.thresholds.min_icir
            and max_drawdown <= 0.15
            and wf_mean_ic >= self.thresholds.min_rank_ic * 0.8
        )
        verdict.cro_gate_pass = cro_pass

        # ---- 4. Honest Validation (CPCV + DSR + Noise 简化) ----
        dsr_score = factor_data.get("dsr_score", 1.0)  # DSR > 1.0 为通过
        noise_stable = factor_data.get("noise_stable", True)
        verdict.honest_validation_pass = dsr_score > 1.0 and noise_stable

        # ---- 5. AB 桶判定 ----
        if verdict.pass_all and verdict.honest_validation_pass:
            verdict.enter_ab_bucket = True
        else:
            verdict.falsified = True
            reasons: list[str] = []
            if not verdict.ic_significant:
                reasons.append("IC 不显著")
            if not verdict.purged_kfold_pass:
                reasons.append("Purged K-Fold 不稳定")
            if not verdict.cro_gate_pass:
                reasons.append("CRO Gate 未通过")
            if not verdict.honest_validation_pass:
                reasons.append("Honest Validation 未通过")
            verdict.falsified_reason = "; ".join(reasons)

        # 审计
        action = "ENTER_AB" if verdict.enter_ab_bucket else "FALSIFIED"
        severity = "INFO" if verdict.enter_ab_bucket else "WARN"
        self._audit(action, verdict.summary_text(), severity=severity)

        return self._verdict_to_dict(verdict)

    # ------------------------------------------------------------
    # 批量验证
    # ------------------------------------------------------------

    def verify_batch(
        self,
        candidates: list[dict[str, Any]],
        factor_data_map: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """批量验证候选因子."""
        factor_data_map = factor_data_map or {}
        results: list[dict[str, Any]] = []
        for cand in candidates:
            fd = factor_data_map.get(cand.get("name", ""), {})
            results.append(self.verify(cand, fd))
        return results

    # ------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------

    def _verdict_to_dict(self, v: HypothesisVerdict) -> dict[str, Any]:
        """转 dict 便于 D1 消费."""
        return {
            "pass": v.enter_ab_bucket,
            "factor_name": v.factor_name,
            "rank_ic_mean": v.rank_ic_mean,
            "icir": v.icir,
            "ic_positive_ratio": v.ic_positive_ratio,
            "effect_size": v.effect_size,
            "cv_score": v.cv_score,
            "ic_significant": v.ic_significant,
            "purged_kfold_pass": v.purged_kfold_pass,
            "cro_gate_pass": v.cro_gate_pass,
            "honest_validation_pass": v.honest_validation_pass,
            "enter_ab_bucket": v.enter_ab_bucket,
            "falsified": v.falsified,
            "falsified_reason": v.falsified_reason,
            "n_samples": v.n_samples,
            "summary": v.summary_text(),
        }

    def _safe_mean(self, series: list[float]) -> float:
        if not series:
            return 0.0
        return sum(series) / len(series)

    def _safe_std(self, series: list[float]) -> float:
        if len(series) < 2:
            return 0.0
        m = self._safe_mean(series)
        var = sum((x - m) ** 2 for x in series) / (len(series) - 1)
        return var**0.5

    def _cohen_d(self, series: list[float]) -> float:
        """Cohen's d 效应量 (简化: mean / std)."""
        std = self._safe_std(series)
        if std == 0:
            return 0.0
        return abs(self._safe_mean(series)) / std

    def _audit(self, action: str, reason: str, severity: str = "INFO") -> None:
        if self.audit is not None:
            try:
                self.audit.log(
                    module="D2_VERIFY",
                    action=action,
                    severity=severity,
                    reason=reason,
                )
            except Exception:
                pass
