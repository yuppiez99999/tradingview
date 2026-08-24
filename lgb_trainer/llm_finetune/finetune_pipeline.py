"""
完整微调 Pipeline — W.C.1

数据准备 → LoRA 微调 → 评估 → 导出
支持从 reflections.jsonl 或合成数据加载训练样本.
"""
from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .finetune_sentiment_model import (
    FinetuneConfig,
    FinetuneResult,
    finetune,
    predict,
)

logger = logging.getLogger("llm_finetune_pipeline")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
REFLECTIONS_PATH = _PROJECT_ROOT / "reports" / "ai_hedge_fund" / "memory" / "reflections.jsonl"


@dataclass
class PipelineResult:
    """Pipeline 执行结果"""

    finetune_result: FinetuneResult
    eval_accuracy: float = 0.0
    sample_predictions: list[dict[str, Any]] | None = None
    data_source: str = ""


def load_training_data(
    source: str = "synthetic",
    max_samples: int = 200,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """加载训练数据

    Args:
        source: "synthetic" (合成数据) 或 "reflections" (从 reflections.jsonl)
        max_samples: 最大样本数

    Returns:
        (train_texts, train_labels, eval_texts, eval_labels)
    """
    if source == "reflections" and REFLECTIONS_PATH.exists():
        return _load_from_reflections(max_samples)
    return _load_synthetic(max_samples)


def _load_synthetic(max_samples: int) -> tuple[list[str], list[str], list[str], list[str]]:
    """合成财经新闻情感分类数据 (POC 用)

    NEW-3 修复: 分层均衡采样, 每类 max_samples//3 个, 避免标签偏斜.
    """
    templates = {
        "正面": [
            "公司{company}发布年报，净利润同比增长{pct}%，超出市场预期",
            "{company}获得{amount}亿元大额订单，股价盘中涨停",
            "央行降准{pct}个百分点，市场流动性充裕，利好股市",
            "{company}回购股份{amount}亿元，彰显管理层信心",
            "新能源板块全线走强，{company}领涨{pct}%",
        ],
        "中性": [
            "公司{company}发布日常公告，更换会计师事务所",
            "今日沪深两市成交额{amount}亿元，与昨日基本持平",
            "{company}召开股东大会，审议通过年度审计报告",
            "证监会就{company}信息披露事项发出问询函",
            "今日北向资金净流入{amount}万元，幅度较小",
        ],
        "负面": [
            "公司{company}发布业绩预告，预计全年亏损{amount}亿元",
            "{company}实控人被立案调查，股价跌停",
            "美联储加息{pct}个基点，全球股市承压下挫",
            "{company}产品召回，预计影响营收{amount}亿元",
            "房地产板块集体下挫，{company}跌{pct}%",
        ],
    }

    companies = ["贵州茅台", "宁德时代", "比亚迪", "中国平安", "招商银行", "五粮液", "隆基绿能", "伊利股份"]
    texts: list[str] = []
    labels: list[str] = []

    per_class = max(1, max_samples // len(templates))
    for label, tmpl_list in templates.items():
        for _ in range(per_class):
            template = random.choice(tmpl_list)
            text = template.format(
                company=random.choice(companies),
                pct=random.randint(1, 50),
                amount=random.randint(1, 100),
            )
            texts.append(text)
            labels.append(label)

    combined = list(zip(texts, labels, strict=True))
    random.shuffle(combined)
    texts, labels = [t for t, _ in combined], [lbl for _, lbl in combined]

    split = int(len(texts) * 0.8)
    return texts[:split], labels[:split], texts[split:], labels[split:]


def _load_from_reflections(max_samples: int) -> tuple[list[str], list[str], list[str], list[str]]:
    """从 reflections.jsonl 加载决策反思数据"""
    texts: list[str] = []
    labels: list[str] = []

    with open(REFLECTIONS_PATH, encoding="utf-8") as f:
        for line in f:
            if len(texts) >= max_samples:
                break
            try:
                record = json.loads(line.strip())
                reflection = record.get("reflection", "")
                if not reflection or len(reflection) < 20:
                    continue
                outcome = record.get("outcome", "")
                if "win" in outcome.lower() or "盈利" in outcome:
                    label = "正面"
                elif "loss" in outcome.lower() or "亏损" in outcome:
                    label = "负面"
                else:
                    label = "中性"
                texts.append(reflection[:200])
                labels.append(label)
            except (json.JSONDecodeError, ValueError, TypeError, KeyError):
                continue

    if len(texts) < 20:
        logger.warning("reflections 数据不足 (%d), 回退合成数据", len(texts))
        return _load_synthetic(max_samples)

    split = int(len(texts) * 0.8)
    return texts[:split], labels[:split], texts[split:], labels[split:]


def evaluate(
    model_dir: str,
    eval_texts: list[str],
    eval_labels: list[str],
    config: FinetuneConfig | None = None,
) -> tuple[float, list[dict[str, Any]]]:
    """评估微调模型准确率

    Returns:
        (accuracy, predictions)
    """
    if len(eval_texts) != len(eval_labels):
        raise ValueError(
            f"eval_texts 长度 {len(eval_texts)} != eval_labels 长度 {len(eval_labels)}"
        )
    if not eval_labels:
        return 0.0, []

    correct = 0
    predictions: list[dict[str, Any]] = []

    for text, true_label in zip(eval_texts, eval_labels, strict=True):
        pred = predict(text, model_dir, config)
        is_correct = pred == true_label
        if is_correct:
            correct += 1
        predictions.append({
            "text": text[:80],
            "true": true_label,
            "pred": pred,
            "correct": is_correct,
        })

    accuracy = correct / len(eval_labels)
    return accuracy, predictions


def run_pipeline(
    data_source: str = "synthetic",
    max_samples: int = 200,
    config: FinetuneConfig | None = None,
) -> PipelineResult:
    """运行完整微调 pipeline

    Args:
        data_source: "synthetic" 或 "reflections"
        max_samples: 最大样本数
        config: 微调配置

    Returns:
        PipelineResult
    """
    if config is None:
        config = FinetuneConfig()

    logger.info("=== W.C.1 LLM 微调 Pipeline ===")
    logger.info("数据源: %s, 最大样本: %d", data_source, max_samples)

    train_texts, train_labels, eval_texts, eval_labels = load_training_data(data_source, max_samples)
    logger.info("训练集: %d, 验证集: %d", len(train_texts), len(eval_texts))

    result = finetune(train_texts, train_labels, config, eval_texts, eval_labels)
    logger.info("微调完成: train_loss=%.4f", result.train_loss)

    eval_accuracy = 0.0
    sample_predictions: list[dict[str, Any]] | None = None
    if eval_texts:
        eval_accuracy, sample_predictions = evaluate(result.output_dir, eval_texts, eval_labels, config)
        logger.info("评估准确率: %.2f%%", eval_accuracy * 100)

    return PipelineResult(
        finetune_result=result,
        eval_accuracy=eval_accuracy,
        sample_predictions=sample_predictions,
        data_source=data_source,
    )
