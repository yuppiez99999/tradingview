"""unsloth 适配器 — 本地 LLM 训练增强 (W8.6 集成).

将 unsloth (10_第三方项目/unsloth) 接入 GLM5DecisionEngine,
为盘中决策/再平衡场景提供本地微调能力, 替代纯 API 调用.

集成点:
    - utils/glm5_decision_engine.py GLM5DecisionEngine
    - utils/ml_enhanced_trainer.py MLEnhancedTrainer

使用方式:
    from utils.unsloth_adapter import UnslothAdapter, get_unsloth_adapter

    adapter = get_unsloth_adapter()
    if adapter.is_ready():
        model, tokenizer = adapter.load_finetuned("glm5-quant-v1")
        response = adapter.infer(model, tokenizer, "分析市场")

降级策略:
    - unsloth 未安装 → is_ready()=False, 调用方回退到 GLM5Client
    - GPU 不可用 → 自动切换 CPU 模式 (slow)
    - 模型路径无效 → 返回 None, 调用方降级

依赖路径:
    - 主系统: 28-终极量化交易系统8.4/
    - unsloth 源码: 10_第三方项目/unsloth/unsloth/
    - 模型输出: models/unsloth_finetuned/

集成日期: 2026-08-21 (v8.6, GitHub 周热门项目集成)
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# unsloth 源码路径 (相对主系统根目录)
_UNSLOTH_SRC = (
    Path(__file__).resolve().parent.parent.parent / "10_第三方项目" / "unsloth"
)
_MODEL_OUTPUT = Path(__file__).resolve().parent.parent / "models" / "unsloth_finetuned"


@dataclass
class UnslothConfig:
    """unsloth 适配配置."""

    model_name: str = "unsloth/Qwen3-8B-Instruct"
    max_seq_length: int = 4096
    load_in_4bit: bool = True
    dtype: Any = None  # None=auto
    gpu_device: int = 0
    finetuned_dir: Path = field(default_factory=lambda: _MODEL_OUTPUT)
    use_unsloth_src: bool = True  # True=直接引用 10_第三方项目/unsloth 源码

    def __post_init__(self) -> None:
        if not self.finetuned_dir.exists():
            self.finetuned_dir.mkdir(parents=True, exist_ok=True)


class UnslothAdapter:
    """unsloth 适配器 — 本地 LLM 加载/推理/微调.

    设计原则:
        - 不可变: 所有训练产出新模型, 不修改原模型
        - 优雅降级: unsloth 不可用时 is_ready()=False
        - 薄包装: 不重写 unsloth API, 仅做场景适配
    """

    def __init__(self, config: UnslothConfig | None = None) -> None:
        self.config = config or UnslothConfig()
        self._unsloth: Any = None
        self._torch: Any = None
        self._init_error: str | None = None
        self._load_unsloth()

    def _load_unsloth(self) -> None:
        """延迟加载 unsloth, 失败记录原因."""
        try:
            if self.config.use_unsloth_src and _UNSLOTH_SRC.exists():
                src_path = str(_UNSLOTH_SRC)
                if src_path not in sys.path:
                    sys.path.insert(0, src_path)
                import unsloth  # type: ignore

                self._unsloth = unsloth
                logger.info("✓ unsloth 已加载 (源码: %s)", src_path)
            else:
                import unsloth  # type: ignore

                self._unsloth = unsloth
                logger.info("✓ unsloth 已加载 (pip 安装)")
        except ImportError as e:
            self._init_error = f"unsloth 未安装: {e}"
            logger.warning(self._init_error)
        except (RuntimeError, OSError) as e:
            self._init_error = f"unsloth 初始化异常: {e}"
            logger.warning(self._init_error)

        try:
            import torch  # type: ignore

            self._torch = torch
        except ImportError:
            logger.warning("torch 未安装, unsloth 推理不可用")

    def is_ready(self) -> bool:
        """unsloth 是否可用."""
        return self._unsloth is not None and self._torch is not None

    def get_gpu_info(self) -> dict[str, Any]:
        """获取 GPU 信息 (供 GLM5DecisionEngine 选择本地/API 模式)."""
        if not self.is_ready():
            return {"available": False, "reason": self._init_error or "未初始化"}
        try:
            torch = self._torch
            if torch.cuda.is_available():
                return {
                    "available": True,
                    "device_count": torch.cuda.device_count(),
                    "device_name": torch.cuda.get_device_name(0),
                    "memory_gb": torch.cuda.get_device_properties(0).total_memory / 1e9,
                }
            return {"available": False, "reason": "CUDA 不可用, 仅 CPU 模式"}
        except (RuntimeError, AttributeError) as e:
            return {"available": False, "reason": str(e)}

    def load_finetuned(self, model_tag: str) -> tuple[Any, Any] | None:
        """加载已微调模型.

        Args:
            model_tag: 模型标签, 如 "glm5-quant-v1"
                       实际路径: models/unsloth_finetuned/<model_tag>/

        Returns:
            (model, tokenizer) 或 None (加载失败时调用方降级到 GLM5Client)
        """
        if not self.is_ready():
            logger.warning("unsloth 不可用, 跳过本地模型加载")
            return None
        model_path = self.config.finetuned_dir / model_tag
        if not model_path.exists():
            logger.warning("微调模型不存在: %s", model_path)
            return None
        try:
            from unsloth import FastLanguageModel  # type: ignore

            model, tokenizer = FastLanguageModel.from_pretrained(
                model_name=str(model_path),
                max_seq_length=self.config.max_seq_length,
                load_in_4bit=self.config.load_in_4bit,
                dtype=self.config.dtype,
            )
            FastLanguageModel.for_inference(model)
            logger.info("✓ 微调模型已加载: %s", model_tag)
            return model, tokenizer
        except (ImportError, RuntimeError, OSError, ValueError) as e:
            logger.error("加载微调模型失败: %s, 降级到 API", e)
            return None

    def infer(
        self,
        model: Any,
        tokenizer: Any,
        prompt: str,
        system_prompt: str = "",
        max_new_tokens: int = 2048,
        temperature: float = 0.3,
    ) -> str:
        """本地推理 (供 GLM5DecisionEngine.make_decisions 调用).

        Returns:
            推理文本; 失败返回空字符串 (调用方降级)
        """
        if not self.is_ready():
            return ""
        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            inputs = tokenizer.apply_chat_template(
                messages, tokenize=True, add_generation_prompt=True, return_tensors="pt"
            )
            torch = self._torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
            inputs = inputs.to(device)

            from unsloth import FastLanguageModel  # type: ignore

            outputs = FastLanguageModel.generate(
                model=model,
                input_ids=inputs,
                max_new_tokens=max_new_tokens,
                temperature=temperature,
                use_cache=True,
            )
            text = tokenizer.batch_decode(outputs, skip_special_tokens=True)[0]
            return text
        except (RuntimeError, ValueError, AttributeError) as e:
            logger.error("unsloth 推理失败: %s", e)
            return ""

    def list_finetuned(self) -> list[str]:
        """列出所有已微调模型标签 (供 UI 选择)."""
        if not self.config.finetuned_dir.exists():
            return []
        return [p.name for p in self.config.finetuned_dir.iterdir() if p.is_dir()]


_unsloth_instance: UnslothAdapter | None = None


def get_unsloth_adapter(config: UnslothConfig | None = None) -> UnslothAdapter:
    """获取 unsloth 适配器单例."""
    global _unsloth_instance
    if _unsloth_instance is None:
        _unsloth_instance = UnslothAdapter(config)
    return _unsloth_instance


def is_unsloth_available() -> bool:
    """快速检查 unsloth 是否可用 (供 GLM5DecisionEngine 启动自检)."""
    return get_unsloth_adapter().is_ready()


if __name__ == "__main__":
    adapter = get_unsloth_adapter()
