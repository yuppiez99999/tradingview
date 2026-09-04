---
type: project_topic
status: active
authoring_mode: ai_generated
created: 2026-09-04
updated: 2026-09-04
contains: github-trending, wave14, week30-dedup, decision2-2027-freeze, timesfm-candidate, crawl4ai-duplicate
related:
  - docs/GitHub周热门项目集成_Wave14_20260904.md
  - cairn/github-trending-wave13-20260903.md
  - cairn/ROADMAP.md
  - docs/github_integration_plan_wave12_20260830.md
---

# Wave 14：GitHub 周榜 Top 30 集成排期裁决（2026-09-04）

> 周榜正式轮（2026-09-04 抓取，30 项）。裁决遵循 09-02 决策 2（2027 前不引入新 GitHub 项目/框架/模型）与 Wave 13 准入判据（pushed_at 活跃度 / 已有替代 / 去重）。
> 排期载体：`docs/GitHub周热门项目集成_Wave14_20260904.md`（本文件仅为决策要点沉淀）。

## 一、分级结果（30/30）

已在用 2（#1 Archify 08-17 已落地并产出架构图、#10 ECC 08-21 已评估）｜已有替代 4（#24 Crawl4AI→12-A 的 trafilatura+feedparser 已完成、#20 router→Switchyard 已选、#23 Soup/#28 ODS→ModelArts+本机资源约束）｜**2027 候选池新增 5**（#17 TimesFM 头号、#4 ponytail、#21+#5 academic/scientific research skills、#15 screenshot-to-code 低优观察）｜劝退 3（#12 freellmapi 合规 / #18 Heretic 不适用 / #27 user-scanner 隐私合规）｜无关 16。

## 二、关键裁决

1. **不新建 Wave 14 代码轨道**：全部有增量价值的项目受 09-02 决策 2 约束 → 2026 零引入；与 Wave 13（09-03 快照轮）同模式，仅登记不排 Sprint。
2. **TimesFM 3.0 = 唯一真实研究增量**：时间序列基础模型（活跃、无本地重复），2027-03 研究 POC（3.0 人天，挂 Wave 9-GH Sprint 2-3 研究线）。纪律：长样本（`D:\etf_data_2015_2026`）纯样本外 + DSR/n_trials 口径一致，结论二选一（登记 or 证伪归档）。环境走 ModelArts（本机无 GPU）。
3. **Crawl4AI 判已有替代而非 2027 候选**：其核心能力（网页→干净 Markdown 正文提取）已被 12-A 08-30 完成的 trafilatura+feedparser 覆盖（`utils/news_sentiment.py`）；动态网页级采集暂非舆情线需求。若未来舆情升级动态站点，以 Wave 13 准入判据重评（注意 v0.9.3 安全披露史：任意文件写入/SSRF/XSS×2）。
4. **router 引思想不引依赖**：其"embedder 对单次请求打分选模型"理念作为 Switchyard 集成（12-B）启动时对照注记，不新增依赖。
5. **skill 安装通用纪律（沉淀）**：ECC 官方明确"仅从官方渠道安装，第三方镜像可能含恶意代码"——适用于 ponytail 等一切 Agent skill 引入。

## 三、后续动作

- **立即（无成本）**：ROADMAP + 排期计划总览 Wave 表登记 Wave 14 行；`cairn/LOG.md` 指针。
- **12-B / Wave 9-GH 启动（2027）**：TimesFM POC 立项前用 Wave 13 劝退清单复核；ponytail/skills 评估走 ECC 08-21 流程先例。
- **不再主动跟踪**：16 项无关 + 3 项劝退，若后续周榜再现同类项直接归类，不重复评估。

## 四、决策记录

1. 为何零引入：不是"没有好项目"，而是 12-31 发布主线 + Q4 冻结窗 + 决策 2 三约束下，任何新依赖都会稀释验证产能；候选池登记已保留完整上下文，2027 重启成本为零。
2. 为何 TimesFM 值得登记而非劝退：它是**唯一**与系统核心能力（时序预测/收益线对照）直接对口的项目，且 2027 评估恰好与 MVSK/qlib shadow 30 天结论互证（10-13 评估周产出基线）。
3. 为何今日不做"立即工具降本"子轨道：Wave 12-A 已于 08-30 全量提前完成（5/5+110 测试），09-07~09-25 支线窗口已空置且无新的零风险工具项——避免为用而用。
