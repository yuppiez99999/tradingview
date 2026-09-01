# FillsStore 成交双重计数

## 症状
从 `FillsStore` 读取当日成交，写入 N 笔却读出 2N 笔，TCA 归因与 PnL 计算被放大一倍。

## 根因
`load_day` 同时合并**内存 buffer** 与**文件内容**，造成重复计数；
文件才是事实源，buffer 仅用于落盘失败时的兜底，不应在读取时二次叠加。

## 修复
以文件为事实源，buffer 仅用于落盘失败兜底；读取时不再把内存态与文件态叠加合并。

## 验证命令
```python
store.write_day(date, fills_15)
assert store.load_day(date) == 15     # 应为 15 而非 30
```

## 防复发
单点事实源原则——落盘与读取都只认一个权威来源，绝不同时叠加内存态与文件态。
