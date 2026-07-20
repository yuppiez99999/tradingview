import os

path = r"e:\各种PY程序\28-终极量化交易系统7.1\qlib_v7_train.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

old = '''    os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"
    
    import qlib
    qlib.init(provider_uri=QLIB_DATA_DIR, region="cn")'''

new = '''    os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"

    # 将本地 qlib 源码目录加入路径，避免被项目根目录下的 qlib/ 命名空间包遮挡
    _qlib_src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qlib")
    if _qlib_src not in sys.path:
        sys.path.insert(0, _qlib_src)

    import qlib
    qlib.init(provider_uri=QLIB_DATA_DIR, region="cn")'''

if old not in content:
    print("OLD block not found")
else:
    content = content.replace(old, new, 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print("patched qlib_v7_train.py")
