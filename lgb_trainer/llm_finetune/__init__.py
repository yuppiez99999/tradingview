"""
LLM 微调包 — W.C.1 unsloth 本地模型微调

用 LoRA (Low-Rank Adaptation) 微调小模型做财经新闻情感打分,
替代外部 LLM API, 降低云端 API 成本.

环境: .venv-finetune (Python 3.12 + CUDA PyTorch + transformers + peft + trl)
GPU: RTX 3060 Laptop 6GB

可用模块:
- finetune_sentiment_model: LoRA 微调情感分类模型
- finetune_pipeline: 完整 pipeline (数据准备 → 微调 → 评估 → 导出)
"""

from __future__ import annotations

__all__ = ["finetune_sentiment_model", "finetune_pipeline"]
