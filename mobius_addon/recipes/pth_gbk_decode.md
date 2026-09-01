# site-packages .pth 文件 GBK 解码失败阻塞 python 启动

## 症状
任意 `python` / `pip` 命令一启动就报
`UnicodeDecodeError: 'gbk' codec can't decode byte ... in site.py`，
连 `python -c "print(1)"` 都无法运行，整个 Python 环境瘫痪。

## 根因
`site-packages` 下某个 `.pth` 文件含非 ASCII 字符（如中文路径或注释），
Python 3.8 的 `site.py` 在加载 user site-packages 时按 GBK 解码该文件失败，
在到达任何业务代码之前就抛错退出。

## 修复
- 临时绕过：`set PYTHONUSERBASE=C:\NUL` 后再跑（跳过 user site 加载）；
- 根治：重命名/清理含中文的 `.pth`，或修正其编码；
- 优先使用项目 venv 的 `python.exe` 而非裸 `python`。

## 验证命令
```powershell
$env:PYTHONUSERBASE="C:\NUL"
python -c "print('ok')"     # 应正常输出 ok
```

## 防复发
不在 site-packages 留中文路径 `.pth`；依赖一律用 venv 隔离，避免污染全局环境。
