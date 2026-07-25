# -*- coding: utf-8 -*-
"""yizhao数据加载器桥接"""
import logging
_log = logging.getLogger("bridges.yizhao_data")

class YiZhaoDocument:
    """yizhao文档模型"""
    def __init__(self, title="", content="", url="", date="", source=""):
        self.title = title
        self.content = content
        self.url = url
        self.date = date
        self.source = source

class YiZhaoDataLoader:
    """占位 - 接入yizhao数据源后替换"""
    def __init__(self, *args, **kwargs):
        _log.info("YiZhaoDataLoader 占位初始化")
    def load_articles(self, *args, **kwargs): return []
    def search_articles(self, *args, **kwargs): return []
    def load_data(self, *args, **kwargs): return {}
    def get_data(self, *args, **kwargs): return {}
    def get_documents(self, *args, **kwargs): return []
