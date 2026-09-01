# P0 文件裸 print 门禁（T201）

## 症状
pre-commit / CI 增量门禁拦截 P0 根目录文件的裸 `print()`，提交被拒。

## 根因
P0 文件需保持生产可读性与日志规范，禁止裸 print（ruff T201）；
门禁对 15 个 P0 根目录文件阻断，单文件确须打印时用 `# allow-print` 豁免。

## 修复
- 改用 `logging` 或项目统一 logger；
- 单文件确须打印时行尾加 `# allow-print`；
- 紧急跳过用环境变量 `SKIP_P0_PRINT=1`。

## 验证命令
```bash
ruff --select T201 <file>     # 应 0 命中（或仅含 # allow-print 豁免行）
```

## 防复发
新代码默认用 logger；门禁类代码必须用与 CI 一致的入参格式做负向测试，
否则“看起来接好”和“真的在拦”是两回事。
