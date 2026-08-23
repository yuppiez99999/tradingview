# FinGPT 系列集成 (轻量 LoRA + RLSP 训练管线)

> **文献**: #14 FinGPT 系列（6 篇）(2023-2025, ★★★★★)
> **任务**: LIT-2.5 FinGPT 系列集成
> **状态**: ✅ 已完成 (2026-08-23)
> **LOG 指针**: `cairn/LOG.md` → "2026-08-23 · LIT-2.5"

## FinGPT 系列 (6 篇)

1. FinGPT (2023): 金融领域 GPT 基座
2. FinGPT-Bench (2024): 金融基准测试
3. FinGPT-RLSP (2024): 强化学习 + 股价反馈
4. FinGPT-LoRA (2024): 轻量 LoRA 微调
5. FinGPT-Trader (2025): 交易代理
6. FinGPT-Multi (2025): 多模态金融

## 核心组件

### 1. LoRAConfig (轻量 LoRA 微调)

- `rank`: LoRA 秩 (常用 8/16/32)
- `alpha`: 缩放因子 (通常 = 2 × rank)
- `scaling`: alpha / rank
- `target_modules`: q_proj / v_proj / k_proj / o_proj

### 2. FinGPTClient (与 GLM5Client 接口兼容)

- 4 个金融领域 prompt 模板: analysis / sentiment / trading / risk
- 不可用时返回 mock 响应 (优雅降级)

### 3. RLSPTrainer (Reinforcement Learning with Stock Price)

- **奖励函数**: reward = excess_return - risk_penalty × volatility
- **excess_return**: portfolio_return - market_return - risk_free_rate/252
- 训练管线就绪检查 + 训练历史记录 + 摘要

### 4. ModelRouter (GLM-5 ↔ FinGPT 路由)

路由策略:
- 交易/盘中/情绪 → 优先 FinGPT (金融领域专精)
- 研究/报告 → 优先 GLM-5 (通用能力强)

## GLM5Client 集成

```python
# 获取 FinGPT 客户端
fingpt = get_fingpt_client()

# 带备选的快速对话
response = quick_chat_with_fallback("分析茅台", task="intraday")
```

## 交付物

| 文件 | 行数 | 说明 |
|------|------|------|
| `utils/fingpt_integration.py` | ~490 | 核心实现 (4组件) |
| `tests/unit/test_fingpt_integration_unit.py` | ~360 | 37 单元测试全绿 |
| `utils/glm5_client.py` | +50行 | get_fingpt_client + quick_chat_with_fallback |

## ruff.toml 豁免

```toml
# T201: CLI main() print
# UP042: str+Enum Py3.8 兼容
"utils/fingpt_integration.py" = ["T201", "UP042"]
```

## 后续方向

- **LIT-2.6**: 对抗新闻攻击防护
- **实际集成**: 接入 AI4Finance-Foundation/FinGPT 开源模型
- **RLSP 训练**: 接入真实股价数据训练