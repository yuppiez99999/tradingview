"""tests/contract/ — 行为契约测试 (v3 §11 唯一未完项, v4 §3 Q1-Q5 升格)

Q1 相关系数/IC 必须 np.isfinite 守卫
Q2 除零路径必须守卫
Q3 分母类参数构造期校验 > 0
Q4 陈旧/缓存数据必须标记 stale 并降级质量分
Q5 禁止裸 except:

每条 Q 对应一个 test_q*.py, 验证现有代码行为符合契约。
"""
