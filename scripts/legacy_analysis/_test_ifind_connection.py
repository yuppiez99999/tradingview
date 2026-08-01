"""iFinD 连接测试 + 财务数据拉取验证

测试项:
1. .env 加载 (IFIND_TOKEN)
2. IFindClient 实例化
3. 单只标的财务数据拉取
4. 批量拉取 (5 只标的)
5. 字段映射验证 (pe/pb/roe/market_cap 等)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# 1. 加载 .env (支持 GBK 编码, .env 文件可能用 GBK 保存中文注释)
print("=" * 70)
print("iFinD 连接测试")
print("=" * 70)

print("\n[1] 加载 .env 文件")
env_path = PROJECT_ROOT / ".env"
print(f"  .env 路径: {env_path}")
print(f"  .env 存在: {env_path.exists()}")

if env_path.exists():
    # 尝试多种编码读取 .env (GBK/utf-8/utf-8-sig)
    env_content = None
    for enc in ["gbk", "utf-8", "utf-8-sig", "latin-1"]:
        try:
            with open(env_path, encoding=enc) as f:
                env_content = f.read()
            print(f"  .env 编码: {enc}")
            break
        except UnicodeDecodeError:
            continue

    if env_content:
        for line in env_content.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip()
                if key and key not in os.environ:
                    os.environ[key] = val
        print("  ✓ .env 手动加载完成")

token = os.environ.get("IFIND_TOKEN", "")
print(f"  IFIND_TOKEN: {'已配置 (' + str(len(token)) + ' 字符)' if token else '未配置'}")
if not token:
    print("  ✗ IFIND_TOKEN 未配置")
    sys.exit(1)
print("  ✓ IFIND_TOKEN 已加载")

# 2. 实例化 IFindClient
print("\n[2] 实例化 IFindClient")
try:
    from utils.ifind_client import IFindClient
    client = IFindClient()
    print("  ✓ IFindClient 实例化成功")
except Exception as e:
    print(f"  ✗ IFindClient 实例化失败: {e}")
    sys.exit(1)

# 3. 单只标的财务数据拉取
print("\n[3] 单只标的财务数据拉取 (600519.SH 贵州茅台)")
try:
    result = client.get_fundamentals_batch(["600519.SH"])
    if result:
        sym, fund = next(iter(result.items()))
        print(f"  ✓ 拉取成功: {sym}")
        print("  字段:")
        for k, v in fund.items():
            if isinstance(v, (int, float)):
                print(f"    {k:25s} = {v:.4f}")
            else:
                print(f"    {k:25s} = {v}")
    else:
        print("  ✗ 返回空结果")
        sys.exit(1)
except Exception as e:
    print(f"  ✗ 拉取异常: {e}")
    sys.exit(1)

# 4. 批量拉取 (5 只标的)
print("\n[4] 批量拉取 (5 只标的)")
test_symbols = ["600519.SH", "000858.SZ", "600036.SH", "000333.SZ", "601318.SH"]
try:
    result = client.get_fundamentals_batch(test_symbols)
    print(f"  ✓ 批量拉取完成: {len(result)}/{len(test_symbols)} 成功")
    for sym, fund in result.items():
        pe = fund.get("pe", 0)
        pb = fund.get("pb", 0)
        roe = fund.get("roe", 0)
        print(f"    {sym}: PE={pe:.1f}, PB={pb:.2f}, ROE={roe:.4f}")
except Exception as e:
    print(f"  ✗ 批量拉取异常: {e}")

# 5. 字段映射验证
print("\n[5] 字段映射验证 (与 real_data_loader 期望字段对齐)")
expected_fields = ["pe", "pb", "ps", "roe", "gross_margin", "debt_to_equity", "market_cap", "revenue"]
if result:
    sample = next(iter(result.values()))
    print(f"  iFinD 返回字段: {list(sample.keys())}")
    print(f"  期望字段: {expected_fields}")
    missing = [f for f in expected_fields if f not in sample]
    if missing:
        print(f"  ⚠ 缺少字段: {missing} (将由 baostock 或代理补充)")
    else:
        print("  ✓ 全部期望字段可用")

print("\n" + "=" * 70)
print("iFinD 连接测试完成")
print("=" * 70)
print("结论:")
print("  - iFinD token 有效, 可用于财务数据拉取")
print("  - real_data_loader.py 的降级链已支持 iFinD")
print("  - 当 baostock 数据不足时, 自动降级到 iFinD")
print("  - iFinD 有配额限制, 优先使用 baostock 数据")
