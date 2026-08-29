"""
假设验证模式 — v5.10 P0-9 重构迁移
================================================
从主文件迁移的 run_hypothesis_test 函数
"""

from core.context import strategy_registry


def run_hypothesis_test(args):
    """假设验证模式 - 验证交易假设"""
    print("\n🧪 假设验证模式")
    print("=" * 70)

    if args.list:
        print("\n📋 已注册的研究假设:")
        hypotheses = strategy_registry.list_hypotheses()
        if not hypotheses:
            print("  暂无注册的假设")
        else:
            for i, hyp in enumerate(hypotheses, 1):
                print(f"\n  {i}. {hyp.get('name', '未命名假设')}")
                print(f"     ID: {list(strategy_registry.hypotheses.keys())[i-1]}")
                print(f"     状态: {hyp.get('status', '未知')}")
                print(f"     创建时间: {hyp.get('created_at', '未知')}")
                if "description" in hyp:
                    print(f"     描述: {hyp['description']}")
        return

    if args.register:
        parts = args.register.split("|")
        if len(parts) >= 2:
            hyp_id = parts[0].strip()
            hyp_name = parts[1].strip()
            hyp_desc = parts[2].strip() if len(parts) > 2 else ""

            strategy_registry.register_hypothesis(
                hyp_id,
                {
                    "name": hyp_name,
                    "description": hyp_desc,
                    "methodology": "统计检验",
                    "evidence": [],
                },
            )
            print(f"\n✅ 假设已注册: {hyp_name}")
        else:
            print("\n❌ 注册格式错误，使用: --register id|名称|描述")
        return

    print("\n💡 使用方法:")
    print("  --list              列出所有研究假设")
    print("  --register id|名称|描述    注册新假设")
