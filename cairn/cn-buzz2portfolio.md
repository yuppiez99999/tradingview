# CN-Buzz2Portfolio 中国市场基准

> **文献**: #24 CN-Buzz2Portfolio (中国市场) (2026.03, ★★★★★)
> **任务**: LIT-2.3 部署 CN-Buzz2Portfolio 中国市场基准
> **状态**: ✅ 已完成 (2026-08-23)
> **LOG 指针**: `cairn/LOG.md` → "2026-08-23 · LIT-2.3"

## 核心思想

日热点新闻 → 宏观/行业配置 → 投资组合构建 (中国市场专属基准)。

## Tri-Stage CPA Agent (三阶段中国组合代理)

### Stage 1: NewsCollection (新闻收集与分类)

基于关键词匹配将新闻分类为 5 类：
- `MACRO_POLICY`: 宏观政策 (降准/降息/MLF/LPR/财政/专项债/两会/十五五...)
- `INDUSTRY_DYNAMICS`: 行业动态 (半导体/光伏/白酒/创新药/银行/AI/房地产/黄金/电力...)
- `COMPANY_ANNOUNCEMENT`: 公司公告 (公告/财报/业绩/预告/披露)
- `MARKET_SENTIMENT`: 市场情绪 (恐慌/贪婪/涨停/跌停/牛市/熊市)
- `UNKNOWN`: 未分类

### Stage 2: BuzzAnalysis (舆情分析)

- **行业映射**: 9 个 A 股主行业 (高端制造/新能源/消费/医药/金融/科技/地产/资源/公用)
- **情绪评分**: -1 到 +1 (利好/增长/突破 vs 利空/下降/亏损)
- **热度评分**: 0 到 1 (文本长度 + 权威来源加成)
- **行业舆情汇总**: {行业: {avg_sentiment, total_heat, count}}

### Stage 3: PortfolioConstruction (组合构建)

- **默认权重**: 9 行业默认配置 (高端制造 20% + 消费 15% + 金融 15% + 医药 13% + 新能源 12% + 科技 10% + 资源 7% + 地产 5% + 公用 3%)
- **舆情调整**: weight *= (1 + sentiment × heat × 0.1)
- **宏观信号**: bullish (>0.2) / neutral / bearish (<-0.2)
- **归一化**: 调整后权重归一化到 100%
- **置信度**: 0.5 × news_factor + 0.5 × industry_factor

## 交付物

| 文件 | 行数 | 说明 |
|------|------|------|
| `tests/eval/cn_buzz2portfolio.py` | ~530 | 核心实现 (3阶段 + 基准框架) |
| `tests/unit/test_cn_buzz2portfolio_unit.py` | ~470 | 44 单元测试全绿 |

## 测试覆盖

- NewsCategory 枚举 (2 测试)
- NewsItem / PortfolioConfig 数据结构 (4 测试)
- 关键词映射 (2 测试)
- NewsClassifier (6 测试: 宏观/行业/公司/情绪/未知/批量)
- BuzzAnalyzer (10 测试: 行业映射/情绪/热度/分析/汇总)
- PortfolioConstructor (8 测试: 默认权重/构建/宏观信号/置信度/理由)
- TriStageCPAAgent (4 测试: 基本/空/多新闻/三阶段)
- CNBuzz2PortfolioBenchmark (5 测试: 运行/批量/摘要/创建)
- 端到端集成 (2 测试)

## ruff.toml 豁免

```toml
# UP042: str+Enum 继承保留 Py3.8 兼容 (StrEnum 需 Py3.11+)
"tests/eval/cn_buzz2portfolio.py" = ["UP042"]
```

## 后续方向

- **LIT-2.4**: KTD-Fin 记忆控制评估
- **实际集成**: 接入 Wind MCP 新闻扫描 → 实时热点新闻输入
- **回测验证**: CSI300 2024-2026 历史新闻回测