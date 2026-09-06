---
wave: 15
date: 2026-09-05
source: GitHub Trending 全语言周榜 + Python 周榜，合并去重 Top 30
verdict: 0 项进入 2026 代码窗口（连续两轮）
governance:
  - ROADMAP 09-02 决策 2：2027 前不引入新 GitHub 项目/新框架/新模型
  - Q4 冻结窗：2026-09-19 ~ 2026-12-10 冻结生产写入
  - Wave 13 准入判据：pushed_at 距今 ≤12 个月 / 已有替代 / 与既有排期去重
grading:
  already_in_use: [archify]
  already_replaced: [Soup, ODS, crawl4ai]
  rejected: [heretic, user-scanner, khoj]
  new_candidates:
    - name: awesome-mcp-servers
      type: 书签资源（零依赖）
      window: 立即（无冻结约束）
    - name: chrome-devtools-mcp
      type: 采集工具（低优）
      window: 2027 Q2
      license: Apache-2.0
      pushed_at: 2026-09-04
  recurring_candidates: [timesfm, scientific-agent-skills, academic-research-skills, screenshot-to-code]
  irrelevant: [OpenMAIC, gods-eye-view, openclaude, minimind, vphone-cli, fmt, open-seo, zod, patent-disclosure-skill, VoiceStudio, ipatool, openwhispr, manim, Open_Duck_Mini, video-use, OpenMontage, ai-job-search]
---

# Wave 15 周榜裁决要点（2026-09-05）

## 关键决策

1. **khoj（37k）劝退 —— 本轮唯一实质性新发现。**
   星标与活跃度均达标（`pushed_at` 2026-08-02），但许可证为 **AGPL-3.0**。
   AGPL 第 13 条将传染范围延伸到网络服务交互，与专有量化交易系统的分发/SaaS 化不兼容。
   叠加已有替代（deep-research 已评估 + cairn 知识层 + llama_index 在册）与重型自托管服务
   （独立进程 + DB + 多模型后端，撞本机 RAM 约束），裁决劝退。

2. **准入判据增补（沉淀为通用规则）。**
   Wave 13 判据只查 `pushed_at` 活跃度，未查许可证，存在盲区。新增：

   > 许可证必须为宽松许可（MIT / Apache-2.0 / BSD / ISC）。
   > Copyleft（GPL/LGPL/AGPL/SSPL）默认劝退作为生产依赖，
   > 仅允许只读参考或独立进程外工具（且不进行网络服务化集成）。

3. **chrome-devtools-mcp 登记为 Crawl4AI 的安全替代对照。**
   Apache-2.0 + Google 侧官方维护 + 昨日仍 push，相比 Crawl4AI（v0.9.3 任意文件写入/SSRF/两处 XSS
   披露史）是更优选。**排位在 DrissionPage 之后**（Windows 单文件、无 Node/浏览器进程依赖）。
   当前 trafilatura+feedparser 已覆盖静态站点，不构成引入需求。

4. **候选池复现项维持原排期。** TimesFM 仍是头号（2027-03 POC 3 人天）。

## 与 Wave 14 的差异

- Wave 14 的 30 项中有 15 项未在本周复现（ponytail / gpt-image-2 / tailcat / OpenMontage 部分 /
  freellmapi / video-use 部分 / router / 追剧导航 / ODS 部分 / user-scanner 部分 /
  go-modern-guidelines / Crawl4AI 部分 / Cursor Plugins 等），周榜换手率约 50%。
- 换手率高说明**周频扫描维持必要**，但连续两轮「0 项进 2026 窗口」说明冻结期判据工作正常。

## 指针

- 排期载体：`docs/GitHub周热门项目集成_Wave15_20260905.md`
- 同步产出：`docs/vnpy_接入spec_20260905.md`（vnpy 劝退裁决）
- 上级事实源：`cairn/ROADMAP.md` §2027 PLAN
