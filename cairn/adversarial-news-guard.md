# 对抗新闻攻击防护 (Adversarial News Attack Guard)

> **任务**: LIT-2.6 对抗新闻攻击防护（安全加固）— Sprint LIT-S2
> **文献**: #35 SaTML 2026 — LLM 安全攻击防护
> **状态**: ✅ 已完成 (2026-08-23)
> **指针**: [LOG 2026-08-23 LIT-2.6](./LOG.md)

## 1. 问题背景

攻击者通过 manipulated news text 操纵 LLM 决策，主要威胁：

1. **Unicode 同形字攻击**: 用相似 Unicode 字符替换关键词 (西里尔 е→拉丁 e, а→a)
2. **隐藏文本注入**: 零宽字符/控制字符嵌入隐藏指令 (U+200B/202E/FEFF)
3. **提示注入**: 新闻中嵌入 "ignore previous instructions" 等恶意指令
4. **情绪操纵**: 极端情绪词汇 ("史诗级暴涨"/"必涨"/"零风险") 操纵 LLM 情绪评分

## 2. 架构设计

```
AdversarialNewsGuard (综合净化管道)
├── HomoglyphDetector      (Unicode 同形字检测 + 归一化)
├── HiddenTextFilter       (零宽/控制/方向覆盖符 检测 + 移除)
└── PromptInjectionDetector (9种注入模式 + 极端情绪词 检测 + 中和)
```

净化流程: 同形字归一化 → 隐藏字符移除 → 注入模式中和 → 输出 SanitizationResult

## 3. 核心组件

### 3.1 HomoglyphDetector
- **HOMOGLYPH_MAP**: 西里尔/希腊/全角 → 拉丁 映射表 (30+ 字符)
- `detect(text) → (检测列表, ThreatReport)`
- `normalize(text) → (净化文本, 修改记录)`

### 3.2 HiddenTextFilter
- **HIDDEN_CHARS**: 零宽字符 + 方向覆盖符 (12个)
- **CONTROL_CHARS**: U+0000-001F (排除 \t\n\r)
- `detect(text) → (检测列表, ThreatReport)` — >5个升为 HIGH
- `remove(text) → (净化文本, 修改记录)`

### 3.3 PromptInjectionDetector
- **INJECTION_PATTERNS**: 9个正则 (ignore/disregard/forget/act as/system:/<system>/override)
- **EXTREME_EMOTION_WORDS**: 16个中文极端情绪词
- `detect_injection(text) → ThreatReport` — 注入匹配 HIGH
- `detect_emotion_manipulation(text) → ThreatReport` — ≥3词 HIGH, 否则 LOW
- `neutralize(text) → (净化文本, 修改记录)` — 注入模式替换为 [REMOVED]

### 3.4 AdversarialNewsGuard
- `sanitize(text) → SanitizationResult` — 串联三组件
- `sanitize_batch(texts) → list[SanitizationResult]`
- `is_safe(text) → bool` — 快速检查
- `get_stats() → dict` — total/safe/blocked/block_rate

## 4. 威胁分级

| Severity | 触发条件 |
|----------|---------|
| SAFE | 无威胁 |
| LOW | 1-2个情绪词 |
| MEDIUM | 同形字 / ≤5个隐藏字符 |
| HIGH | 提示注入 / >5个隐藏字符 / ≥3个情绪词 |
| CRITICAL | (预留) |

## 5. 集成点

- `utils/ai_coordinator.py`: `sanitize_news_input()` 方法，可选依赖 (try/except 降级)
- 在 LLM 调用前对新闻文本进行净化，防止对抗攻击

## 6. 测试覆盖

- `tests/unit/test_adversarial_news_guard_unit.py` — 39 单元测试全绿
- 覆盖: 枚举/数据结构/同形字/隐藏文本/注入检测/情绪操纵/综合管道/端到端

## 7. 已知限制

- 提示注入正则主要针对英文模式 ("ignore previous" 等)；中文注入需扩展中文模式
- 同形字映射表覆盖常见西里尔/希腊/全角，未覆盖所有 Unicode 混淆
- 情绪词表为中文金融场景定制，其他语言需扩展

## 8. 文件清单

| 文件 | 行数 | 用途 |
|------|------|------|
| `utils/adversarial_news_guard.py` | ~511 | 核心实现 |
| `tests/unit/test_adversarial_news_guard_unit.py` | ~366 | 单元测试 |
| `utils/ai_coordinator.py` | (+30行) | sanitize_news_input() 集成 |
| `ruff.toml` | (+4行) | T201/UP042 豁免 |

<!-- AUTO-GENERATED: 相关文档 -->
## 相关文档

- [情绪因子演化：v4.0 → v4.3](sentiment-factor-evolution.md) (相似度 17%)
- [FinGPT 系列集成 (轻量 LoRA + RLSP 训练管线)](fingpt-integration.md) (相似度 16%)
- [CN-Buzz2Portfolio 中国市场基准](cn-buzz2portfolio.md) (相似度 13%)
- [v8.7 发布 — LIT-5.6 全量集成验收](v87-release.md) (相似度 7%)
- [KTD-Fin 记忆控制评估基准](ktd-fin-eval.md) (相似度 7%)

<!-- 由 scripts/cairn_cross_ref.py 自动生成，请勿手动编辑此区块 -->
