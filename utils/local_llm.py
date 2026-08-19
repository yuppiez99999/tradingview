import logging
import os
from typing import Any, Dict, Generator, List, Optional

logger = logging.getLogger(__name__)


class LocalLLMClient:
    """本地 LLM 推理客户端 — 基于 llama-cpp-python

    支持 GGUF 格式模型，CPU/GPU 混合推理
    """

    def __init__(
        self,
        model_path: Optional[str] = None,
        n_ctx: int = 2048,
        n_gpu_layers: int = 0,
        n_threads: int = 4,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ):
        """初始化本地 LLM 客户端

        Args:
            model_path: GGUF 模型文件路径
            n_ctx: 上下文窗口大小
            n_gpu_layers: GPU 层数（0=纯CPU）
            n_threads: CPU 线程数
            temperature: 温度参数
            max_tokens: 最大生成 token 数
        """
        try:
            from utils.paths import get_qwen_model_path
            default_model = get_qwen_model_path()
        except ImportError:
            default_model = r'D:\models\Qwen\Qwen2.5-1.5B-Instruct\qwen2.5-1.5b-instruct-q4_k_m.gguf'
        self.model_path = model_path or os.environ.get(
            'LOCAL_LLM_MODEL_PATH',
            default_model
        )
        self.n_ctx = n_ctx
        self.n_gpu_layers = n_gpu_layers
        self.n_threads = n_threads
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._model = None
        self._available = None

    def is_available(self) -> bool:
        """检查本地模型是否可用"""
        if self._available is not None:
            return self._available

        try:
            if not os.path.exists(self.model_path):
                logger.warning(f'本地模型文件不存在: {self.model_path}')
                self._available = False
                return False

            from llama_cpp import Llama  # noqa: F401
            self._available = True
            return True
        except ImportError:
            logger.warning('llama-cpp-python 未安装，本地模型不可用')
            self._available = False
            return False
        except (AttributeError, TypeError, ValueError, OSError) as e:
            logger.warning(f'本地模型检查失败: {e}')
            self._available = False
            return False

    def _load_model(self):
        """加载模型（懒加载）"""
        if self._model is not None:
            return self._model

        if not self.is_available():
            raise RuntimeError('本地模型不可用')

        from llama_cpp import Llama

        logger.info(f'加载本地模型: {self.model_path}')
        logger.info(f'  上下文窗口: {self.n_ctx}')
        logger.info(f'  GPU 层数: {self.n_gpu_layers}')
        logger.info(f'  CPU 线程: {self.n_threads}')

        self._model = Llama(
            model_path=self.model_path,
            n_ctx=self.n_ctx,
            n_gpu_layers=self.n_gpu_layers,
            n_threads=self.n_threads,
            verbose=False,
        )

        logger.info('✅ 本地模型加载完成')
        return self._model

    def _format_prompt(self, messages: List[Dict[str, str]]) -> str:
        """格式化消息为 Qwen2.5 chat template

        Args:
            messages: 消息列表，格式为 [{"role": "user"/"system"/"assistant", "content": "..."}]

        Returns:
            格式化后的 prompt 字符串
        """
        prompt = ""
        for msg in messages:
            role = msg.get('role', '')
            content = msg.get('content', '')

            if role == 'system':
                prompt += f'<|im_start|>system\n{content}<|im_end|>\n'
            elif role == 'user':
                prompt += f'<|im_start|>user\n{content}<|im_end|>\n'
            elif role == 'assistant':
                prompt += f'<|im_start|>assistant\n{content}<|im_end|>\n'

        prompt += '<|im_start|>assistant\n'
        return prompt

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stream: bool = False,
    ) -> Dict[str, Any]:
        """对话接口（兼容 OpenAI 格式）

        Args:
            messages: 消息列表
            temperature: 温度参数
            max_tokens: 最大生成 token 数
            stream: 是否流式输出

        Returns:
            响应字典，格式兼容 OpenAI API
        """
        model = self._load_model()

        prompt = self._format_prompt(messages)

        output = model(
            prompt=prompt,
            temperature=temperature or self.temperature,
            max_tokens=max_tokens or self.max_tokens,
            stop=['<|im_end|>'],
            stream=stream,
        )

        if stream:
            return self._stream_response(output)

        content = output.get('choices', [{}])[0].get('text', '')

        return {
            'choices': [
                {
                    'message': {
                        'role': 'assistant',
                        'content': content,
                    }
                }
            ],
            'usage': {
                'prompt_tokens': output.get('usage', {}).get('prompt_tokens', 0),
                'completion_tokens': output.get('usage', {}).get('completion_tokens', 0),
                'total_tokens': output.get('usage', {}).get('total_tokens', 0),
            },
            'model': 'local-qwen2.5-72b',
        }

    def _stream_response(self, output_generator) -> Generator[str, None, None]:
        """流式响应生成器"""
        for chunk in output_generator:
            text = chunk.get('choices', [{}])[0].get('text', '')
            if text:
                yield text

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """简化的生成接口

        Args:
            prompt: 用户提示
            system_prompt: 系统提示
            temperature: 温度参数
            max_tokens: 最大生成 token 数

        Returns:
            生成的文本
        """
        messages = []
        if system_prompt:
            messages.append({'role': 'system', 'content': system_prompt})
        messages.append({'role': 'user', 'content': prompt})

        response = self.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        return response['choices'][0]['message']['content']


_default_client = None


def get_local_llm() -> Optional[LocalLLMClient]:
    """获取默认的本地 LLM 客户端（单例）"""
    global _default_client

    if _default_client is None:
        _default_client = LocalLLMClient()

    if not _default_client.is_available():
        return None

    return _default_client


def local_llm_available() -> bool:
    """检查本地 LLM 是否可用"""
    client = get_local_llm()
    return client is not None
