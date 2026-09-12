"""
LoRA 微调情感分类模型 — W.C.1

用 Qwen2.5-1.5B + LoRA 微调做财经新闻情感三分类 (positive/neutral/negative).
4bit 量化 + LoRA rank=8, RTX 3060 6GB 显存足够.

替代 unsloth 方案: 直接用 transformers + peft + trl (unsloth 在 Windows 有 triton 兼容问题).
"""

from __future__ import annotations

import functools
import logging
import os
from dataclasses import dataclass
from typing import Any

import torch
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
)
from trl import SFTConfig, SFTTrainer

logger = logging.getLogger("llm_finetune")

DEFAULT_MODEL = "Qwen/Qwen2.5-0.5B"
DEFAULT_OUTPUT_DIR = "models/llm_finetune/sentiment_lora"

LABELS = {"positive": "正面", "neutral": "中性", "negative": "负面"}


@dataclass
class FinetuneConfig:
    """微调配置"""

    base_model: str = DEFAULT_MODEL
    # 供应链安全 (B615/CWE-1357): 固定基座模型 revision (commit/tag), 避免
    # from_pretrained 拉取可变 main 分支被上游投毒或静默变更。
    # 置为 None 表示不固定 (仅调试用), 生产应显式指定 tag/commit。
    base_model_revision: str | None = None
    output_dir: str = DEFAULT_OUTPUT_DIR
    lora_r: int = 8
    lora_alpha: int = 16
    lora_dropout: float = 0.05
    max_seq_length: int = 512
    batch_size: int = 2
    gradient_accumulation_steps: int = 4
    num_epochs: int = 3
    learning_rate: float = 2e-4
    warmup_ratio: float = 0.1
    use_4bit: bool = True
    max_new_tokens: int = 64


@dataclass
class FinetuneResult:
    """微调结果"""

    output_dir: str
    train_loss: float = 0.0
    eval_loss: float = 0.0
    eval_accuracy: float = 0.0
    model_name: str = ""
    lora_params: int = 0
    total_params: int = 0


def _format_instruction(text: str, label: str | None = None) -> str:
    """格式化指令微调样本"""
    prompt = (
        "你是一个财经新闻情感分析专家。请判断以下新闻的情感倾向，"
        "只回答：正面/中性/负面。\n\n"
        f"新闻：{text}\n\n情感："
    )
    if label is not None:
        prompt += f"{label}"
    return prompt


def load_model_and_tokenizer(config: FinetuneConfig) -> tuple[Any, Any]:
    """加载基座模型 + tokenizer (4bit 量化)"""
    logger.info("加载基座模型: %s", config.base_model)

    bnb_config = None
    if config.use_4bit:
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_use_double_quant=True,
        )

    model = AutoModelForCausalLM.from_pretrained(
        config.base_model,
        revision=config.base_model_revision,
        quantization_config=bnb_config,
        device_map="auto",
        torch_dtype=torch.float16,
    )  # nosec B615 — revision 可固定; 生产由 FinetuneConfig.base_model_revision 指定
    tokenizer = AutoTokenizer.from_pretrained(
        config.base_model, revision=config.base_model_revision
    )  # nosec B615 — 同上
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = prepare_model_for_kbit_training(model)
    return model, tokenizer


def apply_lora(model: Any, config: FinetuneConfig) -> Any:
    """应用 LoRA adapter"""
    lora_config = LoraConfig(
        r=config.lora_r,
        lora_alpha=config.lora_alpha,
        lora_dropout=config.lora_dropout,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()
    return model


def finetune(
    train_texts: list[str],
    train_labels: list[str],
    config: FinetuneConfig | None = None,
    eval_texts: list[str] | None = None,
    eval_labels: list[str] | None = None,
) -> FinetuneResult:
    """LoRA 微调情感分类模型

    Args:
        train_texts: 训练新闻文本列表
        train_labels: 训练标签列表 (positive/neutral/negative)
        config: 微调配置
        eval_texts: 验证文本
        eval_labels: 验证标签

    Returns:
        FinetuneResult
    """
    if config is None:
        config = FinetuneConfig()

    os.makedirs(config.output_dir, exist_ok=True)

    model, tokenizer = load_model_and_tokenizer(config)
    model = apply_lora(model, config)

    train_texts_formatted = [
        _format_instruction(t, label)
        for t, label in zip(train_texts, train_labels, strict=True)
    ]

    from datasets import Dataset

    train_dataset = Dataset.from_dict({"text": train_texts_formatted})

    eval_dataset = None
    if eval_texts and eval_labels:
        eval_formatted = [
            _format_instruction(t, label)
            for t, label in zip(eval_texts, eval_labels, strict=True)
        ]
        eval_dataset = Dataset.from_dict({"text": eval_formatted})

    sft_config = SFTConfig(
        output_dir=config.output_dir,
        num_train_epochs=config.num_epochs,
        per_device_train_batch_size=config.batch_size,
        gradient_accumulation_steps=config.gradient_accumulation_steps,
        learning_rate=config.learning_rate,
        warmup_ratio=config.warmup_ratio,
        max_seq_length=config.max_seq_length,
        logging_steps=10,
        save_strategy="epoch",
        fp16=True,
        report_to="none",
        dataset_text_field="text",
    )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        args=sft_config,
    )

    logger.info("开始微调...")
    trainer.train()

    logger.info("保存 LoRA adapter 到 %s", config.output_dir)
    trainer.save_model(config.output_dir)
    tokenizer.save_pretrained(config.output_dir)

    result = FinetuneResult(
        output_dir=config.output_dir,
        model_name=config.base_model,
    )

    if hasattr(trainer.state, "log_history") and trainer.state.log_history:
        last_log = trainer.state.log_history[-1]
        result.train_loss = last_log.get("train_loss", 0.0)
        result.eval_loss = last_log.get("eval_loss", 0.0)

    return result


def predict(text: str, model_dir: str, config: FinetuneConfig | None = None) -> str:
    """用微调后的模型做情感预测

    Args:
        text: 新闻文本
        model_dir: LoRA adapter 目录

    Returns:
        预测标签 (正面/中性/负面)
    """
    if config is None:
        config = FinetuneConfig()
    predictor = _get_predictor(model_dir, config.base_model, config.use_4bit)
    return predictor.predict(text)


def _parse_label(response: str) -> str:
    """从模型输出解析标签."""
    response = response.strip()
    for key, label in LABELS.items():
        if label in response or key in response:
            return label
    return "中性"


@functools.lru_cache(maxsize=4)
def _get_predictor(
    model_dir: str, base_model: str, use_4bit: bool
) -> SentimentPredictor:
    """缓存模型实例 (按 model_dir+base_model+量化配置 维度缓存).

    lru_cache 避免 evaluate 循环中反复加载模型 (NEW-1 修复).
    maxsize=4: 容纳不同 base_model/量化组合, 超出 LRU 淘汰最久未用.
    """
    config = FinetuneConfig(base_model=base_model, use_4bit=use_4bit)
    return SentimentPredictor(model_dir, config)


class SentimentPredictor:
    """加载一次模型, 复用做推理 (NEW-1 修复: 替代无状态 predict 反复加载).

    用法:
        predictor = SentimentPredictor("models/llm_finetune/sentiment_lora")
        label = predictor.predict("贵州茅台净利润同比增长30%")
        labels = predictor.predict_batch(["新闻1", "新闻2"])
        predictor.close()  # 显式释放 GPU 内存
    """

    def __init__(self, model_dir: str, config: FinetuneConfig | None = None) -> None:
        self.config = config or FinetuneConfig()
        from peft import PeftModel

        bnb_config = None
        if self.config.use_4bit:
            bnb_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_use_double_quant=True,
            )
        self.base_model = AutoModelForCausalLM.from_pretrained(
            self.config.base_model,
            revision=self.config.base_model_revision,
            quantization_config=bnb_config,
            device_map="auto",
            torch_dtype=torch.float16,
        )  # nosec B615 — revision 可固定, 见 FinetuneConfig.base_model_revision
        self.tokenizer = AutoTokenizer.from_pretrained(
            self.config.base_model, revision=self.config.base_model_revision
        )  # nosec B615 — 同上
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        self.model = PeftModel.from_pretrained(self.base_model, model_dir)
        self.model.eval()

    def predict(self, text: str) -> str:
        """单样本推理."""
        prompt = _format_instruction(text)
        inputs = self.tokenizer(prompt, return_tensors="pt").to(self.model.device)
        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=self.config.max_new_tokens,
                do_sample=False,
            )
        response = self.tokenizer.decode(
            outputs[0][inputs["input_ids"].shape[1] :], skip_special_tokens=True
        )
        return _parse_label(response)

    def predict_batch(self, texts: list[str], batch_size: int = 8) -> list[str]:
        """批量推理 (减少 GPU kernel launch 开销)."""
        results: list[str] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            prompts = [_format_instruction(t) for t in batch]
            inputs = self.tokenizer(
                prompts,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=self.config.max_seq_length,
            ).to(self.model.device)
            with torch.no_grad():
                outputs = self.model.generate(
                    **inputs,
                    max_new_tokens=self.config.max_new_tokens,
                    do_sample=False,
                )
            for j, out in enumerate(outputs):
                resp = self.tokenizer.decode(
                    out[inputs["input_ids"][j].shape[0] :], skip_special_tokens=True
                )
                results.append(_parse_label(resp))
        return results

    def close(self) -> None:
        """释放 GPU 内存."""
        del self.model
        del self.base_model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass
