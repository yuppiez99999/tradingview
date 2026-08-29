"""TDAM client 端到端验证脚本 — Phase 0a 验证用.

验证修正后的 tdam_client.py 能调通真实 TDAM 端点。
不依赖 Feature Flag (直接绕过), 仅验证连通性和响应解析。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 把项目根加入 sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from utils.tdam_client import TDAMClient, TDAMConfig


def main() -> int:
    print("=" * 60)
    print("TDAM Client 端到端验证")
    print("=" * 60)

    # 1. 初始化 client (从 .admin-credentials.json 自动加载 user_key + user_id)
    config = TDAMConfig(base_url="http://127.0.0.1:8420")
    client = TDAMClient(config)

    # 绕过 Feature Flag (验证用, 生产环境需启用 USE_TDAM_MEMORY_ENHANCEMENT)
    client._flag_checked = True
    client._flag_enabled = True

    print("\n[配置]")
    print(f"  base_url:    {config.base_url}")
    print(
        f"  user_key:    {config.user_key[:11]}****{config.user_key[-4:] if config.user_key else '(空)'}"
    )
    print(f"  user_id:     {config.user_id}")
    print(f"  service_id:  {config.service_id}")
    print(f"  team_id:     {config.team_id}")

    # 2. 健康检查
    print("\n[1] 健康检查 GET /health")
    ok = client.health_check()
    print(f"  结果: {'通过' if ok else '失败'}")
    if not ok:
        print("  健康检查失败, 终止测试")
        return 1

    # 3. list_skills
    print("\n[2] POST /v3/skill/list")
    result = client.list_skills(limit=10)
    print(f"  success: {'error' not in result}")
    print(f"  total:   {result.get('total', 0)}")
    print(f"  items:   {len(result.get('items', []))}")
    if "error" in result:
        print(f"  error:   {result['error']}")

    # 4. list_knowledge
    print("\n[3] POST /v3/knowledge/list")
    result = client.list_knowledge(limit=10)
    print(f"  success: {'error' not in result}")
    print(f"  total:   {result.get('total', 0)}")
    if "error" in result:
        print(f"  error:   {result['error']}")

    # 5. search_memory (skill)
    print("\n[4] POST /v3/skill/search (query='气象因子')")
    result = client.search_memory(query="气象因子", asset_type="skill", top_k=3)
    print(f"  success:    {result.success}")
    print(f"  degraded:   {result.degraded}")
    print(f"  total:      {result.total}")
    print(f"  latency_ms: {result.latency_ms:.0f}")
    if result.error:
        print(f"  error:      {result.error}")

    # 6. search_memory (conversation)
    print("\n[5] POST /v3/conversation/search (query='GNN')")
    result = client.search_memory(query="GNN", asset_type="conversation", top_k=3)
    print(f"  success:    {result.success}")
    print(f"  total:      {result.total}")
    print(f"  latency_ms: {result.latency_ms:.0f}")
    if result.error:
        print(f"  error:      {result.error}")

    # 7. 熔断器状态
    print("\n[熔断器]")
    print(f"  state:          {client._circuit.state.value}")
    print(f"  failure_count:  {client._circuit.failure_count}")

    print(f"\n{'=' * 60}")
    print("验证完成")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
