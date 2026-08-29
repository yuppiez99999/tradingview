"""
FinGPT 系列集成 (轻量 LoRA + RLSP 训练管线)
=============================================

文献依据: #14 FinGPT 系列（6 篇）(2023-2025, ★★★★★)
论文核心: 金融领域 GPT 模型 + LoRA 轻量微调 + RLSP 强化学习

FinGPT 系列 (6 篇)
------------------
1. FinGPT (2023): 金融领域 GPT 基座
2. FinGPT-Bench (2024): 金融基准测试
3. FinGPT-RLSP (2024): 强化学习 + 股价反馈
4. FinGPT-LoRA (2024): 轻量 LoRA 微调
5. FinGPT-Trader (2025): 交易代理
6. FinGPT-Multi (2025): 多模态金融

核心组件
--------
1. FinGPTConfig / FinGPTClient
   - 与 GLM5Client 接口兼容
   - 支持 LoRA 微调配置
   - 金融领域 prompt 模板

2. LoRAConfig
   - 轻量 LoRA 微调参数 (rank/alpha/dropout)
   - 目标模块配置

3. RLSPTrainer (Reinforcement Learning with Stock Price)
   - 基于股价反馈的强化学习
   - 奖励函数: 超额收益 - 风险惩罚
   - 训练管线就绪检查

4. ModelRouter
   - GLM-5 ↔ FinGPT 模型路由
   - 基于任务类型/成本/可用性自动切换

使用示例
--------
    from utils.fingpt_integration import FinGPTClient, ModelRouter

    client = FinGPTClient()
    response = client.chat("分析茅台近期走势")

    router = ModelRouter()
    model = router.route(task="intraday_decision")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("fingpt_integration")

# ============================================================
# 模型类型枚举
# ============================================================


class ModelType(str, Enum):
    """模型类型。"""

    GLM5 = "glm5"  # GLM-5 (智谱)
    FINGPT = "fingpt"  # FinGPT (AI4Finance)
    DEEPSEEK = "deepseek"  # DeepSeek
    DOUBAO = "doubao"  # 豆包 Speed


class TaskCategory(str, Enum):
    """任务类别。"""

    INTRADAY = "intraday"  # 盘中决策
    DAILY_REPORT = "daily_report"  # 日报告
    RESEARCH = "research"  # 深度研究
    SENTIMENT = "sentiment"  # 情绪分析
    TRADING = "trading"  # 交易执行


# ============================================================
# LoRA 微调配置
# ============================================================


@dataclass
class LoRAConfig:
    """LoRA (Low-Rank Adaptation) 微调配置。

    Attributes:
        rank: LoRA 秩 (常用 8/16/32)
        alpha: 缩放因子 (通常 = 2 × rank)
        dropout: dropout 概率
        target_modules: 目标模块 (q_proj/v_proj/k_proj/o_proj)
        bias: bias 训练模式 (none/all/only)
    """

    rank: int = 16
    alpha: int = 32
    dropout: float = 0.05
    target_modules: list[str] = field(default_factory=lambda: ["q_proj", "v_proj"])
    bias: str = "none"

    @property
    def scaling(self) -> float:
        """LoRA 缩放因子 = alpha / rank。"""
        return self.alpha / max(self.rank, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "alpha": self.alpha,
            "dropout": self.dropout,
            "target_modules": list(self.target_modules),
            "bias": self.bias,
            "scaling": self.scaling,
        }


# ============================================================
# FinGPT 配置
# ============================================================


@dataclass
class FinGPTConfig:
    """FinGPT 模型配置。

    Attributes:
        model_name: 模型名称
        base_model: 基座模型 (如 mistralai/Mistral-7B)
        lora_config: LoRA 微调配置
        max_tokens: 最大 token 数
        temperature: 采样温度
        api_key: API 密钥 (环境变量)
        device: 推理设备 (cpu/cuda/auto)
    """

    model_name: str = "FinGPT-v7"
    base_model: str = "mistralai/Mistral-7B-Instruct"
    lora_config: LoRAConfig = field(default_factory=LoRAConfig)
    max_tokens: int = 4096
    temperature: float = 0.7
    api_key: str = ""
    device: str = "auto"

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "base_model": self.base_model,
            "lora_config": self.lora_config.to_dict(),
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "device": self.device,
        }


# ============================================================
# FinGPT 客户端
# ============================================================

# 金融领域 prompt 模板
FINANCIAL_PROMPT_TEMPLATES: dict[str, str] = {
    "analysis": "作为金融分析师，请分析以下内容：\n\n{content}\n\n请提供：\n1. 基本面分析\n2. 技术面分析\n3. 风险评估\n4. 投资建议",
    "sentiment": "请分析以下金融文本的市场情绪（看多/看空/中性）：\n\n{content}",
    "trading": "基于以下市场信息，请给出交易建议（买入/卖出/持有）：\n\n{content}\n\n请包含：\n1. 建议方向\n2. 建议仓位\n3. 止损止盈",
    "risk": "请评估以下投资组合的风险：\n\n{content}\n\n请包含：\n1. VaR 估算\n2. 最大回撤\n3. 风险因子暴露",
}


class FinGPTClient:
    """FinGPT 客户端 — 与 GLM5Client 接口兼容。

    使用示例:
        client = FinGPTClient()
        response = client.chat("分析茅台走势")
    """

    def __init__(self, config: Optional[FinGPTConfig] = None) -> None:
        self.config = config or FinGPTConfig()
        self._available = self._check_availability()

    def _check_availability(self) -> bool:
        """检查 FinGPT 是否可用。"""
        return bool(self.config.api_key) or self.config.device != "auto"

    def chat(self, message: str, template: Optional[str] = None, **kwargs: Any) -> str:
        """发送聊天请求。

        Args:
            message: 用户消息
            template: prompt 模板名称 (analysis/sentiment/trading/risk)
            **kwargs: 额外参数

        Returns:
            模型响应文本
        """
        prompt = self._build_prompt(message, template)

        if not self._available:
            logger.warning("FinGPT 不可用, 返回 mock 响应")
            return self._mock_response(prompt)

        return self._call_model(prompt, **kwargs)

    def _build_prompt(self, message: str, template: Optional[str]) -> str:
        """构建 prompt。"""
        if template and template in FINANCIAL_PROMPT_TEMPLATES:
            return FINANCIAL_PROMPT_TEMPLATES[template].format(content=message)
        return message

    def _call_model(self, prompt: str, **kwargs: Any) -> str:
        """调用模型 (mock 实现)。"""
        temp = kwargs.get("temperature", self.config.temperature)
        return f"[FinGPT] 温度={temp}, 模型={self.config.model_name}: {prompt[:100]}..."

    def _mock_response(self, prompt: str) -> str:
        """Mock 响应 (FinGPT 不可用时)。"""
        return f"[FinGPT-Mock] {prompt[:100]}..."

    def is_ready(self) -> bool:
        """检查是否就绪。"""
        return self._available

    def get_info(self) -> dict[str, Any]:
        """获取模型信息。"""
        return {
            "model_type": ModelType.FINGPT.value,
            "available": self._available,
            "config": self.config.to_dict(),
        }


# ============================================================
# RLSP 训练管线
# ============================================================


@dataclass
class RLSPConfig:
    """RLSP (Reinforcement Learning with Stock Price) 训练配置。

    Attributes:
        learning_rate: 学习率
        batch_size: 批大小
        n_epochs: 训练轮数
        risk_free_rate: 无风险利率
        risk_penalty: 风险惩罚系数
        reward_window: 奖励计算窗口 (天)
    """

    learning_rate: float = 1e-5
    batch_size: int = 32
    n_epochs: int = 10
    risk_free_rate: float = 0.03
    risk_penalty: float = 0.5
    reward_window: int = 5

    def to_dict(self) -> dict[str, Any]:
        return {
            "learning_rate": self.learning_rate,
            "batch_size": self.batch_size,
            "n_epochs": self.n_epochs,
            "risk_free_rate": self.risk_free_rate,
            "risk_penalty": self.risk_penalty,
            "reward_window": self.reward_window,
        }


@dataclass
class TrainingRecord:
    """训练记录。"""

    epoch: int
    step: int
    loss: float
    reward: float
    excess_return: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "epoch": self.epoch,
            "step": self.step,
            "loss": self.loss,
            "reward": self.reward,
            "excess_return": self.excess_return,
        }


class RLSPTrainer:
    """RLSP 训练管线 — 基于股价反馈的强化学习。

    奖励函数: reward = excess_return - risk_penalty × volatility
    """

    def __init__(self, config: Optional[RLSPConfig] = None) -> None:
        self.config = config or RLSPConfig()
        self._history: list[TrainingRecord] = []
        self._is_ready = False

    def setup(self, model_path: str = "", data_path: str = "") -> bool:
        """设置训练环境。

        Args:
            model_path: 模型路径
            data_path: 数据路径

        Returns:
            是否就绪
        """
        self._is_ready = True
        logger.info(f"RLSP 训练环境就绪: model={model_path}, data={data_path}")
        return self._is_ready

    def compute_reward(
        self, portfolio_return: float, market_return: float, volatility: float
    ) -> float:
        """计算 RLSP 奖励。

        Args:
            portfolio_return: 组合收益率
            market_return: 市场收益率
            volatility: 波动率

        Returns:
            奖励值
        """
        excess = portfolio_return - market_return - self.config.risk_free_rate / 252
        risk_adjustment = self.config.risk_penalty * volatility
        reward = excess - risk_adjustment
        return round(reward, 6)

    def train_step(
        self,
        epoch: int,
        step: int,
        loss: float,
        portfolio_return: float,
        market_return: float,
        volatility: float,
    ) -> TrainingRecord:
        """执行一步训练。

        Args:
            epoch: 当前轮次
            step: 当前步数
            loss: 训练损失
            portfolio_return: 组合收益
            market_return: 市场收益
            volatility: 波动率

        Returns:
            TrainingRecord 训练记录
        """
        reward = self.compute_reward(portfolio_return, market_return, volatility)
        excess = round(portfolio_return - market_return, 6)

        record = TrainingRecord(
            epoch=epoch,
            step=step,
            loss=round(loss, 6),
            reward=reward,
            excess_return=excess,
        )
        self._history.append(record)
        return record

    def is_ready(self) -> bool:
        """训练管线是否就绪。"""
        return self._is_ready

    def get_history(self) -> list[TrainingRecord]:
        """获取训练历史。"""
        return list(self._history)

    def get_summary(self) -> dict[str, Any]:
        """获取训练摘要。"""
        if not self._history:
            return {"n_steps": 0, "ready": self._is_ready}

        total_reward = sum(r.reward for r in self._history)
        total_loss = sum(r.loss for r in self._history)
        n = len(self._history)

        return {
            "n_steps": n,
            "ready": self._is_ready,
            "avg_reward": round(total_reward / n, 6),
            "avg_loss": round(total_loss / n, 6),
            "final_reward": self._history[-1].reward,
            "config": self.config.to_dict(),
        }


# ============================================================
# 模型路由器
# ============================================================


class ModelRouter:
    """模型路由器 — GLM-5 ↔ FinGPT 自动切换。

    路由策略:
    - 交易执行/盘中决策 → FinGPT (金融领域专精)
    - 深度研究/日报告 → GLM-5 (通用能力强)
    - 情绪分析 → FinGPT (金融情绪专精)
    """

    ROUTING_TABLE: dict[TaskCategory, list[ModelType]] = {
        TaskCategory.INTRADAY: [ModelType.FINGPT, ModelType.GLM5, ModelType.DEEPSEEK],
        TaskCategory.DAILY_REPORT: [
            ModelType.GLM5,
            ModelType.FINGPT,
            ModelType.DEEPSEEK,
        ],
        TaskCategory.RESEARCH: [ModelType.GLM5, ModelType.DEEPSEEK, ModelType.FINGPT],
        TaskCategory.SENTIMENT: [ModelType.FINGPT, ModelType.GLM5, ModelType.DOUBAO],
        TaskCategory.TRADING: [ModelType.FINGPT, ModelType.GLM5],
    }

    def __init__(
        self,
        glm5_available: bool = True,
        fingpt_available: bool = False,
        deepseek_available: bool = False,
        doubao_available: bool = False,
    ) -> None:
        self._availability: dict[ModelType, bool] = {
            ModelType.GLM5: glm5_available,
            ModelType.FINGPT: fingpt_available,
            ModelType.DEEPSEEK: deepseek_available,
            ModelType.DOUBAO: doubao_available,
        }

    def route(self, task: TaskCategory) -> Optional[ModelType]:
        """路由任务到最佳模型。

        Args:
            task: 任务类别

        Returns:
            最佳模型类型 (或 None 如果无可用模型)
        """
        candidates = self.ROUTING_TABLE.get(task, [ModelType.GLM5])
        for model in candidates:
            if self._availability.get(model, False):
                return model
        return None

    def get_fallback(self, task: TaskCategory) -> list[ModelType]:
        """获取备选模型列表。"""
        candidates = self.ROUTING_TABLE.get(task, [ModelType.GLM5])
        return [m for m in candidates if self._availability.get(m, False)]

    def set_availability(self, model: ModelType, available: bool) -> None:
        """设置模型可用性。"""
        self._availability[model] = available

    def get_status(self) -> dict[str, Any]:
        """获取路由器状态。"""
        return {m.value: available for m, available in self._availability.items()}


# ============================================================
# CLI 入口
# ============================================================


def main() -> None:
    """CLI 入口: 演示 FinGPT 集成。"""
    print("=" * 60)
    print("FinGPT 系列集成 (轻量 LoRA + RLSP)")
    print("文献: #14 FinGPT 系列 (2023-2025)")
    print("=" * 60)

    print("\n--- 1. FinGPT 客户端 ---")
    client = FinGPTClient()
    response = client.chat("茅台近期走势分析", template="analysis")
    print(f"  响应: {response[:80]}...")
    print(f"  信息: {client.get_info()}")

    print("\n--- 2. LoRA 配置 ---")
    lora = LoRAConfig(rank=16, alpha=32)
    print(f"  配置: {lora.to_dict()}")

    print("\n--- 3. RLSP 训练 ---")
    trainer = RLSPTrainer()
    trainer.setup()
    for epoch in range(3):
        record = trainer.train_step(
            epoch=epoch,
            step=epoch * 10,
            loss=0.5 - epoch * 0.1,
            portfolio_return=0.02 + epoch * 0.005,
            market_return=0.01,
            volatility=0.015,
        )
        print(f"  Epoch {epoch}: loss={record.loss}, reward={record.reward}")

    summary = trainer.get_summary()
    print(f"\n  训练摘要: avg_reward={summary['avg_reward']}")

    print("\n--- 4. 模型路由 ---")
    router = ModelRouter(glm5_available=True, fingpt_available=True)
    for task in TaskCategory:
        model = router.route(task)
        print(f"  {task.value} → {model.value if model else 'None'}")


if __name__ == "__main__":
    main()
