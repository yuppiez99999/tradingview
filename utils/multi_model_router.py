"""
多模型场景路由器 — v5.8 核心架构升级
实现调研推荐方案: 场景路由 + 并行对冲 + 交叉验证 + 熔断器 + 置信度追踪

核心设计:
- 场景路由: 盘中决策→轻量模型, 再平衡→深度推理模型, 报告→创意模型
- 并行对冲: 主备模型同时发出请求，取先返回的结果 (降低延迟 50%+)
- 交叉验证: 双模型并行分析，交集采纳/分歧标记 (提高决策质量)
- 熔断器: 连续失败 3 次自动切换，5 分钟冷却后尝试恢复
- 审计日志: 每次决策记录模型来源、延迟、成本、置信度

支持模型: DeepSeek / GLM(智谱) / 豆包(火山引擎)

使用方式:
    from utils.multi_model_router import ModelRouter

    router = ModelRouter()
    result = router.route("intraday_decision", prompt, system_prompt)
"""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import yaml

logger = logging.getLogger(__name__)


# ==================== 数据类 ====================


@dataclass
class ModelCallResult:
    """单次模型调用结果"""

    provider: str
    model: str
    content: str
    latency_ms: float
    success: bool = True
    error: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class RoutingResult:
    """路由结果"""

    scene: str
    primary_result: ModelCallResult | None = None
    secondary_result: ModelCallResult | None = None
    merged_content: str = ""
    confidence: float = 0.0
    agreement: bool = False  # 双模型是否一致
    divergent_points: list[str] = field(default_factory=list)  # 分歧点
    latency_ms: float = 0.0
    cost_estimate: float = 0.0
    model_path: str = ""  # 实际使用的模型路径
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class CircuitBreaker:
    """熔断器"""

    provider: str
    max_failures: int = 3
    cooldown_seconds: int = 300
    consecutive_failures: int = 0
    last_failure_time: float = 0.0
    is_open: bool = False

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        self.last_failure_time = time.time()
        if self.consecutive_failures >= self.max_failures:
            self.is_open = True
            logger.warning(
                f"熔断器触发: {self.provider} (连续失败 {self.consecutive_failures} 次)"
            )

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.is_open = False

    def should_try_reset(self) -> bool:
        """检查冷却期是否已过，可以尝试恢复"""
        if not self.is_open:
            return False
        elapsed = time.time() - self.last_failure_time
        return elapsed >= self.cooldown_seconds

    def try_reset(self) -> bool:
        """尝试重置熔断器"""
        if self.should_try_reset():
            self.consecutive_failures = 0
            self.is_open = False
            logger.info(f"熔断器冷却完成，恢复 {self.provider}")
            return True
        return False


# ==================== 模型路由器 ====================


class ModelRouter:
    """
    多模型场景路由器

    核心功能:
    1. 按场景自动选择最优模型 (盘中/再平衡/宏观/报告/轻量)
    2. 并行对冲: 主备模型同时调用，取先返回
    3. 交叉验证: 双模型分析 + 交集/分歧检测
    4. 独立熔断器: 每个提供商独立跟踪故障
    5. 置信度追踪: 记录模型性能历史
    """

    def __init__(self, config_path: str | None = None):
        """
        初始化路由器

        Args:
            config_path: 配置文件路径，默认 config/model_routing.yaml
        """
        self.base_dir = Path(__file__).parent.parent

        # 加载配置
        if config_path is None:
            config_path = self.base_dir / "config" / "model_routing.yaml"

        with open(config_path, encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        # 初始化熔断器
        self.circuit_breakers: dict[str, CircuitBreaker] = {}
        providers = self.config.get("providers", {})
        for provider_key in providers:
            self.circuit_breakers[provider_key] = CircuitBreaker(provider=provider_key)

        # 审计日志
        self.audit_log: list[dict] = []
        self.audit_lock = threading.Lock()

        # API 会话缓存 (避免 SSL 握手重复)
        self._sessions: dict[str, requests.Session] = {}

        # 性能统计
        self.stats: dict[str, dict] = {
            provider_key: {"calls": 0, "successes": 0, "total_latency_ms": 0}
            for provider_key in providers
        }

        # 模型定价 (每百万 token, USD) — 2026-09-07 v3 用户指定选型 (国内直连三家)
        # 来源: 各厂商公开价 (估算值, 仅用于成本统计, 缺失时 _estimate_cost 返回 0)
        self.pricing = {
            ("deepseek", "deepseek-v4-pro"): (0.55, 2.19),
            ("deepseek", "deepseek-v4-chat"): (0.14, 0.55),
            ("deepseek", "deepseek-v4-flash"): (0.07, 0.28),  # 估算: V4 标准版约一半
            ("deepseek", "deepseek-v3.2"): (0.27, 1.10),
            ("qwen_max", "qwen3-max"): (0.25, 0.80),
            ("zhipuai", "glm-5.3"): (0.20, 0.65),
            ("zhipuai", "glm-5.2"): (0.14, 0.14),
            ("zhipuai", "glm-4.7-flash"): (0.00, 0.00),
            ("zhipuai", "glm-4-plus"): (0.14, 0.14),
            ("volcengine", "doubao-seed-1-6-251015"): (0.11, 0.27),
            ("volcengine", "doubao-pro-32k"): (0.11, 0.55),
        }

        logger.info(f"ModelRouter 初始化完成, 注册 {len(providers)} 个提供商")

    # ==================== 公开接口 ====================

    def route(
        self,
        scene: str,
        prompt: str,
        system_prompt: str | None = None,
        extra_context: dict | None = None,
        timeout: int | None = None,
    ) -> RoutingResult:
        """
        根据场景路由到最优模型

        Args:
            scene: 场景名称 (intraday_decision / rebalancing_analysis / macro_analysis / report_generation /
            light_analysis)
            prompt: 用户提示词
            system_prompt: 系统提示词
            extra_context: 额外上下文 (如基本面 RAG 数据)
            timeout: 超时秒数 (覆盖配置)

        Returns:
            RoutingResult 包含决策内容和元数据
        """
        scene_config = self.config.get("scenes", {}).get(scene)
        if not scene_config:
            raise ValueError(
                f"未知场景: {scene}, 可用场景: {list(self.config.get('scenes', {}).keys())}"
            )

        logger.info(f"[路由] 场景={scene}, 描述={scene_config.get('description', '')}")

        start_time = time.time()

        # 确定主模型
        primary_cfg = scene_config.get("primary", {})
        primary_provider = primary_cfg.get("provider")
        primary_model = primary_cfg.get("model")

        # 检查熔断器
        if self._is_circuit_open(primary_provider):
            logger.warning(f"[熔断] {primary_provider} 熔断中，尝试降级...")
            fallback_cfg = scene_config.get("fallback", {})
            if fallback_cfg:
                primary_cfg = fallback_cfg
                primary_provider = primary_cfg.get("provider")
                primary_model = primary_cfg.get("model")
                logger.info(f"[降级] 使用备选模型: {primary_provider}/{primary_model}")

        # 判断是否启用并行对冲或交叉验证
        parallel_enabled = scene_config.get("parallel_hedge", {}).get("enabled", False)
        cross_validation_enabled = scene_config.get("cross_validation", {}).get(
            "enabled", False
        )

        # 加载 RAG 上下文 (仅再平衡/宏观场景)
        rag_context = self._build_rag_context(scene, extra_context)

        result = RoutingResult(scene=scene)

        if parallel_enabled:
            # 盘中决策: 并行对冲模式
            result = self._execute_parallel_hedge(
                scene_config, prompt, system_prompt, rag_context, timeout, start_time
            )
        elif cross_validation_enabled:
            # 再平衡/宏观: 双模型交叉验证
            result = self._execute_cross_validation(
                scene_config, prompt, system_prompt, rag_context, timeout, start_time
            )
        else:
            # 单模型模式
            primary_result = self._call_model(
                primary_provider,
                primary_model,
                prompt,
                system_prompt,
                primary_cfg.get("temperature", 0.3),
                primary_cfg.get("max_tokens", 2000),
                timeout or primary_cfg.get("timeout", 30),
            )
            result.primary_result = primary_result
            result.merged_content = primary_result.content if primary_result else ""
            result.confidence = 0.7
            result.model_path = f"{primary_provider}/{primary_model}"

        result.latency_ms = (time.time() - start_time) * 1000
        result.cost_estimate = self._estimate_cost(
            primary_provider, primary_model, len(prompt), len(result.merged_content)
        )

        # 记录审计日志
        self._log_audit(result)

        logger.info(
            f"[路由] 完成, 延迟={result.latency_ms:.0f}ms, "
            f"模型={result.model_path}, 置信度={result.confidence:.2f}"
        )

        return result

    # ==================== 并行对冲执行 ====================

    def _execute_parallel_hedge(
        self,
        scene_config: dict,
        prompt: str,
        system_prompt: str | None,
        rag_context: str,
        timeout: int | None,
        start_time: float,
    ) -> RoutingResult:
        """并行对冲模式: 主备同时发出，取先返回"""
        result = RoutingResult(scene="intraday_decision")

        primary_cfg = scene_config["primary"]
        hedge_cfg = scene_config["parallel_hedge"]
        secondary_cfg = hedge_cfg["secondary"]

        results = []
        errors = []

        # 并行执行
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {}

            # 主模型
            fut_primary = executor.submit(
                self._call_model,
                primary_cfg["provider"],
                primary_cfg["model"],
                prompt + rag_context,
                system_prompt,
                primary_cfg["temperature"],
                primary_cfg["max_tokens"],
                timeout or primary_cfg.get("timeout", 15),
            )
            futures[fut_primary] = f"{primary_cfg['provider']}/{primary_cfg['model']}"

            # 并行对冲模型
            if not self._is_circuit_open(secondary_cfg["provider"]):
                fut_secondary = executor.submit(
                    self._call_model,
                    secondary_cfg["provider"],
                    secondary_cfg["model"],
                    prompt + rag_context,
                    system_prompt,
                    secondary_cfg["temperature"],
                    secondary_cfg["max_tokens"],
                    timeout or secondary_cfg.get("timeout", 15),
                )
                futures[fut_secondary] = (
                    f"{secondary_cfg['provider']}/{secondary_cfg['model']}"
                )

            # 等待任一完成
            try:
                for future in as_completed(futures, timeout=timeout or 30):
                    model_path = futures[future]
                    try:
                        call_result = future.result(timeout=5)
                        if call_result and call_result.success:
                            results.append(call_result)
                            # 如果已经有一个快速返回，取消剩余
                            if len(results) == 1:
                                for f in futures:
                                    if not f.done():
                                        f.cancel()
                                break
                    except (
                        ValueError,
                        TypeError,
                        KeyError,
                        AttributeError,
                        OSError,
                    ) as e:
                        errors.append(f"{model_path}: {e}")
            except FuturesTimeoutError:
                errors.append("所有模型调用超时")

        # 处理结果
        if results:
            # 优先取先返回的
            result.primary_result = results[0]
            result.merged_content = results[0].content
            result.model_path = f"{results[0].provider}/{results[0].model}"

            # 如果两个都返回了，检测一致性
            if len(results) >= 2:
                result.agreement = self._check_agreement(
                    results[0].content, results[1].content
                )
                result.confidence = 0.85 if result.agreement else 0.6
                if not result.agreement:
                    result.divergent_points = ["双模型意见不一致，建议人工审核"]
            else:
                result.confidence = 0.7
        else:
            result.merged_content = f"[错误] 所有模型调用失败: {'; '.join(errors)}"
            result.confidence = 0.0
            result.model_path = "none"

        return result

    # ==================== 交叉验证执行 ====================

    def _execute_cross_validation(
        self,
        scene_config: dict,
        prompt: str,
        system_prompt: str | None,
        rag_context: str,
        timeout: int | None,
        start_time: float,
    ) -> RoutingResult:
        """交叉验证模式: 双模型并行分析，交集采纳/分歧标记"""
        result = RoutingResult(
            scene=scene_config.get("description", "rebalancing_analysis")
        )

        primary_cfg = scene_config["primary"]
        cv_cfg = scene_config.get("cross_validation", {})
        secondary_cfg = cv_cfg.get("secondary", {})

        timeout_val = timeout or primary_cfg.get("timeout", 60)

        results = {}

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {}

            # 主模型 DeepSeek V4 Pro → 逻辑推理
            if not self._is_circuit_open(primary_cfg["provider"]):
                deep_prompt = (
                    prompt
                    + rag_context
                    + "\n\n重点: 请进行深度多因子逻辑推理和多情景模拟分析。"
                )
                futures["primary"] = executor.submit(
                    self._call_model,
                    primary_cfg["provider"],
                    primary_cfg["model"],
                    deep_prompt,
                    system_prompt,
                    primary_cfg["temperature"],
                    primary_cfg["max_tokens"],
                    timeout_val,
                )

            # 交叉验证模型 豆包Pro → 财报细读和消息面
            if not self._is_circuit_open(secondary_cfg["provider"]):
                cv_prompt = (
                    prompt + rag_context + "\n\n重点: 请侧重财报数据细读和消息面评估。"
                )
                futures["secondary"] = executor.submit(
                    self._call_model,
                    secondary_cfg["provider"],
                    secondary_cfg["model"],
                    cv_prompt,
                    system_prompt,
                    secondary_cfg["temperature"],
                    secondary_cfg["max_tokens"],
                    timeout_val,
                )

            # 收集结果
            for key, future in futures.items():
                try:
                    call_result = future.result(timeout=timeout_val + 10)
                    if call_result and call_result.success:
                        results[key] = call_result
                except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
                    logger.warning(f"交叉验证 {key} 失败: {e}")

        # 合成结果
        primary_result = results.get("primary")
        secondary_result = results.get("secondary")

        result.primary_result = primary_result
        result.secondary_result = secondary_result

        if primary_result and secondary_result:
            # 双模型都成功 → 交叉验证
            result.model_path = (
                f"{primary_result.provider}/{primary_result.model}"
                f" + {secondary_result.provider}/{secondary_result.model}"
            )
            result.agreement = self._check_agreement(
                primary_result.content, secondary_result.content
            )

            if result.agreement:
                result.confidence = 0.9
                result.merged_content = self._merge_agreed(
                    primary_result.content, secondary_result.content
                )
            else:
                result.confidence = 0.65
                divergent = self._find_divergent_points(
                    primary_result.content, secondary_result.content
                )
                result.divergent_points = divergent
                result.merged_content = self._merge_divergent(
                    primary_result.content, secondary_result.content, divergent
                )

        elif primary_result:
            result.model_path = f"{primary_result.provider}/{primary_result.model}"
            result.confidence = 0.7
            result.merged_content = primary_result.content

        elif secondary_result:
            result.model_path = f"{secondary_result.provider}/{secondary_result.model}"
            result.confidence = 0.7
            result.merged_content = secondary_result.content

        else:
            result.merged_content = "[错误] 交叉验证所有模型均失败"
            result.confidence = 0.0

        return result

    # ==================== 模型调用核心 ====================

    def _call_model(
        self,
        provider: str,
        model: str,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2000,
        timeout: int = 30,
    ) -> ModelCallResult | None:
        """
        调用指定模型 API

        Args:
            provider: 提供商 (deepseek / zhipuai / volcengine)
            model: 模型名称
            prompt: 用户提示
            system_prompt: 系统提示
            temperature: 温度
            max_tokens: 最大输出 token
            timeout: 超时

        Returns:
            ModelCallResult 或 None
        """
        provider_config = self.config.get("providers", {}).get(provider)
        if not provider_config:
            logger.error(f"未知提供商: {provider}")
            return None

        api_key = os.environ.get(provider_config.get("api_key_env", ""))
        if not api_key:
            logger.error(
                f"缺少 API Key: {provider}, 请设置环境变量 {provider_config.get('api_key_env', '')}"
            )
            return None

        api_base = provider_config.get("api_base", "")
        start_time = time.time()

        try:
            # 获取或创建 Session
            session = self._get_session(provider)

            # 构建消息
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            # 根据提供商构建请求
            if provider == "volcengine":
                # 火山引擎豆包 (使用 Responses API 格式)
                result = self._call_volcengine(
                    session, model, messages, temperature, max_tokens, timeout
                )
            else:
                # DeepSeek / Zhipu 使用标准 OpenAI 兼容格式
                result = self._call_openai_compatible(
                    session,
                    provider,
                    api_base,
                    api_key,
                    model,
                    messages,
                    temperature,
                    max_tokens,
                    timeout,
                )

            latency_ms = (time.time() - start_time) * 1000

            if result:
                self.circuit_breakers[provider].record_success()
                self._update_stats(provider, True, latency_ms)
                return ModelCallResult(
                    provider=provider,
                    model=model,
                    content=result.get("content", ""),
                    latency_ms=latency_ms,
                    success=True,
                    usage=result.get("usage", {}),
                )
            self.circuit_breakers[provider].record_failure()
            self._update_stats(provider, False, latency_ms)
            return ModelCallResult(
                provider=provider,
                model=model,
                content="",
                latency_ms=latency_ms,
                success=False,
                error="API 返回空",
            )

        except (
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            OSError,
            RuntimeError,
        ) as e:
            latency_ms = (time.time() - start_time) * 1000
            self.circuit_breakers[provider].record_failure()
            self._update_stats(provider, False, latency_ms)
            logger.error(f"模型调用失败 {provider}/{model}: {e}")
            return ModelCallResult(
                provider=provider,
                model=model,
                content="",
                latency_ms=latency_ms,
                success=False,
                error=str(e),
            )

    def _call_openai_compatible(
        self,
        session: requests.Session,
        provider: str,
        api_base: str,
        api_key: str,
        model: str,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        timeout: int,
    ) -> dict | None:
        """调用 OpenAI 兼容格式的 API (DeepSeek / Zhipu / Qwen)"""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        response = session.post(
            api_base, headers=headers, json=payload, timeout=max(timeout, 30)
        )
        response.raise_for_status()
        data = response.json()

        choice = data.get("choices", [{}])[0]
        content = choice.get("message", {}).get("content", "") or ""

        return {"content": content, "usage": data.get("usage", {})}

    def _call_volcengine(
        self,
        session: requests.Session,
        model: str,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        timeout: int,
    ) -> dict | None:
        """调用火山引擎豆包 API (Responses 格式)"""
        api_key = os.environ.get("VOLCENGINE_API_KEY", "")
        api_base = "https://ark.cn-beijing.volces.com/api/v3/responses"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # 构建豆包专用消息格式
        input_messages = []
        for msg in messages:
            input_messages.append(
                {
                    "role": msg["role"],
                    "content": [{"type": "input_text", "text": msg["content"]}],
                }
            )

        payload = {
            "model": model,
            "input": input_messages,
            "parameters": {
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        }

        response = session.post(
            api_base, headers=headers, json=payload, timeout=max(timeout, 30)
        )
        response.raise_for_status()
        data = response.json()

        # 解析豆包返回
        content = ""
        for output in data.get("output", []):
            if output.get("type") == "message":
                for content_item in output.get("content", []):
                    if content_item.get("type") == "output_text":
                        content = content_item.get("text", "")

        return {
            "content": content,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    # ==================== 辅助方法 ====================

    def _is_circuit_open(self, provider: str) -> bool:
        """检查熔断器是否开启"""
        cb = self.circuit_breakers.get(provider)
        if cb and cb.is_open:
            # 检查是否可以尝试恢复
            cb.try_reset()
            return cb.is_open
        return False

    def _build_rag_context(self, scene: str, extra_context: dict | None) -> str:
        """构建 RAG 上下文"""
        rag_config = self.config.get("rag", {})
        disabled = rag_config.get("disabled_scenes", [])

        if scene in disabled:
            return ""  # 盘中决策不使用 RAG

        if extra_context is None:
            return ""

        rag_parts = []

        # 基本面数据
        fundamental_data = extra_context.get("fundamental_data", {})
        if fundamental_data:
            rag_parts.append("\n\n【基本面数据 (Wind MCP)】")
            for code, info in fundamental_data.items():
                rag_parts.append(
                    f"- {code} {info.get('名称', '')}: PE={info.get('市盈率TTM', 'N/A')}, "
                    f"PB={info.get('市净率', 'N/A')}, ROE={info.get('ROE(%)', 'N/A')}%, "
                    f"营收同比={info.get('营收同比(%)', 'N/A')}%, "
                    f"利润同比={info.get('利润同比(%)', 'N/A')}%, "
                    f"资产负债率={info.get('资产负债率(%)', 'N/A')}%"
                )

        # 指数数据
        index_data = extra_context.get("index_data", {})
        if index_data:
            rag_parts.append("\n【实时指数 (Wind MCP)】")
            for name, info in index_data.items():
                rag_parts.append(
                    f"- {name}: {info.get('收盘', 'N/A')} ({info.get('涨跌幅', 'N/A')})"
                )

        # 宏观指标
        macro_data = extra_context.get("macro_indicators", {})
        if macro_data:
            rag_parts.append("\n【宏观指标】")
            for key, val in macro_data.items():
                rag_parts.append(f"- {key}: {val}")

        return "\n".join(rag_parts) if rag_parts else ""

    def _check_agreement(self, content1: str, content2: str) -> bool:
        """
        检查两个模型输出是否一致 (简化版)

        未来可替换为更精确的 NLP 语义一致性检测
        """
        if not content1 or not content2:
            return False

        # 简单关键词重叠检测
        c1_lower = content1.lower()
        c2_lower = content2.lower()

        # 检查关键动作词是否一致
        action_keywords = [
            "buy",
            "sell",
            "hold",
            "reduce",
            "买入",
            "卖出",
            "持有",
            "减仓",
        ]
        c1_actions = set(a for a in action_keywords if a in c1_lower[:500])
        c2_actions = set(a for a in action_keywords if a in c2_lower[:500])

        if c1_actions and c2_actions:
            return c1_actions == c2_actions

        # 回退到长度比率
        min_len = min(len(content1), len(content2))
        if min_len == 0:
            return False

        # 简单的内容重叠率
        words1 = set(content1[:2000].split())
        words2 = set(content2[:2000].split())
        overlap = len(words1 & words2) / max(len(words1), len(words2), 1)

        return overlap > 0.3

    def _find_divergent_points(self, content1: str, content2: str) -> list[str]:
        """识别分歧点"""
        divergences = []

        # 检查动作分歧
        if "买入" in content1 and "卖出" in content2:
            divergences.append("模型1建议买入，模型2建议卖出 - 方向分歧")
        if "加仓" in content1 and "减仓" in content2:
            divergences.append("仓位调整方向分歧")

        # 检查风险判断分歧
        if "高风险" in content1 and "低风险" in content2:
            divergences.append("风险评估等级分歧")

        if not divergences:
            divergences.append("模型输出存在细微差异，建议人工审核")

        return divergences

    def _merge_agreed(self, content1: str, content2: str) -> str:
        """合并一致的结果 (取更详细的)"""
        return (
            content1 if len(content1) >= len(content2) else content2
        ) + "\n\n---\n双模型交叉验证：一致 ✅ (高置信度)"

    def _merge_divergent(
        self, content1: str, content2: str, divergences: list[str]
    ) -> str:
        """合并有分歧的结果"""
        merged = "## 主模型分析 (DeepSeek V4 Pro)\n\n"
        merged += content1
        merged += "\n\n---\n## 交叉验证模型 (豆包 Pro)\n\n"
        merged += content2
        merged += "\n\n---\n## ⚠️ 分歧点 (需人工复核)\n\n"
        for i, d in enumerate(divergences, 1):
            merged += f"{i}. {d}\n"
        merged += "\n**由于存在分歧，以上所有建议仅供参考，请人工决策。**"
        return merged

    def _estimate_cost(
        self, provider: str, model: str, prompt_tokens: int, completion_tokens: int
    ) -> float:
        """估算调用成本 (USD)"""
        prices = self.pricing.get((provider, model))
        if not prices:
            return 0.0

        input_price, output_price = prices
        # 粗略估算 token 数: 中文约 1.5 字符/token
        est_input_tokens = max(prompt_tokens, 0) / 1.5
        est_output_tokens = max(completion_tokens, 0) / 1.5

        cost = (est_input_tokens / 1_000_000) * input_price + (
            est_output_tokens / 1_000_000
        ) * output_price
        return round(cost, 6)

    def _get_session(self, provider: str) -> requests.Session:
        """获取或创建 HTTP Session"""
        if provider not in self._sessions:
            session = requests.Session()
            session.trust_env = False  # 禁用系统代理
            self._sessions[provider] = session
        return self._sessions[provider]

    def _update_stats(self, provider: str, success: bool, latency_ms: float) -> None:
        """更新性能统计"""
        if provider in self.stats:
            self.stats[provider]["calls"] += 1
            if success:
                self.stats[provider]["successes"] += 1
            self.stats[provider]["total_latency_ms"] += latency_ms

    def _log_audit(self, result: RoutingResult) -> None:
        """记录审计日志"""
        entry = {
            "timestamp": result.timestamp,
            "scene": result.scene,
            "model_path": result.model_path,
            "latency_ms": result.latency_ms,
            "confidence": result.confidence,
            "agreement": result.agreement,
            "divergent_points": len(result.divergent_points),
            "cost_estimate": result.cost_estimate,
        }
        with self.audit_lock:
            self.audit_log.append(entry)
            # 保留最近 1000 条
            if len(self.audit_log) > 1000:
                self.audit_log = self.audit_log[-1000:]

    def get_stats(self) -> dict[str, Any]:
        """获取性能统计"""
        result = {}
        for provider, s in self.stats.items():
            calls = s["calls"]
            if calls > 0:
                result[provider] = {
                    "total_calls": calls,
                    "success_rate": f"{s['successes'] / calls * 100:.1f}%",
                    "avg_latency_ms": (
                        round(s["total_latency_ms"] / calls, 1) if calls > 0 else 0
                    ),
                    "circuit_breaker": (
                        "OPEN"
                        if self.circuit_breakers.get(
                            provider, CircuitBreaker(provider=provider)
                        ).is_open
                        else "CLOSED"
                    ),
                }
        return result

    def get_audit_log(self, limit: int = 20) -> list[dict]:
        """获取最近审计日志"""
        with self.audit_lock:
            return self.audit_log[-limit:]

    def export_stats_report(self) -> str:
        """导出统计报告"""
        lines = ["# AI 模型路由统计报告", ""]
        lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")
        lines.append("## 各模型性能")
        lines.append("")
        lines.append("| 提供商 | 总调用 | 成功率 | 平均延迟 | 熔断状态 |")
        lines.append("|--------|--------|--------|----------|----------|")

        stats = self.get_stats()
        for provider, s in stats.items():
            cb_status = s.get("circuit_breaker", "CLOSED")
            lines.append(
                f"| {provider} | {s['total_calls']} | {s['success_rate']} | {s['avg_latency_ms']}ms | {cb_status} |"
            )

        lines.append("")
        lines.append("## 最近决策记录")
        lines.append("")

        recent = self.get_audit_log(10)
        for entry in recent:
            lines.append(
                f"- {entry['timestamp'][:19]} | {entry['scene']} | {entry['model_path']} | "
                f"{entry['latency_ms']:.0f}ms | 置信度={entry['confidence']:.2f}"
            )

        return "\n".join(lines)


# ==================== 全局单例 ====================

_router_instance: ModelRouter | None = None


def get_model_router() -> ModelRouter:
    """获取全局 ModelRouter 单例"""
    global _router_instance
    if _router_instance is None:
        _router_instance = ModelRouter()
    return _router_instance


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )

    logger.info("=" * 60)
    logger.info("多模型路由器测试")
    logger.info("=" * 60)

    router = ModelRouter()

    # 测试场景路由配置
    scenes = [
        "intraday_decision",
        "rebalancing_analysis",
        "macro_analysis",
        "report_generation",
        "light_analysis",
    ]

    for scene in scenes:
        logger.info(f"\n--- 场景: {scene} ---")
        cfg = router.config.get("scenes", {}).get(scene, {})
        primary = cfg.get("primary", {})
        logger.info(f"  主模型: {primary.get('provider')}/{primary.get('model')}")
        logger.info(
            f"  温度: {primary.get('temperature')}, max_tokens: {primary.get('max_tokens')}"
        )

        if cfg.get("parallel_hedge", {}).get("enabled"):
            sec = cfg["parallel_hedge"]["secondary"]
            logger.info(f"  并行对冲: {sec.get('provider')}/{sec.get('model')}")

        if cfg.get("cross_validation", {}).get("enabled"):
            sec = cfg["cross_validation"]["secondary"]
            logger.info(f"  交叉验证: {sec.get('provider')}/{sec.get('model')}")

    logger.info("\n--- 熔断器状态 ---")
    for provider, cb in router.circuit_breakers.items():
        logger.info(
            f"  {provider}: {'OPEN' if cb.is_open else 'CLOSED'} (failures={cb.consecutive_failures})"
        )

    logger.info("\n" + "=" * 60)
    logger.info("配置验证完成 (实际 API 调用需设置环境变量)")
    logger.info("=" * 60)
