# Windows 控制台 GBK 编码崩溃（货币符号 / 非 GBK 字符）

## 症状
Python CLI 调用 `print()` 输出含 `¥` 或某些中文全角符号时抛
`UnicodeEncodeError: 'gbk' codec can't encode character`，逻辑其实已成功执行，
但程序在输出阶段崩溃退出，表现为“跑完了却报错”。

## 根因
Windows 控制台默认编码为 GBK(cp936)，`¥`(\xa5) 等字符不在 GBK 字符集内；
裸 `python` 也可能使用系统编码而非 utf-8，导致标准输出写不进去就抛异常。

## 修复
- 输出货币一律用 `RMB` / `CNY` 文本代替 `¥` 符号；
- 或在启动前设置环境变量 `$env:PYTHONIOENCODING="utf-8"`；
- 优先用 `logging` 输出（通常走 utf-8 文件 handler，不触碰控制台编码）。

## 验证命令
```powershell
$env:PYTHONIOENCODING="utf-8"
python your_cli.py      # 复跑，确认不再崩，且逻辑结果已落盘
```

## 防复发
所有外挂 CLI 的启动器内自设 `PYTHONIOENCODING=utf-8`；硬编码货币文本用
`RMB`/`CNY`，绝不在 print 路径写 `¥`。
