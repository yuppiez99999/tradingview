# LLM 鏉冮檺杈圭晫瑙勮寖锛圓UTO-2 / Production Invariant I-01 鎴愭枃锛?026-09-05锛?
> **涓嶅彉閲?I-01**: 浠讳綍 LLM 涓嶅緱鐩存帴浜х敓 execution order銆?> **閫傜敤鑼冨洿**: 鍏ㄤ粨鎵€鏈?LLM 璋冪敤璺緞锛堢幇鏈変笁鏉?+ 鏈潵鏂板锛夈€?> **閰嶅妫€娴?*: `scripts/check_llm_boundary.py`锛堟娴嬫€?CI 闂ㄧ锛?*闈為樆鏂?*鈥斺€旀姤鍛婅繚渚嬩絾涓?fail build锛涜浆闃绘柇闇€鐢ㄦ埛鎷嶆澘锛夈€?
---

## 涓€銆侀搧寰?
1. **LLM 杈撳嚭姘歌繙鏄?寤鸿/鎶ュ憡/澶嶇洏"鎬ц川**锛屼骇鐢熸墽琛屽姩浣滃繀椤荤粡杩囩‘瀹氭€ч鎺ч棬锛坄ai_decision/decision_gate.py`锛変笌鎵ц缂栨帓灞傦紙`ai_decision/execution_bridge.py`锛夈€?2. **LLM 涓嶅緱鎸佹湁璧勯噾瀵嗛挜銆佷笉寰楃洿鎺ヨ皟鐢?broker/涓嬪崟 API銆佷笉寰楃粫杩?Feature Flag 涓庡弻绛?*銆?3. **LLM 鍐崇瓥蹇呴』鐣欑棔**锛氭墍鏈?LLM 鍙備笌鐨勫喅绛栧啓鍏ュ璁★紙JSONL锛夛紝鍚ā鍨嬪悕銆佽緭鍏ユ憳瑕併€佽緭鍑轰笌鏈€缁堟槸鍚﹂噰绾炽€?4. **鏂板 LLM 璺緞鐨勫噯鍏?*锛氶』鍦ㄦ湰鏂囦欢鐧昏锛埪т笁 璺緞娉ㄥ唽琛級+ 閫氳繃 `check_llm_boundary.py` 妫€娴?+ 浜哄伐纭鍏惰緭鍑虹粓鐐规槸"鎶ュ憡/寤鸿"鑰岄潪鎵ц銆?
## 浜屻€佷笁鏉℃棦鏈?LLM 璺緞锛堝凡鏍搁獙鍚堣锛?
| 璺緞 | 妯″潡 | 杈撳嚭缁堢偣 | 鍚堣鏈哄埗 |
|------|------|---------|---------|
| AI Hedge Fund 20 鍒嗘瀽甯?| `quant_modules/ai_hedge_fund/`锛圠angGraph 缂栨帓锛?| 澶氱┖杈╄缁撹 鈫?signal_fusion 鍔犳潈铻嶅悎锛堜俊鍙峰缓璁級 | 铻嶅悎鏉冮噸纭畾鎬ц绠楋紱LLM 浠呰础鐚俊鍙峰垎閲?|
| GLM-5 鐩樹腑鍐崇瓥 | `utils/glm5_decision_engine.py` + `utils/glm5_client.py` | 鍐崇瓥寤鸿 鈫?**L1 decision_gate 纭鎺ч棬** 鈫?L2 execution_bridge锛坅uto 妯″紡浠嶈 veto/escalation 绾︽潫锛?| PreTradeGuard 鍏鍒?+ L2 椋庢帶 veto锛堟祴璇?`test_execution_bridge` 瑕嗙洊 escalation/veto 閾撅級 |
| LLM 鏅鸿兘杩涘寲锛圛deation锛?| `utils/llm_evolution/strategy_ideation.py`锛圖1-D4锛?| 鍥犲瓙/绛栫暐鍋囪 鈫?D2 鍋囪楠岃瘉锛圛C 鏄捐憲鎬?+ CRO Gate + 璇氬疄涓変欢濂楋級鈫?鍏ュ簱寤鸿 | 鍏ュ簱闇€杩?S1-S7 闂ㄧ锛汯ill Criteria 鑷姩閫€褰?|

**鍏卞悓鐐?*: 涓夋潯璺緞鐨勮緭鍑洪兘缁堟浜?淇″彿/鍋囪/澶嶇洏鎶ュ憡"锛屾墽琛屽姩浣滃潎鐢辩‘瀹氭€т唬鐮侊紙椋庢帶鍏欢濂?T09-T18锛夌嫭绔嬪喅绛栥€?
## 涓夈€佽矾寰勬敞鍐岃〃锛堟柊澧?LLM 璺緞蹇呴』鐧昏锛?
| # | 璺緞鍚?| 鍏ュ彛妯″潡 | 杈撳嚭鎬ц川 | 鐧昏 |
|---|--------|---------|---------|------|
| 1 | ai_hedge_fund | `quant_modules/ai_hedge_fund/orchestrator.py` | 淇″彿寤鸿 | 鏃㈡湁 |
| 2 | glm5_intraday | `utils/glm5_decision_engine.py` | 鍐崇瓥寤鸿锛堢粡 L1 闂級 | 鏃㈡湁 |
| 3 | llm_evolution | `utils/llm_evolution/strategy_ideation.py` | 鍥犲瓙鍋囪 | 鏃㈡湁 |

## 鍥涖€佹娴嬫€ч棬绂侊紙`scripts/check_llm_boundary.py`锛?
- **妫€娴嬮€昏緫锛圓ST 绾э級**: 鎵弿鎵ц/涓嬪崟妯″潡锛坄automated_execution_system.py` / `ai_decision/execution_bridge.py` / `utils/execution/*` / broker 閫傞厤鍣?/ T15 LiveOrderExecutor锛夛紝鑻ュ叾 import 浜?LLM 妯″潡锛坄glm5*` / `ai_hedge_fund` / `llm_evolution` / `llm_gateway`锛夆啋 鎶ュ憡杩濅緥锛堟彁绀?LLM 妯″潡琚墽琛岄摼鐩存帴寮曠敤锛岄渶浜哄伐纭鏄惁缁曡繃 decision_gate"锛夈€?- **鍙嶅悜寮曠敤鍚堟硶**: LLM 妯″潡 import 椋庢帶/鎵ц**鎺ュ彛绫诲瀷**锛堝 RiskContext锛変笉杩濅緥鈥斺€旀娴嬫寜"鎵ц閾炬枃浠?鈫?LLM 妯″潡"鍗曞悜鍒ゅ畾銆?- **閫€鍑虹爜鎭?0**锛堟娴嬫€ч潪闃绘柇锛夛紱CI 闆嗘垚浣嶇疆: `.github/workflows/ci.yml` 澧為噺 job 鍙€夋楠わ紙杞樆鏂』鐢ㄦ埛鎷嶆澘锛夈€?
## 浜斻€佽繚渚嬪缃祦绋?
1. 妫€娴嬫姤鍛婁骇鍑鸿繚渚?鈫?On-Call 浜哄伐鏍稿璇?import 鏄惁浜х敓鎵ц璇箟銆?2. 纭杩濅緥 鈫?褰撴棩绉婚櫎鐩磋繛锛圠LM 杈撳嚭鏀硅蛋 decision_gate 杈撳叆閫氶亾锛? `cairn/LOG.md` 鐧昏銆?3. 璇姤 鈫?鍦ㄦ娴嬭剼鏈殑鍚堟硶鏄犲皠琛ㄤ腑鐧昏璞佸厤鐞嗙敱锛堝惈 LOG 鎸囬拡锛夈€?
---
鍏宠仈: Production Invariant I-01锛圧OADMAP锛夛綔`ai_decision/decision_gate.py`锛堢‖椋庢帶闂級锝渀scripts/check_llm_boundary.py`锛堟娴嬪櫒锛?
