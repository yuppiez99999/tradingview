# venv 文件 docstring 重复注入损坏

## 症状
大量 `.py` 导入/语法报错，报错指向 docstring 内部出现重复代码；
venv `site-packages` 多处 `SyntaxError`，连 `import numpy` 都失败。

## 根因
venv 中数百个 `.py` 被 docstring 重复注入损坏（基座包污染或错误复制脚本），
文件在 docstring 边界被插入了重复片段，解析器遇到非法缩进/重复定义即报错。

## 修复
- 从完好的基座 Python `Lib/site-packages` 批量复制完好包到 venv；
- 修正被改名的目录（如 `python_dateutil` → `dateutil`）；
- 手动修复被切片的文件（如 `ruamel.yaml` 的 `scalarbool.py` / `util.py` 行切片去重）；
- 重命名损坏的 `easy-install.pth`（GBK 编码指向旧路径）为 `.bak`。

## 验证命令
```bash
python -c "import numpy, pandas, requests, pydantic, dateutil, ruamel.yaml"
# 全部通过；venv 0 个损坏文件
```

## 防复发
复制依赖使用 venv 而非裸 python；怀疑损坏先核验实物再批量替换，
避免盲目覆盖引入二次损坏。
