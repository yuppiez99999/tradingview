"""
双模型自我判断 (Dual-Model Self-Judgment)
==========================================
v8.6 自动闭环核心: DeepSeek + GLM-5.2 分别对盈亏/风控/对冲做判断,
交叉验证后输出共识决策. 任意单模型失败时降级为单模型决策.

闭环链路:
    复盘报告 → [DeepSeek 判断, GLM 判断] → 交叉验证 → 共识决策 → 动态风控调整

设计原则:
    1. 双模型独立判断: 同一上下文分别喂 DeepSeek 和 GLM, 避免互相污染
    2. 结构化输出: 强制 JSON schema (action + confidence + reason + risk_level)
    3. 交叉验证: action 一致 → 共识; 不一致 → 取高置信度 + 标记分歧
    4. 降级安全: 任一模型失败 → 单模型决策 + degraded 标记; 全失败 → 规则兜底
    5. 审计可追溯: 记录两模型原始回复 + 共识过程 + 最终决策

用法:
    from dual_model_judge import run_dual_model_judgment
    verdict = run_dual_model_judgment(review_report, trade_date="2026-08-19")
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("dual_model_judge")

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
REPORTS_DIR = BASE_DIR / "reports"


SYSTEM_PROMPT = (
    "你是A股量化交易系统的风控决策AI。根据当日复盘数据,对组合的风控状态、"
    "对冲策略、仓位调整给出独立判断。必须返回严格JSON格式:\n"
    "{\n"
    '  "action": "HOLD|TIGHTEN|LOOSEN|REBALANCE|EMERGENCY_HEDGE",\n'
    '  "confidence": 0.0-1.0,\n'
    '  "risk_level": "LOW|MEDIUM|HIGH|CRITICAL",\n'
    '  "position_adjustment": "减仓X%|加仓X%|不变|再平衡",\n'
    '  "hedge_adjustment": "增加对冲|减少对冲|维持|紧急对冲",\n'
    '  "reason": "决策理由(≤100字)",\n'
    '  "key_concerns": ["关注点1", "关注点2"]\n'
    "}\n"
    "只返回JSON,不要任何其他文字。"
)


def _build_user_prompt(review: dict[str, Any]) -> str:
    """从复盘报告构造用户提示词"""
    pnl = review.get("pnl_summary", {})
    hedge = review.get("hedge_review", {})
    ai = review.get("ai_review", {})
    risk = review.get("risk_assessment", {})

    prompt = f"""## 当日复盘数据 (请独立判断)

### 盈亏摘要
- 总盈亏: {pnl.get('total_pnl', 0):.2f} ({pnl.get('total_pnl_pct', 0):.2%})
- 净盈亏: {pnl.get('net_pnl', 0):.2f} ({pnl.get('net_pnl_pct', 0):.2%})
- 对冲盈亏: {pnl.get('hedge_pnl', 0):.2f}
- 对冲成本: {pnl.get('hedge_cost', 0):.2f}

### 对冲复盘
- 对冲启用: {hedge.get('hedge_enabled', False)}
- 组合Beta: {hedge.get('portfolio_beta', 0):.3f}
- 计划工具数: {hedge.get('planned_tools', 0)}
- 实际成交数: {hedge.get('executed_tools', 0)}
- 执行缺口: {hedge.get('execution_gaps', 0)}

### AI决策复盘
- AI建议总数: {ai.get('ai_total_count', 0)}
- AI命中数: {ai.get('ai_hit_count', 0)}
- AI命中率: {ai.get('ai_hit_rate', 0):.2%}

### 风险评估
- 风险等级: {risk.get('risk_level', 'UNKNOWN')}
- 综合评分: {risk.get('risk_score', 0):.4f}
- 风险事件: {json.dumps(risk.get('risk_events', []), ensure_ascii=False)}

### 评分明细
{json.dumps(risk.get('scores', {}), ensure_ascii=False, indent=2)}

请基于以上数据,给出你的独立风控决策(严格JSON格式)。"""
    return prompt


def _call_deepseek(prompt: str, system: str) -> str | None:
    """单独调用 DeepSeek (不走 fallback 链,确保独立性)"""
    try:
        from utils.alpha.llm.router import LLMRouter

        router = LLMRouter.get_instance()
        result = router._call_deepseek(
            prompt,
            system,
            temperature=0.3,
            max_tokens=1500,
            timeout=15,
        )
        return result
    except (ImportError, RuntimeError, ValueError, TypeError) as exc:
        logger.warning("DeepSeek 调用失败: %s", exc)
        return None


def _call_glm(prompt: str, system: str) -> str | None:
    """单独调用 GLM (不走 fallback 链,确保独立性)"""
    try:
        from utils.alpha.llm.router import LLMRouter

        router = LLMRouter.get_instance()
        result = router._call_glm(
            prompt,
            system,
            temperature=0.3,
            max_tokens=1500,
            timeout=15,
        )
        return result
    except (ImportError, RuntimeError, ValueError, TypeError) as exc:
        logger.warning("GLM 调用失败: %s", exc)
        return None


def _parse_json_response(text: str) -> dict[str, Any] | None:
    """从 LLM 回复中提取 JSON (容错: 去除 markdown 代码块/前后缀)"""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
    return None


def _rule_based_fallback(review: dict[str, Any]) -> dict[str, Any]:
    """双模型全失败时的规则兜底决策"""
    risk = review.get("risk_assessment", {})
    risk_level = risk.get("risk_level", "MEDIUM")
    risk_score = float(risk.get("risk_score", 0.5))

    if risk_level == "HIGH" or risk_score > 0.6:
        action = "TIGHTEN"
        pos_adj = "减仓5%"
        hedge_adj = "增加对冲"
    elif risk_level == "LOW" and risk_score < 0.2:
        action = "LOOSEN"
        pos_adj = "不变"
        hedge_adj = "维持"
    else:
        action = "HOLD"
        pos_adj = "不变"
        hedge_adj = "维持"

    return {
        "action": action,
        "confidence": 0.5,
        "risk_level": risk_level,
        "position_adjustment": pos_adj,
        "hedge_adjustment": hedge_adj,
        "reason": "规则兜底: 双模型不可用,基于风险评分决策",
        "key_concerns": [f"风险评分={risk_score:.2f}"],
    }


def _cross_validate(
    deepseek_judgment: dict[str, Any] | None,
    glm_judgment: dict[str, Any] | None,
) -> dict[str, Any]:
    """交叉验证两模型判断,输出共识决策

    Returns:
        {
            "consensus": {...},          # 共识决策
            "agreement": True/False,     # 是否一致
            "mode": "dual|single_deepseek|single_glm|rule_fallback",
            "divergence_reason": "...",  # 不一致时的原因
        }
    """
    ds_action = deepseek_judgment.get("action") if deepseek_judgment else None
    glm_action = glm_judgment.get("action") if glm_judgment else None

    if ds_action and glm_action:
        if ds_action == glm_action:
            ds_conf = float(deepseek_judgment.get("confidence", 0.5))
            glm_conf = float(glm_judgment.get("confidence", 0.5))
            consensus = deepseek_judgment.copy()
            consensus["confidence"] = round((ds_conf + glm_conf) / 2, 4)
            consensus["models_in_agreement"] = ["deepseek", "glm"]
            return {
                "consensus": consensus,
                "agreement": True,
                "mode": "dual",
                "divergence_reason": None,
            }
        ds_conf = float(deepseek_judgment.get("confidence", 0.5))
        glm_conf = float(glm_judgment.get("confidence", 0.5))
        winner = "deepseek" if ds_conf >= glm_conf else "glm"
        consensus = (deepseek_judgment if winner == "deepseek" else glm_judgment).copy()
        consensus["models_in_agreement"] = [winner]
        return {
            "consensus": consensus,
            "agreement": False,
            "mode": "dual",
            "divergence_reason": (
                f"DeepSeek={ds_action}(conf={ds_conf:.2f}) vs "
                f"GLM={glm_action}(conf={glm_conf:.2f}), 取高置信度 {winner}"
            ),
        }

    if ds_action:
        consensus = deepseek_judgment.copy()
        consensus["models_in_agreement"] = ["deepseek"]
        return {
            "consensus": consensus,
            "agreement": True,
            "mode": "single_deepseek",
            "divergence_reason": "GLM 不可用, 单 DeepSeek 决策",
        }
    if glm_action:
        consensus = glm_judgment.copy()
        consensus["models_in_agreement"] = ["glm"]
        return {
            "consensus": consensus,
            "agreement": True,
            "mode": "single_glm",
            "divergence_reason": "DeepSeek 不可用, 单 GLM 决策",
        }
    return {
        "consensus": {},
        "agreement": False,
        "mode": "none",
        "divergence_reason": "双模型均不可用",
    }


def run_dual_model_judgment(
    review_report: dict[str, Any],
    trade_date: str,
) -> dict[str, Any]:
    """双模型自我判断主入口

    Args:
        review_report: execution_reviewer.run_execution_review() 的输出
        trade_date: 交易日期 YYYY-MM-DD

    Returns:
        {
            "trade_date": "...",
            "deepseek_judgment": {...} | None,
            "glm_judgment": {...} | None,
            "cross_validation": {...},
            "final_decision": {...},
            "mode": "dual|single_deepseek|single_glm|rule_fallback",
            "generated_at": "...",
        }
    """
    logger.info("=" * 60)
    logger.info(f"双模型自我判断启动 | trade_date={trade_date}")
    logger.info("=" * 60)

    user_prompt = _build_user_prompt(review_report)

    ds_raw = _call_deepseek(user_prompt, SYSTEM_PROMPT)
    glm_raw = _call_glm(user_prompt, SYSTEM_PROMPT)

    ds_judgment = _parse_json_response(ds_raw) if ds_raw else None
    glm_judgment = _parse_json_response(glm_raw) if glm_raw else None

    logger.info("DeepSeek 判断: %s", "OK" if ds_judgment else "FAIL")
    logger.info("GLM 判断: %s", "OK" if glm_judgment else "FAIL")

    cross = _cross_validate(ds_judgment, glm_judgment)
    mode = cross["mode"]

    if mode == "none":
        final = _rule_based_fallback(review_report)
        mode = "rule_fallback"
        logger.warning("双模型均不可用, 降级到规则兜底")
    else:
        final = cross["consensus"]

    result = {
        "trade_date": trade_date,
        "generated_at": datetime.now().isoformat(),
        "module": "dual_model_judge",
        "deepseek_judgment": ds_judgment,
        "glm_judgment": glm_judgment,
        "deepseek_raw": ds_raw,
        "glm_raw": glm_raw,
        "cross_validation": cross,
        "final_decision": final,
        "mode": mode,
    }

    out_path = REPORTS_DIR / f"dual_model_judgment_{trade_date}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    logger.info("双模型判断报告已保存: %s", out_path)
    logger.info(
        "最终决策: mode=%s action=%s confidence=%s",
        mode,
        final.get("action"),
        final.get("confidence"),
    )

    return result


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    d = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    review_path = REPORTS_DIR / f"execution_review_{d}.json"
    if not review_path.exists():
        logger.error("复盘报告不存在: %s", review_path)
        sys.exit(1)
    with open(review_path, encoding="utf-8") as f:
        review = json.load(f)
    run_dual_model_judgment(review, trade_date=d)
