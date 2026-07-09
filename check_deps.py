import sys
print(sys.version)
mods = [
    'pandas','numpy','lightgbm','mlflow','pyyaml','pydantic_settings',
    'ruamel','redis','redis_lock','filelock','dill','fire','tqdm',
    'pymongo','loguru','gym','cvxpy','joblib','matplotlib','pyarrow'
]
for m in mods:
    try:
        __import__(m)
        print(m, True)
    except Exception as e:
        print(m, False, type(e).__name__)
