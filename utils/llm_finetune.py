"""llm_finetune — unsloth LLM 训练加速封装层

提供 GLM-5 / 豆包 / Qwen 等大模型微调的统一接口，底层使用 unsloth 加速
（2x-5x 训练速度 + 50% VRAM 节省）。unsloth 未安装时优雅降级为 no-op。

主要 API:
    is_unsloth_available()       — 检测 unsloth 是否可用
    FastModelWrapper             — 模型加载+训练统一封装
    prepare_trading_sft_dataset  — 将交易决策记录转为 SFT 数据集
    finetune_glm5                — 一键微调 GLM-5
    finetune_doubao              — 一键微调豆包 (Ark)

Usage:
    from utils.llm_finetune import is_unsloth_available, finetune_glm5
    if is_unsloth_available():
        finetune_glm5("THUDM/glm-4-9b-chat", train_data, output_dir="models/glm5-ft")
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_UNSLOTH_DISABLED = os.environ.get("LLM_FINETUNE_DISABLED", "").strip() not in ("", "0", "false", "False")


def is_unsloth_available() -> bool:
    """检测 unsloth 是否已安装且可用。"""
    if _UNSLOTH_DISABLED:
        return False
    try:
        import unsloth  # noqa: F401
        return True
    except Exception:
        return False


def _import_fast_model():
    """懒导入 unsloth FastModel。"""
    from unsloth import FastModel
    return FastModel


class FastModelWrapper:
    """unsloth FastModel 的轻量封装，提供加载/训练/保存的统一接口。

    unsloth 不可用时构造抛出 RuntimeError，调用方应先 is_unsloth_available()。
    """

    def __init__(self, model_name: str, max_seq_length: int = 2048,
                 load_in_4bit: bool = True, dtype: Any = None) -> None:
        if not is_unsloth_available():
            raise RuntimeError("unsloth not available — pip install -e .[llm-finetune]")
        FastModel = _import_fast_model()
        self.model, self.tokenizer = FastModel.from_pretrained(
            model_name=model_name,
            max_seq_length=max_seq_length,
            load_in_4bit=load_in_4bit,
            dtype=dtype,
        )
        self.model_name = model_name
        self.max_seq_length = max_seq_length

    def add_lora(self, r: int = 16, target_modules: list[str] | None = None,
                 lora_alpha: int = 16, lora_dropout: float = 0.05) -> Any:
        """添加 LoRA 适配器。target_modules 默认全部线性层。"""
        if target_modules is None:
            target_modules = ["q_proj", "k_proj", "v_proj", "o_proj",
                              "gate_proj", "up_proj", "down_proj"]
        FastModel = _import_fast_model()
        self.model, self.tokenizer = FastModel.get_peft_model(
            self.model, self.tokenizer,
            r=r, target_modules=target_modules,
            lora_alpha=lora_alpha, lora_dropout=lora_dropout,
        )
        return self.model

    def train(self, dataset: Any, epochs: int = 3, batch_size: int = 2,
              grad_accum: int = 4, lr: float = 2e-4, **kwargs: Any) -> Any:
        """SFT 训练。dataset 为 HuggingFace datasets.Dataset。"""
        from trl import SFTConfig, SFTTrainer
        cfg = SFTConfig(
            output_dir=kwargs.pop("output_dir", "./_unsloth_tmp"),
            per_device_train_batch_size=batch_size,
            gradient_accumulation_steps=grad_accum,
            num_train_epochs=epochs,
            learning_rate=lr,
            max_seq_length=self.max_seq_length,
            **kwargs,
        )
        trainer = SFTTrainer(
            model=self.model, tokenizer=self.tokenizer,
            train_dataset=dataset, args=cfg,
        )
        trainer_stats = trainer.train()
        return trainer_stats

    def save(self, output_dir: str, save_method: str = "lora") -> None:
        """保存模型。save_method: 'lora' | 'merged_16bit' | 'merged_4bit' | 'gguf'。"""
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        if save_method == "gguf":
            self.model.save_pretrained_gguf(output_dir, self.tokenizer)
        else:
            self.model.save_pretrained(output_dir, save_method=save_method)
        self.tokenizer.save_pretrained(output_dir)
        logger.info("model saved to %s (method=%s)", output_dir, save_method)


def prepare_trading_sft_dataset(decisions: list[dict], system_prompt: str = "") -> list[dict]:
    """将交易决策记录转为 SFT 对话格式。

    decisions: [{"market_context": "...", "analysis": "...", "decision": "..."}]
    返回: [{"messages": [{"role":"system",...}, {"role":"user",...}, {"role":"assistant",...}]}]
    """
    samples: list[dict] = []
    for d in decisions:
        user = d.get("market_context", "") + "\n\n" + d.get("analysis", "")
        assistant = d.get("decision", "")
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user.strip()})
        messages.append({"role": "assistant", "content": assistant})
        samples.append({"messages": messages})
    return samples


def finetune_glm5(model_name: str = "THUDM/glm-4-9b-chat",
                  train_data: list[dict] | Any = None,
                  output_dir: str = "models/glm5-ft",
                  data_path: str | None = None,
                  epochs: int = 3, lr: float = 2e-4) -> dict:
    """一键微调 GLM-5。train_data 为 prepare_trading_sft_dataset 输出或 HF Dataset。"""
    if not is_unsloth_available():
        logger.warning("unsloth not available — skipping GLM-5 fine-tune")
        return {"status": "skipped", "reason": "unsloth_unavailable"}

    from datasets import Dataset
    if isinstance(train_data, list):
        train_data = Dataset.from_list(train_data)
    elif data_path:
        train_data = Dataset.from_json(data_path)

    wrapper = FastModelWrapper(model_name, max_seq_length=2048, load_in_4bit=True)
    wrapper.add_lora(r=16)
    wrapper.train(train_data, epochs=epochs, lr=lr, output_dir=output_dir)
    wrapper.save(output_dir, save_method="merged_16bit")
    return {"status": "ok", "output_dir": output_dir, "model": model_name}


def finetune_doubao(model_name: str = "Qwen/Qwen2.5-7B-Instruct",
                    train_data: list[dict] | Any = None,
                    output_dir: str = "models/doubao-ft",
                    epochs: int = 3, lr: float = 2e-4) -> dict:
    """一键微调豆包（Ark 平台模型，底层多用 Qwen 架构）。"""
    return finetune_glm5(model_name=model_name, train_data=train_data,
                         output_dir=output_dir, epochs=epochs, lr=lr)
