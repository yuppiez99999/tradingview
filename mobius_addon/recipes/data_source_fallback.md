# 数据源降级链与跳过 Wind MCP 的坑

## 症状
行情获取失败或回退到预定义兜底价，分析结果失真；TDX 直连无数据；
免费源请求超时或返回空。

## 根因
全局优先级链：Wind 终端(P0) → Wind MCP(P1) → TDX(P2) → 免费源(P3)
→ 新浪(P4) → 本地缓存(P5) → 兜底价(P6)。常见问题：
- 跳过 Wind MCP 直接到底层，违反强制回退规则；
- TDX 需 `pytdx` + TCP 7709 端口出站，被防火墙/代理拦截；
- 系统代理（如 127.0.0.1:7897）拒绝转发国内金融 API 域名，akshare/requests 超时。

## 修复
- Wind 终端不可用时**必须**尝试 Wind MCP，再逐层降级，绝不跳过；
- 导入 `akshare` / `requests` **之前**设置 `NO_PROXY` 指向国内金融域名：
  `NO_PROXY=push2his.eastmoney.com,push2.eastmoney.com,eastmoney.com,sinajs.cn,sina.com.cn`

## 验证命令
```powershell
$env:NO_PROXY="push2his.eastmoney.com,push2.eastmoney.com,eastmoney.com,sinajs.cn,sina.com.cn"
python -c "from utils.data_provider import MarketDataProvider; print(MarketDataProvider().get_price('600519.SH'))"
```

## 防复发
永不跳过 Wind MCP；代理环境必设 NO_PROXY；TDX 端口 7709 出站须放行。
