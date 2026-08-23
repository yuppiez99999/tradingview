"""
FinGPT 系列集成 — 单元测试
==========================

测试覆盖:
- ModelType / TaskCategory 枚举
- LoRAConfig / FinGPTConfig 配置
- FinGPTClient 客户端
- RLSPConfig / TrainingRecord / RLSPTrainer 训练管线
- ModelRouter 模型路由器

文献: #14 FinGPT 系列 (2023-2025)
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from utils.fingpt_integration import (
    FINANCIAL_PROMPT_TEMPLATES,
    FinGPTClient,
    FinGPTConfig,
    LoRAConfig,
    ModelRouter,
    ModelType,
    RLSPConfig,
    RLSPTrainer,
    TaskCategory,
    TrainingRecord,
)

# ============================================================
# 枚举测试
# ============================================================

class TestEnums:
    """枚举测试。"""

    def test_model_types(self):
        assert len(ModelType) == 4
        assert ModelType.GLM5.value == "glm5"
        assert ModelType.FINGPT.value == "fingpt"

    def test_task_categories(self):
        assert len(TaskCategory) == 5
        assert TaskCategory.INTRADAY.value == "intraday"

    def test_prompt_templates(self):
        assert len(FINANCIAL_PROMPT_TEMPLATES) == 4
        assert "analysis" in FINANCIAL_PROMPT_TEMPLATES
        assert "trading" in FINANCIAL_PROMPT_TEMPLATES


# ============================================================
# LoRAConfig 测试
# ============================================================

class TestLoRAConfig:
    """LoRA 配置测试。"""

    def test_default_values(self):
        config = LoRAConfig()
        assert config.rank == 16
        assert config.alpha == 32
        assert config.dropout == 0.05
        assert config.bias == "none"

    def test_scaling(self):
        """缩放因子 = alpha / rank。"""
        config = LoRAConfig(rank=8, alpha=16)
        assert config.scaling == 2.0

    def test_scaling_zero_rank(self):
        """rank=0 时不除零 (scaling = alpha / max(rank, 1) = alpha)。"""
        config = LoRAConfig(rank=0, alpha=16)
        assert config.scaling == 16.0

    def test_to_dict(self):
        config = LoRAConfig(rank=16, alpha=32)
        d = config.to_dict()
        assert d["rank"] == 16
        assert d["scaling"] == 2.0


# ============================================================
# FinGPTConfig 测试
# ============================================================

class TestFinGPTConfig:
    """FinGPT 配置测试。"""

    def test_default_values(self):
        config = FinGPTConfig()
        assert config.model_name == "FinGPT-v7"
        assert config.max_tokens == 4096
        assert config.temperature == 0.7

    def test_to_dict(self):
        config = FinGPTConfig(model_name="FinGPT-test")
        d = config.to_dict()
        assert d["model_name"] == "FinGPT-test"
        assert "lora_config" in d

    def test_lora_config_included(self):
        """LoRA 配置包含在 FinGPT 配置中。"""
        lora = LoRAConfig(rank=32, alpha=64)
        config = FinGPTConfig(lora_config=lora)
        assert config.lora_config.rank == 32


# ============================================================
# FinGPTClient 测试
# ============================================================

class TestFinGPTClient:
    """FinGPT 客户端测试。"""

    def test_chat_basic(self):
        """基本对话。"""
        client = FinGPTClient()
        response = client.chat("测试消息")
        assert isinstance(response, str)
        assert len(response) > 0

    def test_chat_with_template(self):
        """使用模板对话。"""
        client = FinGPTClient()
        response = client.chat("茅台走势", template="analysis")
        assert isinstance(response, str)

    def test_chat_unknown_template(self):
        """未知模板使用原始消息。"""
        client = FinGPTClient()
        response = client.chat("测试", template="unknown")
        assert isinstance(response, str)

    def test_is_ready(self):
        """就绪检查。"""
        client = FinGPTClient()
        assert isinstance(client.is_ready(), bool)

    def test_get_info(self):
        """模型信息。"""
        client = FinGPTClient()
        info = client.get_info()
        assert info["model_type"] == "fingpt"
        assert "config" in info

    def test_mock_response_when_unavailable(self):
        """不可用时返回 mock 响应。"""
        config = FinGPTConfig(api_key="", device="cuda")
        client = FinGPTClient(config)
        response = client.chat("测试")
        assert "Mock" in response or "FinGPT" in response


# ============================================================
# RLSPTrainer 测试
# ============================================================

class TestRLSPConfig:
    """RLSP 配置测试。"""

    def test_default_values(self):
        config = RLSPConfig()
        assert config.learning_rate == 1e-5
        assert config.batch_size == 32
        assert config.n_epochs == 10

    def test_to_dict(self):
        config = RLSPConfig(learning_rate=1e-4)
        d = config.to_dict()
        assert d["learning_rate"] == 1e-4


class TestTrainingRecord:
    """训练记录测试。"""

    def test_to_dict(self):
        record = TrainingRecord(epoch=1, step=10, loss=0.5, reward=0.02, excess_return=0.01)
        d = record.to_dict()
        assert d["epoch"] == 1
        assert d["loss"] == 0.5


class TestRLSPTrainer:
    """RLSP 训练器测试。"""

    def test_setup(self):
        """设置训练环境。"""
        trainer = RLSPTrainer()
        assert trainer.setup()
        assert trainer.is_ready()

    def test_compute_reward_positive(self):
        """正超额收益 → 正奖励。"""
        trainer = RLSPTrainer()
        reward = trainer.compute_reward(
            portfolio_return=0.05, market_return=0.01, volatility=0.01,
        )
        assert reward > 0

    def test_compute_reward_negative(self):
        """负超额收益 → 负奖励。"""
        trainer = RLSPTrainer()
        reward = trainer.compute_reward(
            portfolio_return=0.01, market_return=0.05, volatility=0.01,
        )
        assert reward < 0

    def test_high_volatility_reduces_reward(self):
        """高波动降低奖励。"""
        trainer = RLSPTrainer()
        low_vol = trainer.compute_reward(0.03, 0.01, 0.005)
        high_vol = trainer.compute_reward(0.03, 0.01, 0.05)
        assert high_vol < low_vol

    def test_train_step(self):
        """训练一步。"""
        trainer = RLSPTrainer()
        trainer.setup()
        record = trainer.train_step(
            epoch=0, step=0, loss=0.5,
            portfolio_return=0.02, market_return=0.01, volatility=0.015,
        )
        assert isinstance(record, TrainingRecord)
        assert record.epoch == 0

    def test_get_history(self):
        """获取训练历史。"""
        trainer = RLSPTrainer()
        trainer.setup()
        for i in range(5):
            trainer.train_step(i, i, 0.5, 0.02, 0.01, 0.015)
        history = trainer.get_history()
        assert len(history) == 5

    def test_get_summary_empty(self):
        """空训练摘要。"""
        trainer = RLSPTrainer()
        summary = trainer.get_summary()
        assert summary["n_steps"] == 0

    def test_get_summary_with_history(self):
        """有训练记录的摘要。"""
        trainer = RLSPTrainer()
        trainer.setup()
        for i in range(3):
            trainer.train_step(i, i, 0.5 - i * 0.1, 0.02, 0.01, 0.015)
        summary = trainer.get_summary()
        assert summary["n_steps"] == 3
        assert "avg_reward" in summary
        assert "avg_loss" in summary


# ============================================================
# ModelRouter 测试
# ============================================================

class TestModelRouter:
    """模型路由器测试。"""

    def test_route_intraday_to_fingpt(self):
        """盘中决策优先 FinGPT。"""
        router = ModelRouter(glm5_available=True, fingpt_available=True)
        model = router.route(TaskCategory.INTRADAY)
        assert model == ModelType.FINGPT

    def test_route_research_to_glm5(self):
        """深度研究优先 GLM-5。"""
        router = ModelRouter(glm5_available=True, fingpt_available=True)
        model = router.route(TaskCategory.RESEARCH)
        assert model == ModelType.GLM5

    def test_route_fallback_to_glm5(self):
        """FinGPT 不可用时回退 GLM-5。"""
        router = ModelRouter(glm5_available=True, fingpt_available=False)
        model = router.route(TaskCategory.INTRADAY)
        assert model == ModelType.GLM5

    def test_route_none_available(self):
        """无可用模型。"""
        router = ModelRouter(glm5_available=False, fingpt_available=False)
        model = router.route(TaskCategory.INTRADAY)
        assert model is None

    def test_get_fallback(self):
        """获取备选列表。"""
        router = ModelRouter(glm5_available=True, fingpt_available=True)
        fallbacks = router.get_fallback(TaskCategory.INTRADAY)
        assert ModelType.FINGPT in fallbacks
        assert ModelType.GLM5 in fallbacks

    def test_set_availability(self):
        """设置可用性。"""
        router = ModelRouter()
        router.set_availability(ModelType.FINGPT, True)
        model = router.route(TaskCategory.INTRADAY)
        assert model == ModelType.FINGPT

    def test_get_status(self):
        """路由器状态。"""
        router = ModelRouter(glm5_available=True, fingpt_available=False)
        status = router.get_status()
        assert status["glm5"] is True
        assert status["fingpt"] is False

    def test_all_task_categories_routable(self):
        """所有任务类别可路由。"""
        router = ModelRouter(glm5_available=True)
        for task in TaskCategory:
            model = router.route(task)
            assert model is not None


# ============================================================
# 端到端集成测试
# ============================================================

class TestEndToEnd:
    """端到端集成测试。"""

    def test_full_fingpt_workflow(self):
        """完整 FinGPT 工作流。"""
        client = FinGPTClient()
        response = client.chat("分析茅台", template="analysis")
        assert len(response) > 0

        trainer = RLSPTrainer()
        trainer.setup()
        record = trainer.train_step(0, 0, 0.5, 0.03, 0.01, 0.015)
        assert record.reward != 0

        router = ModelRouter(glm5_available=True, fingpt_available=True)
        model = router.route(TaskCategory.TRADING)
        assert model == ModelType.FINGPT

    def test_training_progression(self):
        """训练进展 (奖励逐渐改善)。"""
        trainer = RLSPTrainer()
        trainer.setup()
        rewards = []
        for i in range(10):
            record = trainer.train_step(
                epoch=i, step=i,
                loss=0.5 - i * 0.05,
                portfolio_return=0.01 + i * 0.003,
                market_return=0.01,
                volatility=0.015,
            )
            rewards.append(record.reward)

        summary = trainer.get_summary()
        assert summary["n_steps"] == 10
        assert summary["final_reward"] > rewards[0]
