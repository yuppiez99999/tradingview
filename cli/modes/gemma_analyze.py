"""
LLM 分析增强模式 — v5.10 集成
独立运行，不依赖主程序的重型模块
"""

import argparse
import json
import os


def run_gemma_analyze(args):
    """LLM 分析增强模式"""
    model_name = getattr(args, 'model', None) or getattr(args, 'gemma_model', None)
    prompt = getattr(args, 'prompt', None) or getattr(args, 'gemma_prompt', None)
    news = getattr(args, 'news', None) or getattr(args, 'gemma_news', None)
    stock_code = getattr(args, 'stock', None) or getattr(args, 'gemma_stock', None)
    output = getattr(args, 'output', None) or getattr(args, 'gemma_output', None)

    if not model_name:
        model_name = 'qwen2.5:7b'

    print("\n" + "=" * 70)
    print("  💎 LLM 分析增强")
    print("=" * 70)
    print(f"  模型: {model_name}")

    if news:
        system_prompt = (
            "你是专业的A股金融分析师，擅长快速解读新闻对股价的量化影响。"
            "输出简洁、直接，避免背景铺垫。每个判断必须有数据或逻辑支撑。"
        )
        user_prompt = (
            f"新闻内容：{news}\n\n"
            "请按以下格式分析:\n"
            "1. 影响方向: 上涨/下跌/中性\n"
            "2. 影响程度: 高/中/低 (说明原因，≤30字)\n"
            "3. 投资建议: 买入/持有/卖出 (置信度: 0-100%)\n"
            "4. 风险提示: 一句话\n"
            "示例: '方向:上涨 | 程度:中-行业政策利好 | 建议:买入(75%) | 风险:政策执行力度不确定'"
        )
    elif stock_code:
        system_prompt = (
            "你是专业的A股金融分析师，擅长个股基本面+技术面综合诊断。"
            "输出简洁、直接。"
        )
        user_prompt = (
            f"股票代码：{stock_code}\n\n"
            "请按以下格式分析:\n"
            "1. 行业定位: 所属行业及地位 (≤20字)\n"
            "2. 基本面: 关键财务指标健康度 (≤30字)\n"
            "3. 技术面: 当前趋势判断 (≤30字)\n"
            "4. 建议: 买入/持有/卖出 (置信度:X%)"
        )
    elif prompt:
        system_prompt = "你是专业的金融分析师。输出简洁、数据驱动。"
        user_prompt = prompt
    else:
        print("\n❌ 请提供 --news、--stock 或 --prompt")
        return None

    print("\n📝 生成分析中...")

    try:
        import requests
        # v2.0: 使用 Ollama chat API (支持 system/user 角色分离)
        resp = requests.post(
            'http://localhost:11434/api/chat',
            json={
                'model': model_name,
                'messages': [
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_prompt},
                ],
                'stream': False,
            },
            timeout=120,
        )

        if resp.status_code != 200:
            print(f"\n❌ Ollama 请求失败: {resp.status_code}")
            try:
                error_detail = resp.json().get('error', '')
                if error_detail:
                    print(f"   错误详情: {error_detail}")
            except (ValueError, AttributeError):
                # F-9 修复: 错误响应体非 JSON 时, 仅跳过详情解析, 不吞掉其他异常
                pass
            print("   可能原因: ollama 服务未启动或模型未导入")
            print("   启动命令: ollama serve")
            return None

        result = resp.json()
        response = result.get('message', {}).get('content', '').strip()
        if not response:
            # 回退: generate API
            response = result.get('response', '').strip()

    except ImportError:
        print("\n❌ 需要安装 requests")
        print("   pip install requests")
        return None
    except Exception as e:
        print(f"\n❌ 生成失败: {e}")
        print("   提示: 请确保 ollama 服务已启动 (ollama serve)")
        return None

    print("\n" + "=" * 70)
    print("📊 分析结果")
    print("=" * 70)
    print(response)
    print("=" * 70)

    if output:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        output_path = os.path.join(base_dir, 'reports', output)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        result_data = {
            'model': model_name,
            'prompt': user_prompt,
            'analysis': response,
            'timestamp': str(__import__('datetime').datetime.now()),
        }
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2)
        print(f"\n✅ 结果已保存: {output_path}")

    return response


run_gemma_analyze_mode = run_gemma_analyze


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='LLM 分析增强模式')
    parser.add_argument('--model', type=str, default=None, help='Ollama 模型名')
    parser.add_argument('--news', type=str, default=None, help='新闻内容')
    parser.add_argument('--stock', type=str, default=None, help='股票代码')
    parser.add_argument('--prompt', type=str, default=None, help='自定义提示')
    parser.add_argument('--output', type=str, default=None, help='输出文件名')
    args = parser.parse_args()
    run_gemma_analyze(args)
