"""ECL (Experience Context Layer) — 经验上下文层.

Wave10-CTX Phase A 实现:
    - event_store.py: EventStore append-only 事件日志 (CTX-A1)
    - sinks.py: log_decision sink 协议 + EclEventSink (CTX-A1)
    - embeddings.py: 嵌入降级链 st→FTS5→hash (CTX-A2)
    - experience_store.py: ExperienceStore 经验库 + 检索 (CTX-A2)
    - bypass.py: EOD 阶段 4.95 旁路编排 (CTX-A3)

设计依据: cairn/experience-context-layer.md + docs/Wave10_经验上下文层集成计划_20260828.md
铁律: 全部旁路只读, flag 默认 False, 生产决策输出 diff=0
"""
