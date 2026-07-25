# -*- coding: utf-8 -*-
"""v7.5 AI路由子包 — 多模型路由/GLM5决策/协调器/LLM客户端"""
try: from .model_router import ModelRouter
except ImportError: ModelRouter = None
try: from .glm5_engine import GLM5DecisionEngine
except ImportError: GLM5DecisionEngine = None
try: from .coordinator import AICoordinator
except ImportError: AICoordinator = None
try: from .llm_client import LLMClient
except ImportError: LLMClient = None
