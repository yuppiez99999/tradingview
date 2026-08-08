# -*- coding: utf-8 -*-
"""
GLM-5 本地客户端 - 量化交易系统集成模块
支持 ModelScope SDK 加载 + API 调用双模式

使用方式:
    from utils.glm5_client import GLM5Client
    client = GLM5Client(mode="local")  # 或 "api"
    result = client.chat("分析今天的市场行情")
"""

import os
import sys
import json
import logging
from typing import Optional, Dict, Any, List, Generator
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime

# 配置日志
logger = logging.getLogger(__name__)


@dataclass
class GLM5Config:
    """GLM-5 配置"""
    mode: str = "api"  # "local" | "api" | "ollama" | "local_gguf"
    
    # Local/ModelScope 模式配置
    model_path: str = "ZhipuAI/GLM-5"
    device: str = "auto"  # "auto" | "cuda" | "cpu"
    dtype: str = "float16"  # "float16" | "float32" | "int8"
    max_new_tokens: int = 3000
    temperature: float = 0.3
    top_p: float = 0.9
    
    # Local GGUF 模式配置（llama-cpp-python）
    gguf_model_path: str = os.environ.get("LOCAL_LLM_MODEL_PATH", "")
    gguf_n_ctx: int = 8192
    gguf_n_gpu_layers: int = 0  # 0=纯CPU，有GPU可设为50+
    gguf_n_threads: int = 8
    
    # API 模式配置
    api_key: str = ""
    api_base: str = "https://ark.cn-beijing.volces.com/api/v3/responses"  # 默认豆包API
    api_model: str = "doubao-seed-1-6-251015"  # 默认使用豆包Speed（最快响应）
    api_model_fallbacks: List[str] = field(default_factory=lambda: [
        "doubao-pro-32k",       # 备选1：长上下文
        "glm-4-plus",           # 备选2：智谱AI
        "glm-4-flash",          # 备选3：快速响应
    ])
    
    # Ollama 模式配置
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "glm-5"
    
    # 系统提示词（金融场景优化）
    system_prompt: str = """你是一位资深的量化交易分析师和投资顾问。请根据用户提供的信息：
1. 用专业、客观的语言进行分析
2. 给出数据支撑的结论，避免主观臆断
3. 明确风险提示和操作建议
4. 回复格式清晰，适合直接用于交易报告"""

    def __post_init__(self):
        # 从环境变量读取配置（优先豆包，其次智谱）
        if not self.api_key:
            self.api_key = os.environ.get("VOLCENGINE_API_KEY", "") or os.environ.get("ZHIPUAI_API_KEY", "")
        if os.environ.get("VOLCENGINE_API_BASE"):
            self.api_base = os.environ.get("VOLCENGINE_API_BASE")
        elif os.environ.get("ZHIPUAI_API_BASE"):
            self.api_base = os.environ.get("ZHIPUAI_API_BASE")
        if os.environ.get("GLM5_MODE"):
            self.mode = os.environ.get("GLM5_MODE")
        if os.environ.get("VOLCENGINE_MODEL"):
            self.api_model = os.environ.get("VOLCENGINE_MODEL")
        elif os.environ.get("ZHIPUAI_MODEL"):
            self.api_model = os.environ.get("ZHIPUAI_MODEL")
        if os.environ.get("LOCAL_LLM_MODEL_PATH"):
            self.gguf_model_path = os.environ.get("LOCAL_LLM_MODEL_PATH")
        if os.environ.get("GGUF_N_GPU_LAYERS"):
            self.gguf_n_gpu_layers = int(os.environ.get("GGUF_N_GPU_LAYERS"))


# 模型加载缓存（全局单例）
_model_cache = {}


class GLM5Client:
    """
    GLM-5 客户端 - 统一接口
    
    使用示例:
        # API 模式（推荐快速开始）
        client = GLM5Client(mode="api", api_key="your_key")
        resp = client.chat("市场分析")
        
        # 本地模式（需要 GPU）
        client = GLM5Client(mode="local")
        resp = client.chat("市场分析")
        
        # Ollama 模式（零配置）
        client = GLM5Client(mode="ollama")
        resp = client.chat("市场分析")
    """
    
    def __init__(self, config: Optional[GLM5Config] = None, **kwargs):
        self.config = config or GLM5Config(**kwargs)
        self._model = None
        self._tokenizer = None
        self._client = None
        
        # 构建缓存键
        cache_key = f"{self.config.mode}_{self.config.api_model}_{self.config.model_path}"
        
        logger.info(f"初始化 GLM-5 客端, 模式={self.config.mode}")
        
        # 检查缓存
        if cache_key in _model_cache:
            cached = _model_cache[cache_key]
            self._model = cached.get('model')
            self._tokenizer = cached.get('tokenizer')
            self._client = cached.get('client')
            self._llm = cached.get('llm')
            logger.info(f"✓ 从缓存加载模型: {cache_key}")
            return
        
        # 根据模式初始化
        try:
            if self.config.mode == "local":
                self._init_local()
            elif self.config.mode == "api":
                self._init_api()
            elif self.config.mode == "ollama":
                self._init_ollama()
            elif self.config.mode == "local_gguf":
                self._init_local_gguf()
            else:
                raise ValueError(f"不支持的模式: {self.config.mode}")
            
            # 缓存模型
            _model_cache[cache_key] = {
                'model': self._model,
                'tokenizer': self._tokenizer,
                'client': self._client,
                'llm': getattr(self, '_llm', None),
                'config': self.config,
            }
            logger.info(f"✓ 模型已缓存: {cache_key}")
            
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.error(f"GLM-5 初始化失败: {e}")
            raise
    
    def _init_local(self):
        """初始化本地模型 (ModelScope + Transformers)"""
        try:
            from modelscope import AutoModelForCausalLM, AutoTokenizer
            import torch
            
            logger.info(f"加载本地模型: {self.config.model_path}")
            
            self._tokenizer = AutoTokenizer.from_pretrained(
                self.config.model_path,
                trust_remote_code=True
            )
            
            dtype_map = {
                "float16": torch.float16,
                "float32": torch.float32,
                "bfloat16": torch.bfloat16,
                "int8": torch.int8,
            }
            torch_dtype = dtype_map.get(self.config.dtype, torch.float16)
            
            self._model = AutoModelForCausalLM.from_pretrained(
                self.config.model_path,
                torch_dtype=torch_dtype,
                device_map=self.config.device,
                trust_remote_code=True
            )
            self._model.eval()
            
            logger.info("✓ GLM-5 本地模型加载成功")
            
        except ImportError:
            logger.warning("本地依赖缺失, 请安装: pip install modelscope transformers torch")
            raise
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.error(f"本地模型加载失败: {e}")
            raise
    
    def _init_local_gguf(self):
        """初始化本地 GGUF 模型 (llama-cpp-python)"""
        try:
            from llama_cpp import Llama
            
            model_path = self.config.gguf_model_path
            
            if not os.path.exists(model_path):
                raise FileNotFoundError(f"GGUF 模型文件不存在: {model_path}")
            
            logger.info(f"加载本地 GGUF 模型: {model_path}")
            logger.info(f"  上下文窗口: {self.config.gguf_n_ctx}")
            logger.info(f"  GPU 层数: {self.config.gguf_n_gpu_layers}")
            logger.info(f"  CPU 线程: {self.config.gguf_n_threads}")
            
            self._llm = Llama(
                model_path=model_path,
                n_ctx=self.config.gguf_n_ctx,
                n_gpu_layers=self.config.gguf_n_gpu_layers,
                n_threads=self.config.gguf_n_threads,
                verbose=False,
            )
            
            logger.info("✓ 本地 GGUF 模型加载成功")
            
        except ImportError:
            logger.warning("llama-cpp-python 未安装, 请安装: pip install llama-cpp-python")
            raise
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.error(f"本地 GGUF 模型加载失败: {e}")
            raise
    
    def _init_api(self):
        """初始化 API 客户端（支持豆包和智谱AI）"""
        if not self.config.api_key:
            # 尝试从配置文件读取
            config_file = Path(__file__).parent.parent / "config" / "settings.yaml"
            if config_file.exists():
                import yaml
                with open(config_file, 'r', encoding='utf-8') as f:
                    settings = yaml.safe_load(f) or {}
                    # 支持多种配置格式（优先豆包，其次智谱）
                    self.config.api_key = (
                        os.environ.get('VOLCENGINE_API_KEY', '') or  # 豆包环境变量
                        os.environ.get('ZHIPUAI_API_KEY', '') or     # 智谱环境变量
                        settings.get('volcengine_api_key', '') or    # 豆包旧格式
                        settings.get('zhipuai_api_key', '') or       # 智谱旧格式
                        settings.get('glm5', {}).get('api_key', '')  # 新格式
                    )
            
            if not self.config.api_key:
                raise ValueError("API 模式需要提供 api_key, 可通过环境变量 VOLCENGINE_API_KEY 或 ZHIPUAI_API_KEY 设置")
        
        # 检查 API 类型：豆包使用 requests，智谱使用 zhipuai SDK
        try:
            import requests
            # 使用 Session 对象，禁用代理而不修改全局环境变量
            session = requests.Session()
            session.trust_env = False  # 禁用系统代理
            
            # 判断API类型：豆包API还是智谱API
            if 'ark.cn-beijing.volces.com' in self.config.api_base:
                self._api_type = 'volcengine'
            elif 'bigmodel.cn' in self.config.api_base:
                self._api_type = 'zhipuai'
            else:
                self._api_type = 'volcengine'  # 默认豆包
            
            self._client = session
            # API Key 脱敏输出
            masked_key = self.config.api_key[:4] + "***" + self.config.api_key[-4:] if len(self.config.api_key) >= 8 else "***"
            logger.info(f"✓ API 客户端初始化成功, 类型={self._api_type}, 模型={self.config.api_model}, Key={masked_key}")
        except ImportError:
            logger.warning("requests 包未安装, 请安装: pip install requests")
            raise
    
    def _init_ollama(self):
        """初始化 Ollama 客户端"""
        import requests
        try:
            # 检查 Ollama 服务是否可用
            resp = requests.get(f"{self.config.ollama_url}/api/tags", timeout=5)
            if resp.status_code != 200:
                raise ConnectionError(f"Ollama 服务不可用: {self.config.ollama_url}")
            
            models = [m['name'] for m in resp.json().get('models', [])]
            if not any(self.config.ollama_model in m for m in models):
                logger.warning(f"Ollama 中未找到模型 {self.config.ollama_model}, 需要先执行: ollama pull {self.config.ollama_model}")
            
            logger.info("✓ Ollama 客户端初始化成功")
        except requests.exceptions.ConnectionError:
            raise ConnectionError(f"无法连接 Ollama 服务 ({self.config.ollama_url}), 请先启动 Ollama")
    
    def chat(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        对话接口
        
        Args:
            message: 用户消息
            history: 历史对话 [{"role": "user/assistant", "content": "..."}]
            system_prompt: 系统提示词（覆盖默认）
            temperature: 温度参数
            max_tokens: 最大生成 token 数
            
        Returns:
            {"role": "assistant", "content": "...", "model": "glm-5", ...}
        """
        # 输入参数验证
        if not message or not message.strip():
            raise ValueError("消息内容不能为空")
        if len(message.strip()) > 100000:
            raise ValueError("消息内容不能超过100000字符")
        if temperature is not None and (temperature < 0 or temperature > 2):
            raise ValueError("temperature 参数必须在 0-2 之间")
        if max_tokens is not None and (max_tokens < 1 or max_tokens > 100000):
            raise ValueError("max_tokens 参数必须在 1-100000 之间")
        
        system_prompt = system_prompt or self.config.system_prompt
        temperature = temperature if temperature is not None else self.config.temperature
        max_tokens = max_tokens or self.config.max_new_tokens
        
        if self.config.mode == "local":
            return self._chat_local(message, history, system_prompt, temperature, max_tokens)
        elif self.config.mode == "api":
            return self._chat_api(message, history, system_prompt, temperature, max_tokens)
        elif self.config.mode == "ollama":
            return self._chat_ollama(message, history, system_prompt, temperature, max_tokens)
        elif self.config.mode == "local_gguf":
            return self._chat_local_gguf(message, history, system_prompt, temperature, max_tokens)
    
    def _chat_local(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]],
        system_prompt: str,
        temperature: float,
        max_tokens: int
    ) -> Dict[str, Any]:
        """本地模型对话"""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if history:
            messages.extend(history[-10:])  # 保留最近10轮
        messages.append({"role": "user", "content": message})
        
        response, new_history = self._model.chat(
            self._tokenizer,
            message,
            history=[(h['role'], h['content']) for h in (history or [])],
            temperature=temperature,
            max_new_tokens=max_tokens,
            **{k:v for k,v in {
                'top_p': self.config.top_p,
            }.items() if v}
        )
        
        return {
            "role": "assistant",
            "content": response,
            "model": f"glm-5-local-{self.config.model_path}",
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "created_at": datetime.now().isoformat(),
        }
    
    def _chat_local_gguf(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]],
        system_prompt: str,
        temperature: float,
        max_tokens: int
    ) -> Dict[str, Any]:
        """本地 GGUF 模型对话（Qwen2.5 chat template）"""
        # 构建 Qwen2.5 chat template 格式
        prompt = ""
        if system_prompt:
            prompt += f'<|im_start|>system\n{system_prompt}<|im_end|>\n'
        if history:
            for h in history[-10:]:
                prompt += f'<|im_start|>{h["role"]}\n{h["content"]}<|im_end|>\n'
        prompt += f'<|im_start|>user\n{message}<|im_end|>\n'
        prompt += '<|im_start|>assistant\n'
        
        output = self._llm(
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=['<|im_end|>'],
            stream=False,
        )
        
        content = output.get('choices', [{}])[0].get('text', '')
        usage = output.get('usage', {})
        
        return {
            "role": "assistant",
            "content": content,
            "model": "local-qwen2.5-72b-gguf",
            "usage": {
                "prompt_tokens": usage.get('prompt_tokens', 0),
                "completion_tokens": usage.get('completion_tokens', 0),
                "total_tokens": usage.get('total_tokens', 0),
            },
            "created_at": datetime.now().isoformat(),
        }
    
    def _chat_api(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]],
        system_prompt: str,
        temperature: float,
        max_tokens: int
    ) -> Dict[str, Any]:
        """API 模式对话（支持豆包和智谱AI，支持模型降级）"""
        # 模型列表：主模型 + 备选模型
        models_to_try = [self.config.api_model] + self.config.api_model_fallbacks
        
        last_error = None
        for model_name in models_to_try:
            try:
                # 根据 API 类型构建不同的请求
                if self._api_type == 'volcengine':
                    # 豆包 API 格式
                    headers = {
                        "Authorization": f"Bearer {self.config.api_key}",
                        "Content-Type": "application/json"
                    }
                    
                    # 构建豆包 API 专用消息格式
                    input_messages = []
                    if system_prompt:
                        input_messages.append({
                            "role": "user",
                            "content": [{"type": "input_text", "text": system_prompt}]
                        })
                    if history:
                        for h in history[-20:]:
                            input_messages.append({
                                "role": h['role'],
                                "content": [{"type": "input_text", "text": h['content']}]
                            })
                    input_messages.append({
                        "role": "user",
                        "content": [{"type": "input_text", "text": message}]
                    })
                    
                    payload = {
                        "model": model_name,
                        "input": input_messages,
                        "parameters": {
                            "temperature": temperature,
                            "max_tokens": max_tokens,
                            "top_p": self.config.top_p,
                        }
                    }
                    
                    response = self._client.post(
                        self.config.api_base,
                        headers=headers,
                        json=payload,
                        timeout=120
                    )
                    response.raise_for_status()
                    data = response.json()
                    
                    # 解析豆包 API 返回格式
                    content = ""
                    for output in data.get('output', []):
                        if output.get('type') == 'message':
                            for content_item in output.get('content', []):
                                if content_item.get('type') == 'output_text':
                                    content = content_item.get('text', '')
                        elif output.get('type') == 'output_text':
                            content = output.get('content', '')
                    
                else:
                    # 智谱 AI API 格式（使用 zhipuai SDK）
                    from zhipuai import ZhipuAI
                    zhipu_client = ZhipuAI(api_key=self.config.api_key)
                    
                    messages = []
                    if system_prompt:
                        messages.append({"role": "system", "content": system_prompt})
                    if history:
                        messages.extend(history[-20:])
                    messages.append({"role": "user", "content": message})
                    
                    response = zhipu_client.chat.completions.create(
                        model=model_name,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        top_p=self.config.top_p,
                    )
                    
                    choice = response.choices[0]
                    content = choice.message.content or ""
                
                # 检查返回内容是否为空
                if not content.strip():
                    logger.warning(f"模型 {model_name} 返回空内容, 尝试下一个模型...")
                    last_error = f"模型 {model_name} 返回空内容"
                    continue
                
                # 如果使用了备选模型，记录日志
                if model_name != self.config.api_model:
                    logger.info(f"主模型不可用, 使用降级模型: {model_name}")
                
                return {
                    "role": "assistant",
                    "content": content,
                    "model": model_name,
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    "finish_reason": "done",
                    "created_at": datetime.now().isoformat(),
                }
            except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
                logger.warning(f"模型 {model_name} 调用失败: {e}")
                last_error = str(e)
                continue
        
        # 所有模型都失败
        error_msg = f"所有模型调用失败, 最后错误: {last_error}"
        logger.error(error_msg)
        raise RuntimeError(error_msg)
    
    def _chat_ollama(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]],
        system_prompt: str,
        temperature: float,
        max_tokens: int
    ) -> Dict[str, Any]:
        """Ollama 对话"""
        import requests
        
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if history:
            messages.extend(history[-10:])
        messages.append({"role": "user", "content": message})
        
        try:
            response = requests.post(
                f"{self.config.ollama_url}/api/chat",
                json={
                    "model": self.config.ollama_model,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "temperature": temperature,
                        "num_predict": max_tokens,
                        "top_p": self.config.top_p,
                    }
                },
                timeout=120
            )
            response.raise_for_status()
            data = response.json()
            
            return {
                "role": "assistant",
                "content": data["message"]["content"],
                "model": f"ollama/{self.config.ollama_model}",
                "usage": {
                    "prompt_count": data.get("prompt_eval_count", 0),
                    "completion_count": data.get("eval_count", 0),
                },
                "created_at": datetime.now().isoformat(),
            }
        except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
            logger.error(f"Ollama 调用失败: {e}")
            raise
    
    def chat_stream(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
        **kwargs
    ) -> Generator[str, None, None]:
        """流式对话（仅支持 API 和 Ollama 模式）"""
        if self.config.mode == "local":
            # 本地模式不支持流式，返回完整响应
            result = self.chat(message, history, **kwargs)
            yield result["content"]
            return
        
        system_prompt = kwargs.pop("system_prompt", self.config.system_prompt)
        temperature = kwargs.pop("temperature", self.config.temperature)
        max_tokens = kwargs.pop("max_tokens", self.config.max_new_tokens)
        
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if history:
            messages.extend(history[-20:])
        messages.append({"role": "user", "content": message})
        
        if self.config.mode == "api":
            stream = self._client.chat.completions.create(
                model=self.config.api_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True
            )
            for chunk in stream:
                if chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        
        elif self.config.mode == "ollama":
            import requests
            with requests.post(
                f"{self.config.ollama_url}/api/chat",
                json={
                    "model": self.config.ollama_model,
                    "messages": messages,
                    "stream": True,
                    "options": {
                        "temperature": temperature,
                        "num_predict": max_tokens,
                    }
                },
                stream=True,
                timeout=120
            ) as resp:
                for line in resp.iter_lines():
                    if line:
                        data = json.loads(line)
                        if data.get("done"):
                            break
                        content = data.get("message", {}).get("content", "")
                        if content:
                            yield content
    
    def analyze_market(
        self,
        market_data: Dict[str, Any],
        focus_areas: Optional[List[str]] = None
    ) -> str:
        """
        市场分析专用接口（针对量化场景优化）
        
        Args:
            market_data: 市场数据字典, 包含指数、持仓、资金流等
            focus_areas: 关注领域 ["大盘", "持仓标的", "资金流向", "技术指标"]
        
        Returns:
            分析报告文本
        """
        focus_str = ", ".join(focus_areas or ["综合"])
        prompt = f"""请对以下市场数据进行专业分析:

【关注重点】: {focus_str}

【数据内容】:
{json.dumps(market_data, ensure_ascii=False, indent=2)}

请按以下结构输出:
1. 市场概况摘要（3-5句话）
2. 重点标的分析（如有持仓数据）
3. 关键信号识别（买卖点、异常波动等）
4. 风险评估与建议
5. 后续关注要点"""

        result = self.chat(prompt, temperature=0.6)
        return result["content"]
    
    def generate_report(
        self,
        report_type: str,
        context_data: Dict[str, Any]
    ) -> str:
        """
        报告生成专用接口
        
        Args:
            report_type: 报告类型 ["daily", "weekly", "rebalancing", "risk_alert"]
            context_data: 上下文数据
        
        Returns:
            格式化报告文本
        """
        type_prompts = {
            "daily": "生成每日交易日报",
            "weekly": "生成周度复盘报告",
            "rebalancing": "生成再平衡计划报告",
            "risk_alert": "生成风险预警报告",
        }
        
        prompt = f"""{type_prompts.get(report_type, '生成报告')}

基于以下数据生成专业的中文报告:

{json.dumps(context_data, ensure_ascii=False, indent=2)}

要求:
1. 结构清晰, 使用 Markdown 格式
2. 数据准确, 引用具体数值
3. 结论明确, 操作建议具体
4. 风险提示完整"""

        result = self.chat(prompt, temperature=0.4)
        return result["content"]
    
    @property
    def is_ready(self) -> bool:
        """检查服务是否就绪"""
        if self.config.mode == "local":
            return self._model is not None and self._tokenizer is not None
        elif self.config.mode == "api":
            return self._client is not None
        elif self.config.mode == "ollama":
            import requests
            try:
                resp = requests.get(f"{self.config.ollama_url}/api/tags", timeout=3)
                return resp.status_code == 200
            except requests.RequestException as e:
                logger.warning("Ollama 健康探测失败 (%s): %s", self.config.ollama_url, e)
                return False
        elif self.config.mode == "local_gguf":
            return hasattr(self, '_llm') and self._llm is not None
        return False
    
    def health_check(self) -> Dict[str, Any]:
        """健康检查"""
        status = {
            "mode": self.config.mode,
            "is_ready": self.is_ready,
            "timestamp": datetime.now().isoformat(),
        }
        
        if self.config.mode == "local":
            status.update({
                "model_path": self.config.model_path,
                "device": self.config.device,
                "dtype": self.config.dtype,
            })
        elif self.config.mode == "api":
            status.update({
                "api_model": self.config.api_model,
                "has_key": bool(self.config.api_key),
            })
        elif self.config.mode == "ollama":
            status.update({
                "ollama_model": self.config.ollama_model,
                "url": self.config.ollama_url,
            })
        elif self.config.mode == "local_gguf":
            status.update({
                "gguf_model_path": self.config.gguf_model_path,
                "n_ctx": self.config.gguf_n_ctx,
                "n_gpu_layers": self.config.gguf_n_gpu_layers,
            })
        
        return status


# ==================== 快捷函数 ====================

def get_glm5_client(**kwargs) -> GLM5Client:
    """获取 GLM-5 客户端实例（单例模式）"""
    if "_glm5_instance" not in globals():
        globals()["_glm5_instance"] = GLM5Client(**kwargs)
    return globals()["_glm5_instance"]


def quick_chat(message: str, **kwargs) -> str:
    """快速对话（一行代码调用）"""
    client = get_glm5_client(**kwargs)
    result = client.chat(message)
    return result["content"]


if __name__ == "__main__":
    # 测试代码
    logger.info("=" * 60)
    logger.info("GLM-5 客户端测试")
    logger.info("=" * 60)
    
    import argparse
    parser = argparse.ArgumentParser(description="GLM-5 测试工具")
    parser.add_argument("--mode", default="api", choices=["local", "api", "ollama", "local_gguf"], help="运行模式")
    parser.add_argument("--message", "-m", default="你好，请介绍一下你的能力", help="测试消息")
    parser.add_argument("--api-key", help="API 密钥")
    args = parser.parse_args()
    
    try:
        config_kwargs = {"mode": args.mode}
        if args.api_key:
            config_kwargs["api_key"] = args.api_key
        
        client = GLM5Client(**config_kwargs)
        
        logger.info(f"\n✓ 模式: {args.mode}")
        logger.info(f"✓ 就绪状态: {client.is_ready}")
        logger.info(f"\n健康检查: {json.dumps(client.health_check(), indent=2, ensure_ascii=False)}")
        logger.info(f"\n发送消息: {args.message}")
        logger.info("-" * 60)
        
        result = client.chat(args.message)
        logger.info(f"回复:")
        logger.info(result["content"])
        
        if "usage" in result:
            logger.info(f"\nToken 使用: {result['usage']}")
        
        logger.info("\n" + "=" * 60)
        logger.info("测试完成!")
        
    except Exception as e:  # noqa: BLE001  # fail-safe, 待后续精确化
        logger.error(f"\n❌ 错误: {e}")
        logger.info("\n可能的原因:")
        logger.info("  [API 模式]   需要设置 ZHIPUAI_API_KEY 环境变量或传入 --api-key")
        logger.info("  [本地模式]   需要 GPU 和 modelscope/transformers 库")
        logger.info("  [Ollama模式] 需要先启动 Ollama 并 pull glm-5 模型")
        logger.info("  [本地GGUF]   需要 llama-cpp-python 和 GGUF 模型文件")
        sys.exit(1)
