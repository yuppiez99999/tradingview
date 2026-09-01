"""ONNX Runtime 适配器 — ML 模型推理加速 (W8.6 集成).

将 microsoft/onnxruntime 接入主系统, 加速 LightGBM/TensorFlow 模型推理,
降低盘中决策延迟.

集成点:
    - lgb_enhanced_trainer.py  LightGBM 模型导出为 ONNX
    - utils/glm5_decision_engine.py  盘中决策推理加速
    - utils/tf_price_predictor.py  TF 模型 ONNX 推理

使用方式:
    from utils.onnxruntime_adapter import OnnxRuntimeAdapter, get_onnx_adapter

    adapter = get_onnx_adapter()
    if adapter.is_ready():
        session = adapter.load_session("models/lgb_enhanced/model.onnx")
        result = adapter.infer(session, {"input": features_array})

降级策略:
    - onnxruntime 未安装 → is_ready()=False, 回退到原生 LightGBM/TF 推理
    - ONNX 模型文件不存在 → 返回 None, 调用方使用原模型
    - 推理异常 → 返回空结果, 调用方降级

依赖路径:
    - 主系统: 28-终极量化交易系统8.4/
    - onnxruntime 源码: 10_第三方项目/onnxruntime/
    - ONNX 模型: models/onnx_exported/

集成日期: 2026-08-22 (v8.6, GitHub 今日热门项目集成)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ONNX_SRC = (
    Path(__file__).resolve().parent.parent.parent / "10_第三方项目" / "onnxruntime"
)
_MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "onnx_exported"


@dataclass
class OnnxConfig:
    """ONNX Runtime 适配配置."""

    providers: list[str] = field(
        default_factory=lambda: [
            "CUDAExecutionProvider",  # GPU 优先
            "CPUExecutionProvider",  # CPU 回退
        ]
    )
    intra_op_num_threads: int = 4
    inter_op_num_threads: int = 1
    graph_optimization_level: str = "all"  # all / basic / none
    model_dir: Path = field(default_factory=lambda: _MODEL_DIR)

    def __post_init__(self) -> None:
        if not self.model_dir.exists():
            self.model_dir.mkdir(parents=True, exist_ok=True)


class OnnxRuntimeAdapter:
    """ONNX Runtime 适配器 — ML 推理加速.

    设计原则:
        - 不可变: 推理产生新结果, 不修改输入
        - 优雅降级: onnxruntime 不可用时回退到原生推理
        - 薄包装: 不重写 onnxruntime API, 仅做场景适配
    """

    def __init__(self, config: OnnxConfig | None = None) -> None:
        self.config = config or OnnxConfig()
        self._ort: Any = None
        self._init_error: str | None = None
        self._load_ort()

    def _load_ort(self) -> None:
        """延迟加载 onnxruntime."""
        try:
            import onnxruntime as ort  # type: ignore

            self._ort = ort
            logger.info("✓ onnxruntime 已加载 (%s)", ort.__version__)
        except ImportError as e:
            self._init_error = f"onnxruntime 未安装: {e}"
            logger.warning(self._init_error)
        except (RuntimeError, OSError) as e:
            self._init_error = f"onnxruntime 初始化异常: {e}"
            logger.warning(self._init_error)

    def is_ready(self) -> bool:
        """onnxruntime 是否可用."""
        return self._ort is not None

    def get_available_providers(self) -> list[str]:
        """获取可用执行提供者 (供 UI 显示 GPU/CPU 状态)."""
        if not self.is_ready():
            return []
        try:
            return self._ort.get_available_providers()
        except (RuntimeError, AttributeError) as e:
            logger.warning("获取 providers 失败: %s", e)
            return []

    def load_session(self, model_path: str | Path) -> Any | None:
        """加载 ONNX 推理会话.

        Args:
            model_path: ONNX 模型路径 (.onnx)

        Returns:
            InferenceSession 或 None (加载失败时调用方降级到原生模型)
        """
        if not self.is_ready():
            logger.warning("onnxruntime 不可用, 跳过 ONNX 加载")
            return None
        path = Path(model_path)
        if not path.exists():
            logger.warning("ONNX 模型不存在: %s", path)
            return None
        try:
            ort = self._ort
            # 选择可用的 provider
            available = set(ort.get_available_providers())
            providers = [p for p in self.config.providers if p in available]
            if not providers:
                providers = ["CPUExecutionProvider"]

            session_options = ort.SessionOptions()
            session_options.intra_op_num_threads = self.config.intra_op_num_threads
            session_options.inter_op_num_threads = self.config.inter_op_num_threads
            if self.config.graph_optimization_level == "all":
                session_options.graph_optimization_level = (
                    ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                )
            elif self.config.graph_optimization_level == "basic":
                session_options.graph_optimization_level = (
                    ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
                )

            session = ort.InferenceSession(
                str(path),
                sess_options=session_options,
                providers=providers,
            )
            logger.info("✓ ONNX 会话已加载: %s (providers=%s)", path.name, providers)
            return session
        except (RuntimeError, OSError, ValueError) as e:
            logger.error("加载 ONNX 模型失败: %s, 降级到原生推理", e)
            return None

    def infer(self, session: Any, inputs: dict[str, Any]) -> dict[str, Any]:
        """ONNX 推理 (供 GLM5DecisionEngine / tf_price_predictor 调用).

        Args:
            session: InferenceSession
            inputs: {"input_name": ndarray}

        Returns:
            {"output_name": ndarray} 或 {} (失败时调用方降级)
        """
        if not self.is_ready() or session is None:
            return {}
        try:
            results = session.run(None, inputs)
            output_names = [o.name for o in session.get_outputs()]
            return dict(zip(output_names, results, strict=False))
        except (RuntimeError, ValueError, AttributeError) as e:
            logger.error("ONNX 推理失败: %s", e)
            return {}

    def get_model_info(self, session: Any) -> dict[str, Any]:
        """获取模型输入/输出信息 (供调试和 UI 显示)."""
        if session is None:
            return {}
        try:
            return {
                "inputs": [
                    {"name": i.name, "shape": i.shape, "type": i.type}
                    for i in session.get_inputs()
                ],
                "outputs": [
                    {"name": o.name, "shape": o.shape, "type": o.type}
                    for o in session.get_outputs()
                ],
            }
        except (RuntimeError, AttributeError) as e:
            logger.warning("获取模型信息失败: %s", e)
            return {}

    def list_models(self) -> list[str]:
        """列出所有已导出的 ONNX 模型 (供 UI 选择)."""
        if not self.config.model_dir.exists():
            return []
        return [p.name for p in self.config.model_dir.glob("*.onnx")]

    def get_status(self) -> dict[str, Any]:
        """获取 ONNX Runtime 状态 (供 UI 系统概览)."""
        return {
            "available": self.is_ready(),
            "version": self._ort.__version__ if self._ort else None,
            "providers": self.get_available_providers(),
            "model_count": len(self.list_models()),
            "init_error": self._init_error,
        }


_onnx_instance: OnnxRuntimeAdapter | None = None


def get_onnx_adapter(config: OnnxConfig | None = None) -> OnnxRuntimeAdapter:
    """获取 ONNX 适配器单例."""
    global _onnx_instance
    if _onnx_instance is None:
        _onnx_instance = OnnxRuntimeAdapter(config)
    return _onnx_instance


def is_onnx_available() -> bool:
    """快速检查 onnxruntime 是否可用."""
    return get_onnx_adapter().is_ready()


if __name__ == "__main__":
    adapter = get_onnx_adapter()
