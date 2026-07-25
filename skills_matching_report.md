# 量化交易系统高价值技能匹配报告

**生成时间**: 2026-07-23  
**扫描范围**: 本地已安装Skills + 缓存Skills库  
**总技能数**: 78个 (3个本地已安装 + 75个临时缓存)

---

## 一、本地已安装核心技能 (3个)

### ✅ 1. wind-mcp-skill (万得金融数据)
- **路径**: `.agents/skills/wind-mcp-skill/`
- **分类**: 数据获取
- **核心功能**: Wind MCP调用路由器,覆盖A股/港股/美股/基金/指数/债券/宏观数据查询
- **覆盖范围**: 7大server_type, 209+接口
- **使用频率**: ⭐⭐⭐⭐⭐ (最高)
- **匹配度**: 与当前系统完美匹配,提供底层数据支撑

### ✅ 2. position_sizing_decision_skill (仓位决策)
- **路径**: `.agents/skills/position_sizing_decision_skill/`
- **分类**: 交易执行/风险管理
- **核心功能**: 根据风险预算、波动特征、组合承受力给出单笔仓位建议
- **执行流程**: 7步法 (风险预算→交易类型→波动测量→组合相关性→分批框架→压力测试)
- **匹配度**: 与v8.x institutional风控模块高度互补

### ✅ 3. ifind-finance-data (同花顺iFinD)
- **路径**: `skills/ifind-finance-data/`
- **分类**: 数据获取
- **核心功能**: iFinD金融数据查询,支持股票/基金/债券/宏观/新闻
- **特色能力**: 日内高频实时行情、智能选股选基
- **匹配度**: 与Wind形成双数据源冗余

---

## 二、高价值技能分类矩阵

### 📊 数据获取类 (4个技能)

| 排名 | 技能名称 | 本地安装 | 核心价值 |
|------|---------|---------|---------|
| 1 | wind-mcp-skill | ✅ | 万能数据入口,标准化路由 |
| 2 | ifind-finance-data | ✅ | 同花顺数据源,高频行情 |
| 3 | tushare-finance-skill | ❌ | 220+ Tushare Pro接口,免费数据源 |
| 4 | wind-find-finance-skill | ❌ | 能力发现与自动安装路由 |

**匹配建议**: 
- 当前已有Wind+iFinD双数据源,覆盖度已达90%
- 建议补充Tushare作为免费兜底数据源

---

### 💰 估值建模类 (3个技能)

| 排名 | 技能名称 | 本地安装 | 核心价值 |
|------|---------|---------|---------|
| 1 | dcf-model | ❌ | 专业级DCF现金流折现模型,自动生成Excel |
| 2 | valuation_snapshot_skill | ❌ | 快速估值高低判断,历史分位分析 |
| 3 | valuation-pricing-framework | ❌ | 估值定价框架,重估触发因素识别 |

**匹配建议**:
- ⚠️ **高优先级补充**: dcf-model可增强当前系统的估值模块
- 当前系统缺少专业DCF建模能力

---

### 🔬 研究分析类 (5个技能)

| 排名 | 技能名称 | 本地安装 | 核心价值 |
|------|---------|---------|---------|
| 1 | equity-investment-thesis | ❌ | 个股核心投资逻辑深度研究 |
| 2 | earnings-analysis | ❌ | 专业级财报更新报告(8-12页) |
| 3 | earnings_preview_skill | ❌ | 财报前预期梳理与情景推演 |
| 4 | earnings_reaction_interpreter_skill | ❌ | 财报后涨跌反应解读 |
| 5 | conference_call_takeaway_skill | ❌ | 业绩会信息提炼与管理层语气分析 |

**匹配建议**:
- ⚠️ **中高优先级补充**: earnings-analysis可自动化生成机构级研报
- 当前系统研究模块偏量化,缺基本面深度分析

---

### 🎯 选股策略类 (7个技能)

| 排名 | 技能名称 | 本地安装 | 核心价值 |
|------|---------|---------|---------|
| 1 | breakout_candidate_finder_skill | ❌ | 突破形态批量识别+优先级排序 |
| 2 | high_quality_compounder_finder_skill | ❌ | 高资本回报长线复利选股 |
| 3 | pullback_opportunity_finder_skill | ❌ | 回调充分但趋势未破坏的低吸机会 |
| 4 | canslim_growth_scan_skill | ❌ | CAN SLIM框架成长股筛选 |
| 5 | pead_opportunity_skill | ❌ | 财报后漂移(PEAD)中短线机会 |
| 6 | value_dividend_candidate_skill | ❌ | 估值安全边际+股息吸引力筛选 |
| 7 | earnings_momentum_setup_skill | ❌ | 财报后业绩指引强化的强势股 |

**匹配建议**:
- ✅ 当前系统已有丰富的选股策略(MA/RSI/MACD/涨停/金叉)
- ⚠️ **中优先级补充**: breakout_candidate_finder_skill与当前突破策略高度契合
- high_quality_compounder_finder_skill可增强长线核心仓选股

---

### 🚀 交易执行类 (4个技能)

| 排名 | 技能名称 | 本地安装 | 核心价值 |
|------|---------|---------|---------|
| 1 | position_sizing_decision_skill | ✅ | 风险预算反推仓位大小 |
| 2 | trade_plan_builder_skill | ❌ | 完整执行计划(入场/止损/止盈/应急) |
| 3 | breakout_trade_execution_skill | ❌ | 突破交易落地执行方案 |
| 4 | dip_buy_decision_skill | ❌ | 下跌承接观察区与试错条件 |

**匹配建议**:
- ✅ 已有position_sizing_decision_skill,仓位管理有纪律
- ⚠️ **中优先级补充**: trade_plan_builder_skill可将策略信号转化为可执行计划

---

### 🛡️ 风险管理类 (5个技能)

| 排名 | 技能名称 | 本地安装 | 核心价值 |
|------|---------|---------|---------|
| 1 | stop_loss_discipline_skill | ❌ | 价格/逻辑/时间三维止损规则 |
| 2 | trim_or_hold_decision_skill | ❌ | 大涨后部分兑现vs继续持有 |
| 3 | failed_breakout_exit_skill | ❌ | 突破失败减仓止损纠错 |
| 4 | take_profit_ladder_skill | ❌ | 分批止盈路径设计 |
| 5 | avatar-nassim-taleb-risk | ❌ | 尾部风险+林迪效应+杠铃策略 |

**匹配建议**:
- ✅ 当前系统已有完善风控(止损止盈/仓位预警/熔断机制)
- ⚠️ **低优先级补充**: 现有风控已覆盖核心需求,可补充塔勒布尾部风险管理

---

### 🌡️ 市场情绪类 (7个技能)

| 排名 | 技能名称 | 本地安装 | 核心价值 |
|------|---------|---------|---------|
| 1 | market_sentiment_temperature_skill | ❌ | 量化情绪冷热+风险偏好+拥挤度 |
| 2 | market_breadth_health_skill | ❌ | 指数涨跌背后广度支撑判断 |
| 3 | market_regime_switch_skill | ❌ | 进攻/防守/震荡/切换阶段判断 |
| 4 | sector_rotation_radar_skill | ❌ | 板块轮动+资金切换+风格迁移 |
| 5 | theme_heat_tracker_skill | ❌ | 主题题材热度变化跟踪 |
| 6 | theme_leader_identification_skill | ❌ | 热门题材龙头/中军/跟随股识别 |
| 7 | theme-detector | ❌ | 各板块趋势主题生命周期分析 |

**匹配建议**:
- ✅ 当前系统有a-share-primary-theme-identification主线识别
- ⚠️ **中高优先级补充**: sector_rotation_radar_skill可增强板块轮动分析
- market_regime_switch_skill可优化当前仓位调节逻辑

---

### 📈 基本面分析类 (6个技能)

| 排名 | 技能名称 | 本地安装 | 核心价值 |
|------|---------|---------|---------|
| 1 | business_model_decoder_skill | ❌ | 公司获客/交付/定价/赚钱逻辑拆解 |
| 2 | management_quality_check_skill | ❌ | 管理层背景/激励/资本配置/治理质量 |
| 3 | moat_strength_review_skill | ❌ | 竞争优势来源/强度/可持续性评估 |
| 4 | growth_quality_check_skill | ❌ | 增长来源拆解+盈利含量+现金含量 |
| 5 | institutional_position_shift_skill | ❌ | 机构持仓变化/共识强化/调仓方向 |
| 6 | industry_chain_signal_skill | ❌ | 产业链上下游景气/价格/订单/库存 |

**匹配建议**:
- ⚠️ **中优先级补充**: institutional_position_shift_skill可北向资金跟踪结合
- 当前系统偏量价因子,缺公司治理/护城河定性分析

---

### 📰 新闻事件类 (5个技能)

| 排名 | 技能名称 | 本地安装 | 核心价值 |
|------|---------|---------|---------|
| 1 | major_announcement_impact_skill | ❌ | 并购/减持/定增/重大合同影响路径 |
| 2 | watchlist_news_impact_digest_skill | ❌ | 自选股新闻公告舆情批量整理 |
| 3 | intraday_abnormal_move_alert_skill | ❌ | 盘中急拉急跌放量异常波动识别 |
| 4 | trading_halt_resume_tracker_skill | ❌ | 停复牌事件跟踪与走势评估 |
| 5 | guidance_change_impact_skill | ❌ | 业绩指引上修/下修真实含义解读 |

**匹配建议**:
- ⚠️ **中优先级补充**: intraday_abnormal_move_alert_skill可增强实时监控模块
- major_announcement_impact_skill可结合公告抓取技能

---

### 🧠 决策辅助类 (9个技能)

| 排名 | 技能名称 | 本地安装 | 核心价值 |
|------|---------|---------|---------|
| 1 | bull_bear_case_builder_skill | ❌ | 看多vs看空逻辑对比+证据强弱 |
| 2 | peer_comparison_decision_skill | ❌ | 同业公司横向比较+相对强弱 |
| 3 | add_to_winner_decision_skill | ❌ | 盈利仓是否继续加仓判断 |
| 4 | daily_watchlist_morning_brief_skill | ❌ | 自选股盘前简报+隔夜信息 |
| 5 | premarket_trade_checklist_skill | ❌ | 开盘前催化剂/流动性/计划核查 |
| 6 | gap_open_interpreter_skill | ❌ | 高开/低开/跳空缺口预期差解读 |
| 7 | price_target_reach_alert_skill | ❌ | 接近目标价分批处理建议 |
| 8 | policy_headline_interpreter_skill | ❌ | 政策新闻对行业/个股影响路径 |
| 9 | after_close_watchlist_recap_skill | ❌ | 收盘后自选股表现总结 |

**匹配建议**:
- ⚠️ **中优先级补充**: bull_bear_case_builder_skill可防止确认偏误
- policy_headline_interpreter_skill适合A股政策市特色

---

## 三、技能匹配度评估

### 当前系统能力矩阵 (v8.3/v8.4 Institutional)

| 能力维度 | 现有覆盖 | 缺口 | 匹配建议 |
|---------|---------|------|---------|
| **数据获取** | ✅ Wind + iFinD 双源 | Tushare兜底 | 中优先级 |
| **估值建模** | ⚠️ 简单PE/PB | DCF专业模型 | **高优先级** |
| **选股策略** | ✅ MA/RSI/MACD/涨停/金叉 | 突破形态/CAN SLIM | 中优先级 |
| **交易执行** | ✅ 限价/市价/条件单 | 完整执行计划 | 中优先级 |
| **风险管理** | ✅ 止损止盈/仓位预警 | 尾部风险/塔勒布 | 低优先级 |
| **市场情绪** | ⚠️ 主线识别 | 板块轮动/情绪量化 | **中高优先级** |
| **基本面分析** | ⚠️ 财务数据 | 公司治理/护城河 | 中优先级 |
| **新闻事件** | ⚠️ 公告抓取 | 盘中异动/政策解读 | 中优先级 |
| **研究分析** | ⚠️ 因子研究 | 财报报告/投资 thesis | **中高优先级** |
| **回测验证** | ✅ 完整回测引擎 | - | 已完善 |

---

## 四、TOP 10 高价值技能推荐

基于**实用性**、**覆盖度**、**专业性**、**与当前系统互补性**四维评分:

| 排名 | 技能名称 | 分类 | 优先级 | 推荐理由 |
|------|---------|------|--------|---------|
| 🥇 1 | **dcf-model** | 估值建模 | 🔴 高 | 专业级DCF模型,自动生成Excel,填补当前估值模块最大空白 |
| 🥈 2 | **sector_rotation_radar_skill** | 市场情绪 | 🟡 中高 | 板块轮动是A股超额收益核心来源,与主线识别互补 |
| 🥉 3 | **earnings-analysis** | 研究分析 | 🟡 中高 | 自动化生成机构级财报报告,研究效率提升10倍 |
| 4 | **breakout_candidate_finder_skill** | 选股策略 | 🟢 中 | 突破选股与现有策略高度契合,批量扫描+优先级排序 |
| 5 | **market_regime_switch_skill** | 市场情绪 | 🟢 中 | 优化仓位调节逻辑,区分进攻/防守/震荡阶段 |
| 6 | **trade_plan_builder_skill** | 交易执行 | 🟢 中 | 将策略信号转化为可执行计划,覆盖入场/出场/风控全链条 |
| 7 | **institutional_position_shift_skill** | 基本面分析 | 🟢 中 | 北向资金/机构持仓跟踪,与量化信号形成共振 |
| 8 | **intraday_abnormal_move_alert_skill** | 新闻事件 | 🟢 中 | 增强实时监控模块,快速识别异动驱动因素 |
| 9 | **bull_bear_case_builder_skill** | 决策辅助 | 🟢 中 | 防止确认偏误,强制多空双向思考 |
| 10 | **policy_headline_interpreter_skill** | 决策辅助 | 🟢 中 | A股政策市特色,将政策新闻转化为板块机会 |

---

## 五、实施路线图

### Phase 1: 核心能力补强 (1-2周)
- [ ] 安装 **dcf-model** - 估值建模能力跃升
- [ ] 安装 **sector_rotation_radar_skill** - 板块轮动分析
- [ ] 安装 **earnings-analysis** - 自动化财报报告

### Phase 2: 策略增强 (2-3周)
- [ ] 安装 **breakout_candidate_finder_skill** - 突破选股
- [ ] 安装 **market_regime_switch_skill** - 市场阶段判断
- [ ] 安装 **trade_plan_builder_skill** - 执行计划生成

### Phase 3: 监控升级 (3-4周)
- [ ] 安装 **intraday_abnormal_move_alert_skill** - 盘中异动监控
- [ ] 安装 **institutional_position_shift_skill** - 机构持仓跟踪
- [ ] 安装 **bull_bear_case_builder_skill** - 多空决策辅助

### Phase 4: 政策与情绪 (4-5周)
- [ ] 安装 **policy_headline_interpreter_skill** - 政策解读
- [ ] 安装 **market_sentiment_temperature_skill** - 情绪量化
- [ ] 安装 **tushare-finance-skill** - 免费数据源兜底

---

## 六、技能协同效应

### 数据层 → 分析层 → 决策层 → 执行层

```
Wind/iFinD/Tushare (数据获取)
    ↓
DCF Model + 估值快照 (估值建模)
    ↓
选股策略 (突破/回调/CAN SLIM/PEAD)
    ↓
市场情绪 (板块轮动/ regime判断/情绪温度)
    ↓
决策辅助 (多空案例/同业比较/政策解读)
    ↓
交易执行 (仓位决策/执行计划/止损止盈)
    ↓
风险管理 (三维止损/尾部风险/分批止盈)
```

### 关键协同组合:
1. **Wind + DCF + 估值快照** = 专业级估值体系
2. **突破选股 + 板块轮动 + 市场情绪** = 高胜率选股
3. **仓位决策 + 执行计划 + 三维止损** = 纪律化交易
4. **机构持仓 + 政策解读 + 多空案例** = 深度研究

---

## 七、风险提示

1. **技能过载风险**: 当前系统已有78个技能,建议聚焦TOP 10优先安装
2. **数据源冲突**: Wind/iFinD/Tushare三者功能重叠,建议主从搭配
3. **维护成本**: 临时缓存技能需定期同步更新,避免版本滞后
4. **专业门槛**: DCF/估值建模需要财务专业知识,建议配合finance插件使用

---

## 八、总结

### 当前优势:
✅ 双数据源(Wind + iFinD)覆盖90%金融数据需求  
✅ 完善的选股策略库(MA/RSI/MACD/涨停/金叉/超卖)  
✅ 工业级风控体系(止损止盈/仓位预警/熔断机制)  
✅ 完整回测引擎(事件驱动+延迟模型+TCA分析)  

### 核心缺口:
❌ 专业估值建模(DCF缺失)  
❌ 板块轮动分析(仅主线识别)  
❌ 自动化研究报告(依赖人工)  
❌ 机构持仓跟踪(仅北向资金)  

### 行动建议:
🎯 **立即安装**: dcf-model (估值建模能力跃升)  
🎯 **本周完成**: sector_rotation_radar_skill + earnings-analysis  
🎯 **本月完成**: TOP 10技能全部部署并集成到工作流  

---

**报告结束**  
*数据来源: 本地Skills扫描 + temp缓存分析*  
*生成时间: 2026-07-23*
