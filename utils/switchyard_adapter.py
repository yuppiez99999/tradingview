"""Switchyard 适配器 — LLM 多模型路由增强 (W8.6 集成).

将 NVIDIA-NeMo/Switchyard (10_第三方项目/Switchyard) 接入 GLM5Client,
提供 OpenAI/Anthropic 兼容的多模型路由, 替代 LiteLLMRouter 部分场景.

集成点:
    - utils/glm5_client.py GLM5Client (薄包装)
    - utils/llm_gateway/litellm_router.py LiteLLMRouter

使用方式:
    from utils.switchyard_adapter import SwitchyardAdapter, get_switchyard_adapter

    adapter = get_switchyard_adapter()
    if adapter.is_ready():
        resp = adapter.route(messages=[{"role": "user", "content": "分析"}],
                             model="auto",  # 自动选择最优模型
                             strategy="cost_aware")

降级策略:
    - Switchyard 服务未启动 → is_ready()=False, 回退到 LiteLLMRouter
    - 路由失败 → 自动回退到 GLM5Client 原生调用
    - Python 绑定缺失 → 仅记录警告, 不阻断主流程

依赖路径:
    - 主系统: 28-终极量化交易系统8.4/
    - Switchyard 源码: 10_第三方项目/Switchyard/switchyard_rust/
    - Switchyard 服务: 默认 http://localhost:7777

集成日期: 2026-08-21 (v8.6, GitHub 周热门项目集成)
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_SWITCHYARD_SRC = (
    Path(__file__).resolve().parent.parent.parent / "10_第三方项目" / "Switchyard"
)


@dataclass
class SwitchyardConfig:
    """Switchyard 适配配置."""

    server_url: str = "http://localhost:7777"
    api_key: str = field(
        default_factory=lambda: os.environ.get("SWITCHYARD_API_KEY", "")
    )
    default_strategy: str = "cost_aware"  # cost_aware / quality_first / latency_first
    timeout_seconds: int = 30
    fallback_to_litellm: bool = True
    use_native_binding: bool = True  # True=使用 switchyard_rust Python 绑定

    # 模型路由映射 (主系统场景 → Switchyard 模型)
    scene_model_map: dict[str, str] = field(
        default_factory=lambda: {
            "intraday_decision": "qwen3-flash",  # 低延迟
            "rebalancing_analysis": "deepseek-v4-pro",  # 深度推理
            "macro_analysis": "glm-5.2",  # 宏观分析
            "report_generation": "qwen-plus",  # 结构化输出
            "light_analysis": "doubao-speed",  # 情感/分类
        }
    )


class SwitchyardAdapter:
    """Switchyard 适配器 — LLM 多模型路由.

    设计原则:
        - 不可变: 路由决策产生新请求, 不修改原消息
        - 优雅降级: 服务不可用时回退到 LiteLLMRouter
        - 薄包装: 不重写 Switchyard API, 仅做场景适配
    """

    def __init__(self, config: Optional[SwitchyardConfig] = None) -> None:
        self.config = config or SwitchyardConfig()
        self._binding: Any = None
        self._bridge_python: Optional[str] = None  # Python 3.12 路径 (跨版本桥接)
        self._init_error: Optional[str] = None
        self._load_binding()

    def _load_binding(self) -> None:
        """加载 Switchyard Python 绑定 (优先本版本, 降级到 Python 3.12 桥接)."""
        if not self.config.use_native_binding:
            return
        # 优先: 当前 Python 版本直接导入
        try:
            binding_path = _SWITCHYARD_SRC / "switchyard_rust"
            if binding_path.exists():
                str_path = str(binding_path)
                if str_path not in sys.path:
                    sys.path.insert(0, str_path)
            import switchyard_rust  # type: ignore

            self._binding = switchyard_rust
            logger.info("✓ Switchyard Python 绑定已加载 (本版本)")
            return
        except ImportError as e:
            logger.debug("本版本 switchyard_rust 不可用: %s, 尝试跨版本桥接", e)
        except (RuntimeError, OSError) as e:
            self._init_error = f"Switchyard 初始化异常: {e}"
            logger.warning(self._init_error)
            return

        # 降级: 查找 Python 3.12+ 通过 subprocess 桥接
        import shutil

        for py_cmd in ("python3.12", "python3.13", "python3.14"):
            py_path = shutil.which(py_cmd)
            if py_path:
                try:
                    import subprocess

                    result = subprocess.run(
                        [py_path, "-c", "import switchyard_rust; print('ok')"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    if result.returncode == 0 and "ok" in result.stdout:
                        self._bridge_python = py_path
                        logger.info("✓ Switchyard 跨版本桥接已激活 (%s)", py_path)
                        return
                except (subprocess.TimeoutExpired, OSError):
                    continue
        self._init_error = "Switchyard 绑定未安装 (需 Python 3.12+ 或 nemo-switchyard)"
        logger.warning(self._init_error)

    def is_ready(self) -> bool:
        """Switchyard 是否可用 (本版本绑定 或 跨版本桥接)."""
        return self._binding is not None or self._bridge_python is not None

    def route(
        self,
        messages: list[dict[str, str]],
        model: str = "auto",
        strategy: Optional[str] = None,
        max_tokens: int = 3000,
        temperature: float = 0.3,
        scene: Optional[str] = None,
    ) -> dict[str, Any]:
        """路由 LLM 请求.

        Args:
            messages: OpenAI 格式消息列表
            model: 模型名 / "auto" (按 strategy 自动选择)
            strategy: 路由策略 (None=使用默认)
            max_tokens: 最大生成 token
            temperature: 采样温度
            scene: 主系统场景 (用于模型映射)

        Returns:
            {"content": str, "model": str, "usage": dict, "latency_ms": int}
            失败时返回 {"content": "", "error": str}
        """
        if not self.is_ready():
            return {"content": "", "error": self._init_error or "Switchyard 不可用"}

        if scene and model == "auto":
            model = self.config.scene_model_map.get(scene, "auto")

        strategy = strategy or self.config.default_strategy

        try:
            # 跨版本桥接: 通过 subprocess 调用 Python 3.12
            if self._binding is None and self._bridge_python:
                import subprocess

                bridge_script = str(Path(__file__).parent / "switchyard_bridge.py")
                params = json.dumps(
                    {
                        "messages": messages,
                        "model": model,
                        "strategy": strategy,
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                        "server_url": self.config.server_url,
                        "api_key": self.config.api_key,
                        "timeout": self.config.timeout_seconds,
                    },
                    ensure_ascii=False,
                )
                result = subprocess.run(
                    [self._bridge_python, bridge_script, params],
                    capture_output=True,
                    text=True,
                    timeout=self.config.timeout_seconds + 10,
                    encoding="utf-8",
                )
                if result.returncode == 0:
                    response = json.loads(result.stdout)
                    logger.info(
                        "✓ Switchyard 桥接路由成功 (model=%s)",
                        response.get("model", model),
                    )
                    return response
                return {"content": "", "error": f"桥接失败: {result.stderr}"}

            server = self._binding.server
            response = server.route_request(
                messages=messages,
                model=model,
                strategy=strategy,
                max_tokens=max_tokens,
                temperature=temperature,
                server_url=self.config.server_url,
                api_key=self.config.api_key,
                timeout=self.config.timeout_seconds,
            )
            logger.info(
                "✓ Switchyard 路由成功 (model=%s, strategy=%s, latency=%sms)",
                response.get("model", model),
                strategy,
                response.get("latency_ms", 0),
            )
            return response
        except (
            RuntimeError,
            ValueError,
            ConnectionError,
            TimeoutError,
            subprocess.TimeoutExpired,
        ) as e:
            logger.warning("Switchyard 路由失败: %s, 应降级到 LiteLLMRouter", e)
            return {"content": "", "error": str(e)}

    def benchmark_models(
        self,
        test_prompts: list[str],
        models: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        """对比多个模型在测试 prompt 上的性能 (供 AICoordinator 选型).

        Returns:
            [{"model": str, "avg_latency_ms": int, "avg_cost": float, "quality_score": float}]
        """
        if not self.is_ready():
            return []
        results: list[dict[str, Any]] = []
        target_models = models or list(set(self.config.scene_model_map.values()))
        for model_name in target_models:
            latencies: list[int] = []
            for prompt in test_prompts:
                resp = self.route(
                    messages=[{"role": "user", "content": prompt}],
                    model=model_name,
                    strategy="quality_first",
                )
                if "latency_ms" in resp:
                    latencies.append(resp["latency_ms"])
            if latencies:
                results.append(
                    {
                        "model": model_name,
                        "avg_latency_ms": sum(latencies) // len(latencies),
                        "samples": len(latencies),
                    }
                )
        return results

    def get_status(self) -> dict[str, Any]:
        """获取 Switchyard 服务状态 (供 UI 系统概览)."""
        return {
            "binding_loaded": self._binding is not None,
            "server_url": self.config.server_url,
            "default_strategy": self.config.default_strategy,
            "scene_model_map": self.config.scene_model_map,
            "init_error": self._init_error,
        }


_switchyard_instance: Optional[SwitchyardAdapter] = None


def get_switchyard_adapter(
    config: Optional[SwitchyardConfig] = None,
) -> SwitchyardAdapter:
    """获取 Switchyard 适配器单例."""
    global _switchyard_instance
    if _switchyard_instance is None:
        _switchyard_instance = SwitchyardAdapter(config)
    return _switchyard_instance


def is_switchyard_available() -> bool:
    """快速检查 Switchyard 是否可用 (供 GLM5Client 启动自检)."""
    return get_switchyard_adapter().is_ready()


if __name__ == "__main__":
    adapter = get_switchyard_adapter()
