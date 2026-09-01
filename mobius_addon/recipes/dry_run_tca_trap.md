# dry-run 成交回报陷阱（日志有记录 ≠ 已成交）

## 症状
TCA fills 日志有记录，但成交回报 JSON 不落盘、`positions.json` 不更新，
诊断时误以为“已成交”，实际下游 PnL 恒为空。

## 根因
以 dry-run 运行执行器时，日志有撮合记录但落盘被跳过；
“日志有记录” ≠ “成交已执行”，诊断被日志误导。

## 修复
诊断执行闭环时以**落盘产物**（`fills` JSON / `positions.json` 更新）为事实源，
不以日志为准；dry-run 模式须显式区分“模拟成交”与“真实落盘”。

## 验证命令
```bash
ls reports/fills/
python -c "import json; print(json.load(open('config/positions.json'))['positions'])"
```

## 防复发
诊断报告必须与源码 + 落盘产物交叉验证，不轻信日志；
观测路径 fail-open 降级但必须留痕，决策路径 fail-close 阻断。
