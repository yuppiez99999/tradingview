"""
硅基流动API客户端
作为主要AI推理引擎，提供大模型服务
"""

from typing import Dict, List, Optional
from datetime import datetime
import requests
import logging

from ..config.config import API_CONFIG

class SiliconFlowClient:
    """
    硅基流动API客户端
    
    主要AI推理引擎，使用DeepSeek-V4系列模型
    """
    
    def __init__(self):
        """初始化硅基流动客户端"""
        self.logger = logging.getLogger('SiliconFlowClient')
        self.api_key = API_CONFIG['silicon_flow']['api_key']
        self.base_url = API_CONFIG['silicon_flow']['base_url']
        self.model_flash = API_CONFIG['silicon_flow']['model_flash']
        self.model_pro = API_CONFIG['silicon_flow']['model_pro']
        
        # 成本追踪
        self.total_tokens_used = 0
        self.total_cost = 0.0
        self.request_count = 0
        
        # 定价（单位：元/百万tokens）
        self.pricing = {
            self.model_flash: {'input': 1.0, 'output': 2.0, 'cache': 0.5},
            self.model_pro: {'input': 3.0, 'output': 6.0, 'cache': 1.5}
        }
        
        self.logger.info("硅基流动客户端初始化完成")
    
    def chat_completion(self, messages: List[Dict], model: str = None, 
                        temperature: float = 0.7, max_tokens: int = 2000) -> Dict:
        """
        聊天补全
        
        Args:
            messages: 消息列表
            model: 模型名称（None则使用Flash）
            temperature: 温度参数
            max_tokens: 最大token数
            
        Returns:
            响应字典
        """
        if model is None:
            model = self.model_flash  # 默认使用Flash
        
        try:
            # 构建请求
            url = f"{self.base_url}/chat/completions"
            headers = {
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json'
            }
            
            payload = {
                'model': model,
                'messages': messages,
                'temperature': temperature,
                'max_tokens': max_tokens
            }
            
            # 发送请求
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            
            result = response.json()
            
            # 更新统计信息
            usage = result.get('usage', {})
            input_tokens = usage.get('prompt_tokens', 0)
            output_tokens = usage.get('completion_tokens', 0)
            total_tokens = usage.get('total_tokens', 0)
            
            self._update_statistics(input_tokens, output_tokens, model)
            
            self.logger.info(f"AI请求成功: {model}, tokens={total_tokens}, cost={self._calculate_cost(input_tokens, output_tokens, model):.4f}元")
            
            return {
                'success': True,
                'content': result['choices'][0]['message']['content'],
                'usage': usage,
                'model': model,
                'timestamp': datetime.now()
            }
            
        except Exception as e:
            self.logger.error(f"AI请求失败: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'content': '',
                'usage': {},
                'model': model,
                'timestamp': datetime.now()
            }
    
    def analyze_sentiment(self, text: str) -> Dict:
        """
        情感分析
        
        Args:
            text: 待分析文本
            
        Returns:
            情感分析结果
        """
        messages = [
            {
                "role": "system",
                "content": "你是一个专业的金融情感分析师。请分析给定文本的市场情感，返回JSON格式结果：{\"sentiment\": \"positive/negative/neutral\", \"confidence\": 0.8, \"key_points\": [\"要点1\", \"要点2\"]}"
            },
            {
                "role": "user",
                "content": text
            }
        ]
        
        response = self.chat_completion(messages, model=self.model_flash)
        
        if response['success']:
            try:
                import json
                result = json.loads(response['content'])
                result['timestamp'] = datetime.now()
                return result
            except json.JSONDecodeError:
                return {
                    'sentiment': 'neutral',
                    'confidence': 0.5,
                    'key_points': ['解析失败'],
                    'timestamp': datetime.now()
                }
        
        return {
            'sentiment': 'neutral',
            'confidence': 0.0,
            'key_points': [],
            'timestamp': datetime.now()
        }
    
    def generate_investment_report(self, market_data: Dict, portfolio_status: Dict) -> str:
        """
        生成投资报告
        
        Args:
            market_data: 市场数据
            portfolio_status: 组合状态
            
        Returns:
            投资报告文本
        """
        messages = [
            {
                "role": "system",
                "content": "你是硅能智投的AI投资分析师，基于A股和能源产业深度认知，生成专业的投资晨报。报告应该包括：市场概要、组合表现、风险提示、操作建议。"
            },
            {
                "role": "user",
                "content": f"""
请基于以下信息生成今日投资晨报：

市场数据：
- 市场情绪：{market_data.get('market_sentiment', 'neutral')}
- 关键事件：{', '.join(market_data.get('key_events', []))}

组合状态：
- 总市值：{portfolio_status.get('total_value', 0):,.2f}元
- 现金余额：{portfolio_status.get('cash_balance', 0):,.2f}元
- 总盈亏：{portfolio_status.get('total_pnl', 0):,.2f}元
- 总盈亏率：{portfolio_status.get('total_pnl_percent', 0):.2%}
- 持仓数量：{portfolio_status.get('position_count', 0)}个

请生成结构化的专业投资晨报。
                """
            }
        ]
        
        response = self.chat_completion(messages, model=self.model_pro, max_tokens=3000)
        
        if response['success']:
            return response['content']
        else:
            return "报告生成失败，请检查AI服务连接"
    
    def get_trading_recommendation(self, risk_alerts: List, rebalance_info: Dict) -> List[Dict]:
        """
        获取交易建议
        
        Args:
            risk_alerts: 风险告警列表
            rebalance_info: 再平衡信息
            
        Returns:
            交易建议列表
        """
        messages = [
            {
                "role": "system",
                "content": "你是硅能智投的交易决策引擎。基于风险控制和组合再平衡需求，生成具体的交易建议。返回JSON格式：[{\"symbol\": \"代码\", \"action\": \"BUY/SELL\", \"reason\": \"理由\", \"priority\": \"HIGH/MEDIUM/LOW\", \"quantity\": 数量}]"
            },
            {
                "role": "user",
                "content": f"""
基于以下信息生成交易建议：

风险告警：{len(risk_alerts)}个
再平衡需求：{len(rebalance_info)}个标的需要调整

请生成具体的交易操作建议，优先处理风险控制。
                """
            }
        ]
        
        response = self.chat_completion(messages, model=self.model_flash, max_tokens=1500)
        
        if response['success']:
            try:
                import json
                recommendations = json.loads(response['content'])
                return recommendations
            except json.JSONDecodeError:
                return []
        
        return []
    
    def _update_statistics(self, input_tokens: int, output_tokens: int, model: str):
        """更新统计信息"""
        self.total_tokens_used += (input_tokens + output_tokens)
        cost = self._calculate_cost(input_tokens, output_tokens, model)
        self.total_cost += cost
        self.request_count += 1
    
    def _calculate_cost(self, input_tokens: int, output_tokens: int, model: str) -> float:
        """
        计算成本
        
        Args:
            input_tokens: 输入token数
            output_tokens: 输出token数
            model: 模型名称
            
        Returns:
            成本（元）
        """
        pricing = self.pricing.get(model, self.pricing[self.model_flash])
        
        input_cost = (input_tokens / 1_000_000) * pricing['input']
        output_cost = (output_tokens / 1_000_000) * pricing['output']
        
        return input_cost + output_cost
    
    def get_cost_summary(self) -> Dict:
        """
        获取成本摘要
        
        Returns:
            成本摘要字典
        """
        avg_cost_per_request = self.total_cost / self.request_count if self.request_count > 0 else 0
        avg_tokens_per_request = self.total_tokens_used / self.request_count if self.request_count > 0 else 0
        
        return {
            'total_tokens': self.total_tokens_used,
            'total_cost_yuan': round(self.total_cost, 2),
            'request_count': self.request_count,
            'avg_cost_per_request': round(avg_cost_per_request, 4),
            'avg_tokens_per_request': int(avg_tokens_per_request),
            'model_breakdown': {
                'flash': self.model_flash,
                'pro': self.model_pro
            }
        }
    
    def reset_statistics(self):
        """重置统计信息"""
        self.total_tokens_used = 0
        self.total_cost = 0.0
        self.request_count = 0
        self.logger.info("统计信息已重置")
    
    def check_connection(self) -> bool:
        """
        检查连接状态
        
        Returns:
            连接是否正常
        """
        if not self.api_key:
            self.logger.warning("未配置API密钥")
            return False
        
        try:
            # 发送测试请求
            test_messages = [{"role": "user", "content": "test"}]
            response = self.chat_completion(test_messages, max_tokens=10)
            return response['success']
            
        except Exception as e:
            self.logger.error(f"连接检查失败: {str(e)}")
            return False
    
    def get_optimal_model_for_task(self, task_type: str) -> str:
        """
        根据任务类型获取最优模型
        
        Args:
            task_type: 任务类型
            
        Returns:
            模型名称
        """
        if task_type in ['complex_analysis', 'strategy_optimization', 'detailed_report']:
            return self.model_pro  # 复杂任务使用Pro
        else:
            return self.model_flash  # 常规任务使用Flash
    
    def __str__(self):
        return f"SiliconFlowClient(requests={self.request_count}, cost={self.total_cost:.2f}元)"