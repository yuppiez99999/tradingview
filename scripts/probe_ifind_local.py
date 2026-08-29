import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

env_path = os.path.join(REPO_ROOT, ".env")
if os.path.exists(env_path):
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

import iFinDPy  # noqa: E402

user = os.getenv("IFIND_USER", "")
pwd = os.getenv("IFIND_PASS", "")
print("user=", user, "pwd=", pwd)
if not user or not pwd:
    sys.exit(1)

ret = iFinDPy.THS_iFinDLogin(user, pwd)
print("login_ret=", ret)

print("\n=== THS_HistoryQuotes ===")
for code in ["600276.SH", "000408.SZ", "512170.SH"]:
    try:
        raw = iFinDPy.THS_HistoryQuotes(
            code, "最新价,涨跌幅,成交额,成交量", "", "2026-07-14", "2026-07-16"
        )
        print(
            code,
            "type=",
            type(raw),
            "errorcode=",
            getattr(raw, "errorcode", ""),
            "errmsg=",
            getattr(raw, "errmsg", ""),
        )
        if hasattr(raw, "data") and raw.data is not None:
            print(" data=", str(raw.data)[:300])
        else:
            print(" raw=", str(raw)[:300])
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
        TimeoutError,
        ConnectionError,
    ) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        print(code, "EXC", repr(e))

print("\n=== THS_HQ ===")
for code in ["600276.SH", "000408.SZ", "512170.SH"]:
    try:
        raw = iFinDPy.THS_HQ(
            code, "最新价,涨跌幅,成交额,成交量", "", "2026-07-14", "2026-07-16"
        )
        print(
            code,
            "type=",
            type(raw),
            "errorcode=",
            getattr(raw, "errorcode", ""),
            "errmsg=",
            getattr(raw, "errmsg", ""),
        )
        if hasattr(raw, "data") and raw.data is not None:
            print(" data=", str(raw.data)[:300])
        else:
            print(" raw=", str(raw)[:300])
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
        TimeoutError,
        ConnectionError,
    ) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        print(code, "EXC", repr(e))

print("\n=== THS_SS ===")
for code in ["600276.SH", "000408.SZ", "512170.SH"]:
    try:
        raw = iFinDPy.THS_SS(
            code, "最新价,涨跌幅,成交额,成交量", "", "2026-07-14", "2026-07-16"
        )
        print(
            code,
            "type=",
            type(raw),
            "errorcode=",
            getattr(raw, "errorcode", ""),
            "errmsg=",
            getattr(raw, "errmsg", ""),
        )
        if hasattr(raw, "data") and raw.data is not None:
            print(" data=", str(raw.data)[:300])
        else:
            print(" raw=", str(raw)[:300])
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RuntimeError,
        OSError,
        TimeoutError,
        ConnectionError,
    ) as e:
        # 数据处理/计算/IO 异常: 格式/类型/字段/属性/运行时/网络/超时
        print(code, "EXC", repr(e))

print("\n=== THS_RQ ===")
for code in ["600276.SH", "000408.SZ", "512170.SH"]:
    raw = iFinDPy.THS_RQ(code, "最新价,涨跌幅,成交额,成交量")
    print(
        code,
        "type=",
        type(raw),
        "errorcode=",
        getattr(raw, "errorcode", ""),
        "errmsg=",
        getattr(raw, "errmsg", ""),
    )
    if hasattr(raw, "data") and raw.data is not None:
        print(" data=", str(raw.data)[:300])
    else:
        print(" raw=", str(raw)[:300])
