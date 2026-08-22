"""价值投资决策纪律层 (S3 集成 ai-berkshire)

四大师对抗分析 + 强制结论 + 镜子测试 + 去劣硬否决。
在 GLM5DecisionEngine.make_decisions 出口叠加, 向后兼容, 可开关。

详见 docs/集成记录/S3/spec-design_20260821.md
"""

import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Optional

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.value_discipline.info_grade import grade_info, grade_label
from utils.value_discipline.mirror_test import mirror_test as _mirror_test
from utils.value_discipline.prompts import MASTER_BUILDERS
from utils.value_discipline.quality_screen import ScreenResult, screen_quality

logger = logging.getLogger(__name__)


@dataclass
class MasterView:
    master: str
    score: float = 0.0
    reason: str = ""
    available: bool = True


@dataclass
class DisciplinedSignal:
    signal: Any                       # 增强后的 TradingSignal
    verdict: str = "grey"             # pass / fail / grey
    price_band: Optional[tuple[float, float]] = None
    tier_advice: dict[str, str] = field(default_factory=dict)
    masters: dict[str, MasterView] = field(default_factory=dict)
    consensus: float = 0.0
    mirror_pass: bool = True
    info_grade: str = "B"
    quality_screen: Optional[ScreenResult] = None
    extra_alerts: list[Any] = field(default_factory=list)


class ValueDisciplineLayer:
    """决策纪律层。可开关, 失败旁路, 不破坏主决策。"""

    def __init__(self, config_path: str = "config/value_discipline.yaml"):
        self.config: dict[str, Any] = {}
        self.enabled = False
        self._llm_caller: Optional[Any] = None
        try:
            cfg_path = Path(config_path)
            if not cfg_path.is_absolute():
                cfg_path = Path(__file__).parent.parent / config_path
            with open(cfg_path, encoding="utf-8") as f:
                self.config = yaml.safe_load(f) or {}
            self.enabled = bool(self.config.get("enabled", False))
            self._llm_caller = self._init_llm()
            logger.info("价值纪律层已加载 enabled=%s", self.enabled)
        except FileNotFoundError:
            logger.warning("价值纪律层配置不存在 %s, 旁路", config_path)
        except Exception as exc:
            logger.warning("价值纪律层初始化失败, 旁路: %s", exc)

    def _init_llm(self) -> Optional[Any]:
        """复用主系统 LLM, 失败返回 None (四大师将标记 unavailable)。"""
        try:
            from utils.glm5_client import GLM5Client

            client = GLM5Client()
            return lambda prompt: client.chat(prompt)
        except Exception as exc:
            logger.warning("价值纪律层 LLM 初始化失败, 四大师将不可用: %s", exc)
            return None

    def apply(
        self,
        signal: Any,
        financial_data: dict[str, Any],
        market_meta: dict[str, Any],
    ) -> DisciplinedSignal:
        """对单只标的叠加纪律层。失败旁路返回原信号。"""
        if not self.enabled:
            return DisciplinedSignal(signal=signal)

        try:
            return self._apply_impl(signal, financial_data, market_meta)
        except Exception as exc:
            logger.error("纪律层 apply 异常, 旁路: %s", exc)
            return DisciplinedSignal(signal=signal)

    def apply_batch(
        self,
        signals: list[Any],
        fin_data_map: dict[str, dict[str, Any]],
        meta_map: dict[str, dict[str, Any]],
    ) -> list[DisciplinedSignal]:
        """批量叠加。fin_data_map/meta_map 以 signal.code 为键。"""
        if not self.enabled:
            return [DisciplinedSignal(signal=s) for s in signals]

        max_workers = min(8, max(1, len(signals)))
        results: list[DisciplinedSignal] = [DisciplinedSignal(signal=s) for s in signals]
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_to_idx = {}
            for idx, sig in enumerate(signals):
                fin = fin_data_map.get(getattr(sig, "code", ""), {})
                meta = meta_map.get(getattr(sig, "code", ""), {})
                future_to_idx[pool.submit(self.apply, sig, fin, meta)] = idx
            for fut in as_completed(future_to_idx):
                idx = future_to_idx[fut]
                try:
                    results[idx] = fut.result()
                except Exception as exc:
                    logger.error("纪律层批量第 %d 项异常: %s", idx, exc)
        return results

    def _apply_impl(
        self,
        signal: Any,
        fin: dict[str, Any],
        meta: dict[str, Any],
    ) -> DisciplinedSignal:
        # ① 信息评级
        grade = grade_info(meta)

        # ② 去劣筛选
        qs_cfg = self.config.get("quality_screen", {})
        thresholds = qs_cfg.get("indicators", {})
        is_bank = meta.get("is_bank_insurance", False)
        screen = screen_quality(fin, thresholds, is_bank) if qs_cfg.get("enabled", True) else None

        # ③ 四大师对抗
        masters = self._run_four_masters(signal, fin, grade)
        weights = self.config.get("masters", {})

        # ④ 共识度
        consensus = self._weighted_consensus(masters, weights)

        # ⑤ 镜子测试
        mirror_cfg = self.config.get("mirror_test", {})
        mirror_ok = True
        if mirror_cfg.get("enabled", True):
            mirror_ok = _mirror_test(
                getattr(signal, "reason", ""),
                max_sentences=mirror_cfg.get("max_sentences", 5),
                llm_caller=self._llm_caller,
                fallback=mirror_cfg.get("llm_fallback", True),
            )

        # ⑥ 判定 verdict
        verdict = self._decide_verdict(screen, consensus, mirror_ok)

        # ⑦ 价格区间 + 分层建议
        price_band, tier_advice = self._extract_advice(masters, signal)

        # ⑧ 增强信号 (不可变)
        extra_alerts: list[Any] = []
        new_signal = self._enhance_signal(
            signal, verdict, consensus, mirror_ok, screen, extra_alerts
        )

        return DisciplinedSignal(
            signal=new_signal,
            verdict=verdict,
            price_band=price_band,
            tier_advice=tier_advice,
            masters=masters,
            consensus=consensus,
            mirror_pass=mirror_ok,
            info_grade=grade,
            quality_screen=screen,
            extra_alerts=extra_alerts,
        )

    def _run_four_masters(
        self, signal: Any, fin: dict[str, Any], grade: str
    ) -> dict[str, MasterView]:
        stock_name = getattr(signal, "name", "") or getattr(signal, "code", "")
        results: dict[str, MasterView] = {}
        llm_cfg = self.config.get("llm", {})
        parallel = llm_cfg.get("parallel", True)
        timeout = llm_cfg.get("timeout_per_master", 8)

        def _call_one(master: str) -> MasterView:
            builder = MASTER_BUILDERS.get(master)
            if builder is None or self._llm_caller is None:
                return MasterView(master=master, available=False)
            prompt = builder(stock_name, fin, signal, grade)
            try:
                raw = self._llm_caller(prompt)
                text = raw.strip() if isinstance(raw, str) else getattr(raw, "content", "").strip()
                start, end = text.find("{"), text.rfind("}")
                if start == -1 or end == -1:
                    return MasterView(master=master, available=False)
                obj = json.loads(text[start : end + 1])
                return MasterView(
                    master=master,
                    score=float(obj.get("score", 0)),
                    reason=str(obj.get("reason", ""))[:120],
                )
            except Exception as exc:
                logger.warning("四大师 %s 调用失败: %s", master, exc)
                return MasterView(master=master, available=False)

        masters_list = list(MASTER_BUILDERS.keys())
        if parallel and self._llm_caller is not None:
            with ThreadPoolExecutor(max_workers=4) as pool:
                futs = {pool.submit(_call_one, m): m for m in masters_list}
                for fut in as_completed(futs, timeout=timeout * 4):
                    m = futs[fut]
                    try:
                        results[m] = fut.result(timeout=timeout)
                    except Exception as exc:
                        logger.warning("四大师 %s 超时/异常: %s", m, exc)
                        results[m] = MasterView(master=m, available=False)
        else:
            for m in masters_list:
                results[m] = _call_one(m)
        return results

    @staticmethod
    def _weighted_consensus(
        masters: dict[str, MasterView], weights: dict[str, Any]
    ) -> float:
        num = 0.0
        den = 0.0
        for m, view in masters.items():
            if not view.available:
                continue
            w = float(weights.get(m, {}).get("weight", 0.25))
            num += w * view.score
            den += w
        if den <= 0:
            return 0.0
        return max(0.0, min(1.0, (num / den) / 5.0))

    @staticmethod
    def _decide_verdict(
        screen: Optional[ScreenResult], consensus: float, mirror_ok: bool
    ) -> str:
        if screen is not None and screen.hard_fail:
            return "fail"
        if not mirror_ok:
            return "fail"
        if consensus >= 0.7:
            return "pass"
        if consensus <= 0.4:
            return "fail"
        return "grey"

    @staticmethod
    def _extract_advice(
        masters: dict[str, MasterView], signal: Any
    ) -> tuple[Optional[tuple[float, float]], dict[str, str]]:
        price = getattr(signal, "price", 0.0) or 0.0
        band = None
        if price > 0:
            band = (round(price * 0.95, 2), round(price * 1.05, 2))
        avg_score = 0.0
        cnt = 0
        for v in masters.values():
            if v.available:
                avg_score += v.score
                cnt += 1
        avg = avg_score / cnt if cnt else 0.0
        tier = {
            "激进": f"当前价位可建仓, 大师均分 {avg:.1f}/5" if avg >= 3.5 else "观望",
            "稳健": f"等回调至 {band[0] if band else 'N/A'} 附近建仓" if band else "观望",
            "保守": "不符合 10 年确定性标准, 观望" if avg < 4.0 else "可小仓位建仓",
        }
        return band, tier

    def _enhance_signal(
        self,
        signal: Any,
        verdict: str,
        consensus: float,
        mirror_ok: bool,
        screen: Optional[ScreenResult],
        extra_alerts: list[Any],
    ) -> Any:
        base_conf = float(getattr(signal, "confidence", 0.0))
        new_conf = base_conf * consensus * (1.0 if mirror_ok else 0.0)
        new_conf = max(0.0, min(1.0, new_conf))

        reason = getattr(signal, "reason", "")
        tags = []
        if verdict == "fail":
            tags.append("[纪律否决]")
        if not mirror_ok:
            tags.append("[论点不可压缩]")
        if screen is not None and screen.hard_fail:
            tags.append("[去劣硬否决]")
        new_reason = f"{reason} {' '.join(tags)}".strip() if tags else reason

        action = getattr(signal, "action", "HOLD")
        urgency = getattr(signal, "urgency", "MEDIUM")

        if screen is not None and screen.hard_fail:
            action = self.config.get("quality_screen", {}).get("hard_fail_action", "REDUCE")
            urgency = "HIGH"
            extra_alerts.append(self._make_alert(signal, "QUALITY_FAIL", "HIGH",
                f"去劣硬否决: {screen.detail.get('hard_fail', '')}"))
        if not mirror_ok and action not in ("HOLD", "REDUCE", "SELL"):
            action = "HOLD"
            urgency = "MEDIUM"

        try:
            return replace(
                signal,
                confidence=new_conf,
                reason=new_reason,
                action=action,
                urgency=urgency,
            )
        except TypeError:
            signal.confidence = new_conf
            signal.reason = new_reason
            signal.action = action
            signal.urgency = urgency
            return signal

    @staticmethod
    def _make_alert(signal: Any, alert_type: str, severity: str, message: str) -> Any:
        try:
            from utils.glm5_decision_engine import RiskAlert

            return RiskAlert(
                alert_type=alert_type,
                severity=severity,
                code=getattr(signal, "code", ""),
                message=message,
                action_required="复核财务数据, 考虑减仓或剔除",
            )
        except Exception:
            return {"alert_type": alert_type, "severity": severity,
                    "code": getattr(signal, "code", ""), "message": message}

    def render_summary(self, disciplined: list[DisciplinedSignal]) -> str:
        """生成纪律层摘要, 追加到 DecisionResult.raw_analysis。"""
        if not disciplined:
            return ""
        lines = ["\n\n## 价值纪律层摘要 (四大师对抗 + 去劣 + 镜子)"]
        for d in disciplined:
            sig = d.signal
            masters_str = " ".join(
                f"{m}:{v.score:.1f}" for m, v in d.masters.items() if v.available
            )
            lines.append(
                f"- {getattr(sig, 'name', getattr(sig, 'code', ''))}: "
                f"verdict={d.verdict} 共识={d.consensus:.2f} 镜子={d.mirror_pass} "
                f"评级={d.info_grade}({grade_label(d.info_grade)}) | {masters_str}"
            )
        return "\n".join(lines)
