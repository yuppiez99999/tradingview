***
type: project\_topic
status: active
authoring\_mode: ai\_generated
created: 2026-08-02
updated: 2026-09-09
related:

- cairn/gnn-supply-chain-factor.md
- docs/ROADMAP缁撴瀯瀹℃煡鍥炲簲_鍙戝竷娌荤悊_20260905.md

***

# v8.7 Release Control Board锛圧OADMAP锛?
> **2026-09-05 缁撴瀯閲嶇粍锛圧-1锛屽鏌ユ媿鏉匡級**锛氭湰鏂囦粠"鍘嗗彶璁板綍+璁″垝+鍐崇瓥鏃ュ織"875 琛屽帇缂╀负 **Release Control Board ~300 琛?*銆傚綋鍓嶄簨瀹炲彧鐪嬫湰鏂囦欢椤堕儴 `CURRENT STATE`锛涘叏閮ㄥ巻鍙插彊杩颁笌杩囩▼淇涓嬫矇 `cairn/LOG.md`锛屾湰鏂囦粎鐣欏喅绛栨寚閽堛€?> **鐗堟湰鍙ｅ緞锛堜笁绾垮懡鍚嶏紝R-1 钀藉畾锛?*锛歚software = v8.7`锛?2-31 鍙戝竷锛屾潈濞佹簮 README/CHANGELOG锛夛綔`preset = p9_200w`锛?00 涓囩敓浜х粍鍚堥厤缃紝**鍘?"v9.0 preset" 鍛藉悕搴熸**锛夛綔`roadmap = r9.3`锛堣鍒掓枃妗ｈ凯浠ｅ彿锛夈€備笉瀛樺湪 v8.8锛?v8.8 瀵瑰啿璋冧紭"鎴愭灉宸插綊鍏?v8.7 鍙戝竷娓呭崟锛?8-29 娉ㄨ锛夈€?> **璺嚎鍥剧粨鏋勯搧寰?*锛歚Release 鈫?Stream 鈫?Gate 鈫?Task` 鍥涘眰锛涘瓨閲?Wave/Sprint/缂栧彿淇濈暀涓嶉噸缂栵紝浠呮柊澧炰换鍔＄敤鍥涘眰銆?>
> **鏃х珷鑺傚紩鐢ㄥ吋瀹规槧灏?*锛堝瓨閲忔枃妗ｅ鏈枃鏃х珷鑺傚悕鐨勫紩鐢ㄦ寜姝よ鍙栵紱LOG 鍘嗗彶鏉＄洰涓嶅洖鏀癸級锛?> 搂绛栫暐浼樺寲鎺掓湡鍐崇瓥 / 搂ETF鏈熸潈瀵瑰啿鎺掓湡 / 搂Wave 2 鈫?`搂CURRENT STATE` + `搂CURRENT QUARTER` + `搂ARCHIVED DECISIONS` 鍐崇瓥鐧昏琛?锝?搂绋冲畾瑙傚療鏈熶笌杩愯惀鏀舵暃鍐崇瓥 鈫?`搂CURRENT QUARTER`锛圕hange Budget锛? 鍐崇瓥鐧昏琛?锝?搂v8.7.1 绋冲畾鎬у寮虹増鏈?鈫?`搂2027 PLAN` 锝?搂v9.0 preset 鈫?`preset = p9_200w`锛堝懡鍚嶅凡搴熸锛? `搂CURRENT STATE` 璧勯噾绾?锝?搂浜戠鑷姩寮€鍙戜换鍔℃睜 鈫?`搂CURRENT QUARTER` 鍐呭悓鍚嶈妭锛堜繚鐣欙級锝?搂GitHub 闆嗘垚 Wave 鏀舵暃鍖?鈫?`搂2027 PLAN` GitHub 闆嗘垚 锝?搂Phase 3锛圗TF 褰卞瓙锛夆啋 `搂CURRENT STATE` etf_option_submodel + `cairn/p3-0-gate.md` 锝?搂澶氱瓥鐣ョ粍鍚堜紭鍖?鈫?`cairn/mvsk-higher-moment-optimization.md` 锝?搂Wave 6 鈫?`docs/Wave6_鏀跺熬鎶ュ憡_20261231.md`銆?
---

## CURRENT STATE锛堝崟涓€浜嬪疄婧愶紝鏇存柊浜?2026-09-08锛?
```yaml
release:
  target: v8.7
  software_release: 2026-12-31        # 浠呰蒋浠跺彂甯? 璧勯噾/妯″瀷涓嶅姩 (R-3)
  production_switch_window: 2027-01-02 ~ 01-09   # 200涓囪祫閲?+ S12 + MVSK, 鐙珛 Go/No-Go (R-3 鍘熷洓椤? qlib 09-07 鍋滆窇褰掓。 R-6, 淇 D-1/D-2/D-4)
  performance_targets:                # 瀹炵洏缁╂晥鐩爣 (鐢ㄦ埛鎷嶆澘 2026-09-05, 鍙栦唬"姣忓ぉ绋冲畾鐩堝埄"鍙ｅ緞)
    accounts: "璇佸埜 200 涓?(p9_200w_preset) + 鏈熻揣 100 涓?(瀵瑰啿/濂楀埄杞戒綋, 寮€鎴?鎺ュ叆/楠屾敹鏈帓鏈?鈫?2027)"
    annual_return: "8% ~ 18% (缁勫悎鍙ｅ緞 = (璇佸埜 PnL + 鏈熻揣 PnL) / 鏈熷垵瀹為檯鍒颁綅鎬绘潈鐩? 鐩爣缁撴瀯 300 涓?= 200 + 100; 鏈熸湜鍊奸潪鎵胯)"
    max_drawdown: "鈮?10% (棰勭畻绾? 椋庢帶鍥涘眰 + Kill Switch 纭害鏉?"
    monthly_win_rate: "鈮?9/12 涓湀姝?(75%) 鈥?鏈堟 = 璐瑰悗鍑€鏀剁泭 > 0, 鍚湡璐х(灏变綅鍚?; 鍘熴€屸墺70%/8-9 鏈堛€嶆嫭鍙峰彛寰勪綔搴?(F02/R-6)"
    daily_target: "搴熸 鈥?鏃ュ害绋冲畾鐩堝埄鏁板涓婁笉瀛樺湪 (绛変环骞村寲澶忔櫘 26), 鍏佽浜忔崯鏃? 鍗曟棩鎹熷け鐢遍鎺ч棬绾︽潫"
    acceptance_basis: "鐪熷疄璧勯噾褰卞瓙缁╂晥 (20涓団啋100涓団啋200涓囩伆搴?, 闈炲洖娴? 鏈熻揣绔畾浣嶅鍐?濂楀埄 (IC/IM/IF Beta + 鍩哄樊), 鏉犳潌 鈮? 鍊?
  batch_plan:
    - "12-10 鍔熻兘鍐荤粨"
    - "12-11~12-20 RC-1 鍙獙璇佷笉鏀瑰姛鑳?
    - "12-21 RC-2 鏈€缁堥厤缃攣瀹?
    - "12-22~12-30 Release rehearsal 瀹屾暣妯℃嫙鐢熶骇"
    - "12-31 v8.7 杞欢鍙戝竷 (璧勯噾淇濇寔鏃ч厤缃?"
    - "01-02~01-09 鐢熶骇鍒囨崲绐?(閫愰」瀹炴柦, 姣忛」鐙珛鍥炴粴棰勬)"

critical_gates:
  D11_phase_b_shadow:
    stable_days: "12/7 鉁?(09-07 EOD)"
    samples: "12/20 (09-07 EOD, 姣忎氦鏄撴棩+1)"
    status: ON_TRACK
    pass_expected: 2026-09-17 EOD (20/20)
    reverify: 2026-09-18
    reverify_checklist: docs/d11_reverify_checklist_20260830.md   # R-5: 09-18 鍓嶆墿灞曞畬鏁存€у瓙椤? 浠ｇ爜鍒ゆ嵁淇濇寔鍙屾潯浠朵笉鍙?  gates_trio:
    industrial_grade_check: "11 PASS / 1 WARN (C1, xtquant 鏈=鐗╃悊闃诲) / 0 FAIL"
    assert_data_validity: "11 PASS / 1 FAIL (D1 鍘嬪姏娴嬭瘯閬楃暀, 鐙珛璺熻釜)"
    engineering_debt_gate: "09-08 瀹炴祴 RED 浠呭洜 D11 鏍锋湰鏈弧锛圱6 YELLOW=61>30 涓?09-06 ec99b61b 鐩戞帶鍙ｅ緞鎵╁甯﹀叆銆丄UTO-1 娑堝寲锛?9-17/18 D11 杈炬爣鍚庢秷瑙ｏ級"
  daily_workflow: "2180 琛?鈮?000 鉁?(D7 闂ㄧ, 09-08 瀹炴祴)"
  coverage: "0.833 鈮?.80 鉁?(reports/ci/coverage_baseline.json 鍐荤粨)"

phase_b:                              # 鏉冨▉婧? scripts/phase_b_progressive_enabler.py --check
  B1_USE_DRIFT_DETECTOR: enabled (2026-08-26)
  B2_USE_FEEDBACK_LOOP: enabled (2026-08-27)
  B3_USE_AUTO_RETRAIN: enabled (2026-08-27)   # 闃舵杞ㄥ凡杈炬渶缁堥樁娈?orchestrator (09-01)
  B4_USE_MLOPS_PIPELINE: enabled (2026-09-11)  # warmup 8/8 鍏ㄩ棴鐜?+ 鍙岀鍚敤 (phase_b_enabler/phase_b_health_gate); 棣?EOD 楠岃瘉寰?09-11 EOD
    # B4 鐪熷疄璺緞 = phase_b_b4_shadow_runner.py shadow 7 澶?鈫?璇勪及鍚敤 (闈?enabler --auto/--advance 鐩存帴鎺ㄨ繘)

shadow_lines:
  S12_P3_defensive: running                  # P3.2 姣忔棩 EOD (S12_Shadow_EOD 16:30), NAV 1.0117, 璇勪及璁＄畻 10-13 (缁熶竴璇勪及鍛?
  mvsk_30day: cron_registered                # Shadow30Day_EOD 16:35, 棣栬Е鍙?09-07, 绐楀彛 09-13~10-12 = 30 鑷劧鏃?(瀹為檯浜ゆ槗鏃?~14-15, R-6 鍙ｅ緞), preflight 9/9 缁? 浠?MVSK P5-2 (qlib 宸插仠璺? 閿悕鑷?mvsk_qlib_30day 绠€鍖?R-6)
  gnn_s6_paper: cron_registered              # GNN_S6_Paper_EOD 16:50, 棣栬Е鍙?09-07, 绐楀彛 09-13~10-12
  b4_llm_loop: running                       # b4_shadow_status.json, history/run_count 宸插箓绛夊榻?(09-05 娌荤悊)

erl_evolution_rebalance:
  er_1x: done (08-27, 涓夌己鍙ｄ唬鐮佸叏灏辩华)
  er_2x_flag_dual_sign: pending              # 09-13~09-18 鎵ц (D-3), 鍐荤粨绐楀墠鍞竴绌烘。
  er_3x: frozen_window_observe_only          # 12-31 鍚?Stage 2鈫? (D-3)

etf_option_submodel:                          # 瀹氫綅: S12 绾槻寰￠闄╁钩浠?"璇氬疄涓嬮檺" (D-4/D-5)
  s12_shadow: running (P3.2)
  p3_3_evaluation: script_ready (run_p33_evaluation.py, 璇勪及璁＄畻 10-13; 2026-09-05 澧為噺涓€鑷存€у洓椤?
  p3_3_acceptance: "鏀剁泭姝ｅ悜 / 鍥炴挙<15% / 鎹㈡墜姝ｅ父 / 褰卞瓙骞村寲钀藉洖娴嬫粴鍔?0鏃ュ垎甯冨甫 [P5,P95] (瀹炴祴 -5.12%~24.35%)"
  s12_defense_acceptance: "鏀剁泭姝ｅ悜(鈮?鍚屾湡 CPI 缁熻灞€鍙ｅ緞) / 30鏃ユ粴鍔ㄥ洖鎾?鈮?% (鍥炴祴2.60%) / 涓庝富缁勫悎鏉冪泭灞傜浉鍏虫€?<0.3"   # D-4
  production_path: 骞跺叆 p9_200w 鐏板害 (Sprint3-1/2/3), 鏃犵嫭绔嬭祫閲戠伆搴?
mvsk_p5:
  p5_1_shadow_ready: done (08-24, W7.1.6)
  p5_2_30day: cron_registered (绐楀彛 09-13~10-12)
  p5_3_switch: 鍒ゅ畾浜庣粺涓€璇勪及鍛?(10-13 璁＄畻璧?, 瀹炴柦鍚庣疆鐢熶骇鍒囨崲绐?(R-3 淇 D-1)

qlib_lgb_v2:
  model_ready: done (08-24, W7.1.8)
  shadow_30day: 鍋滆窇褰掓。 (2026-09-07 R-6, 鍙岃璁＄己闄峰彛寰?FAIL; 閲嶅紑鍓嶆彁 = 鍏堜慨鎺ョ嚎閿欓厤, 鍙﹁璇勪及)
  known_gap: "鈿?鍙岃璁＄己闄? 鈶犳帴绾块敊閰?娑堣垂绔?4 鍙?ETF vs 妯″瀷 86 鍙釜鑲? 姣忔棩鍏ㄨ蛋 fallback 闅忔満鏁? 鈶′俊鍙烽潤鎬?predictions 鎴嚦 2026-07-08, 闈炴瘡鏃ユ帹鐞? 鈫?W7.2.9 鐨?螖澶忔櫘瀵规瘮缁熻涓婃棤鎰忎箟, 10-13 璇勪及鍒?FAIL(璁捐缂洪櫡鍙ｅ緞) + D-2 涓嶅垏鎹? 鍐崇瓥鏉愭枡 = docs/qlib_w729_gap_analysis_20260905.md; 09-07 鍋滆窇褰掓。 (R-6)"
```

## RELEASE GATES锛圧-2 閾佸緥锛?026-09-05 鎷嶆澘锛?
**RELEASE GATE 鈮?RESEARCH GATE銆?* MVSK / GNN / ERL / S12 / Alpha Registry / regime risk budget **鍧囦笉寰楁垚涓?v8.7 鍙戝竷鐨勯殣寮忓墠缃潯浠?*锛坬lib 宸蹭簬 R-6 鍋滆窇褰掓。锛屼笉鍐嶅垪鍏ワ級銆傜粺涓€璇勪及鍛紙10-12 鏀跺熬 鈫?10-13 璁＄畻 鈫?10-14~10-16 鍒ゅ畾锛夊彧浜у嚭**鍒囨崲鍐崇瓥鏉愭枡**锛屼笉浜х敓鍙戝竷涔夊姟銆?
**Release Blocker 鐧藉悕鍗曪紙浠呬互涓嬪彲闃诲 12-31锛?*锛?
| # | Blocker | 褰撳墠鐘舵€?|
|---|---------|---------|
| G-1 | D11 鍙屾潯浠?PASS锛坰table鈮? AND samples鈮?0锛宍engineering_debt_gate.py:1094`锛?| ON_TRACK, 09-18 澶嶉獙 |
| G-2 | 闂ㄧ涓変欢濂?0 FAIL锛?2-31 鍓?21 澶╄瀵熺獥鑷?12-10 鍐荤粨璧风畻锛?| **瑙傚療绐?12-10 璧风畻, 绐楀彛鍐?0 FAIL**锛汥1 閬楃暀 FAIL 鍦ㄧ獥鍙ｅ鐙珛璺熻釜, 12-10 鍓嶉棴鐜垨鏄惧紡璞佸厤锛堣眮鍏嶇紪鍙疯鍐崇瓥鐧昏 R-6锛?|
| G-3 | daily_workflow 鈮?000 琛岋紙D7锛?| 鉁?2159 |
| G-4 | 瑕嗙洊鐜?鈮?.80 鍩虹嚎涓嶉€€鍖栵紙D9锛?| 鉁?0.833 |
| G-5 | 12-10 鍔熻兘鍐荤粨鎵ц + Change Budget 閬靛畧 | 寰呮墽琛?|

**Health Score 瀹氫綅閾佸緥**锛欻ealth Score 鏄?**Dashboard 涓嶆槸 Gate**鈥斺€旇瘎鍒嗛珮涓嶈眮鍏嶄换浣曠‖闂ㄧ FAIL锛涚‖闂ㄧ FAIL 鏃朵笉寰椾互"绯荤粺 X 鍒嗙湅璧锋潵鍋ュ悍"涓虹敱鏀捐銆?
## DECISION NEEDED锛坧ending 鍐崇瓥闃熷垪锛屾瘡鏃ユ煡鐪嬭矾寰勫唴锛?
| 浜嬮」 | 鍐崇瓥浜?| 鎴鏃?| 鐜扮姸 / 澶囬€?|
|------|--------|--------|-------------|
| GitHub Actions 浜戠璁¤垂澶辫触锛堟墍鏈?job 鏈惎鍔級 | 鐢ㄦ埛锛圔illing锛?| 09-19 鍐荤粨绐楀墠 | `gh run list` 瀹炴祴 09-08锛欳I/Quality Gate/ocr 鍏ㄩ儴瑙﹀彂鍗冲け璐ワ紝鎶?payments failed or spending limit"锛涙湰鍦伴棬绂佸叏缁夸笉鍙楀奖鍝嶃€傚閫夛細瑙ｉ櫎 billing / 鎴栫櫥璁拌眮鍏嶈浆绾湰鍦?CI 鍙ｅ緞锛堝奖鍝?QC-1.2/1.4 涓?ocr 绾匡級 |
| Wind MCP 鏈嶅姟浣欓涓嶈冻锛坘line 杩斿洖銆屼綑棰濅笉瓒筹紝璇峰厛鍏呭€笺€嶏級 | 鐢ㄦ埛锛堝厖鍊?/ 鏇夸唬婧愮櫥璁帮級 | 灏藉揩锛堝奖鍝?EOD/drift/DSR 鏁版嵁閾捐川閲忥級 | 09-11 瀹炴祴锛涙浛浠ｈ矾寰?= 鑵捐 qfq锛堝凡楠岃瘉锛? 鏂版氮锛堟姈鍔級/ TDX锛圞 绾跨┖锛夈€傚綋鏃?9/10 宸茬敤鑵捐璺緞琛ラ綈 |
| D1 鍘嬪姏娴嬭瘯 FAIL 澶勭疆 | 涓荤嚎 + 鐢ㄦ埛鎷嶆澘 | 12-10锛堣瀵熺獥璧风畻鍓嶏級 | 闂幆淇 鎴?鐧昏璞佸厤锛汫-2 瑙傚療绐楀彛寰勫凡婢勬竻锛圧-6锛?|
| C1 WARN锛坸tquant 鏈=鐗╃悊闃诲锛?| 涓荤嚎 | 12-10锛堝喕缁撳墠锛?| 瑁?xtquant 鎴?鐧昏璞佸厤 + 褰卞搷璇勪及 |
| qlib W7.2.9 鎺ョ嚎淇鍚庨噸寮€璇勪及 | 涓荤嚎 | 2027 Q1锛堥粯璁や笉鎺掞級 | R-6 宸插仠璺戝綊妗ｏ紱閲嶅紑闇€鍏堜慨鎺ョ嚎閿欓厤锛堣璁＄己闄峰彛寰?鈮?妯″瀷璇佷吉锛?|
| ERL Kill 鍋ュ悍搴﹂槇鍊硷紙N 鏃ュ熀绾匡級 | 鐮旂┒渚?| ER-2.x 鍙岀鍓嶏紙09-18锛?| Kill 鍒ゆ嵁涓変欢濂楄ˉ鍏紙R-6锛夛紝闃堝€煎緟瀹?|
| 鐢熶骇鍒囨崲绐?2027 鎵╃獥/婊戠Щ瑙勫垯 | 涓荤嚎 | 2026-12 鍒囨崲绐楁帓鏈熷墠 | 涓夐」鍚岀獥闇€鎵╃獥鎴栧垎鎵规粦绉伙紙2027 瑙勫垝锛?|
| 2027 GitHub Waves 閿欏嘲 + 瀹归噺棰勭畻 | 涓荤嚎 | 2026-12锛圵ave 9-GH/11-B/12-B 鍚屾棩璧锋帓鏈熷啿绐侊級 | F12/F13 寰呮帓鏈熷鏌?|

## NEXT 14 DAYS锛?9-05 ~ 09-18锛岃嚦 D11 澶嶉獙锛?
| 鏃ユ湡 | 浜嬮」 |
|------|------|
| 09-05锛堝叚锛?| 鉁?R-1/R-2/R-3 钀藉湴锛堟湰鏂囦欢閲嶇粍 + 鎷嗘壒娆℃媿鏉匡級锛涒渽 shadow 鍐欐簮娌荤悊鎵规锛圱ier-2 琛ョ櫥璁?+ 绌?date fail-closed + B4 history 骞傜瓑锛?|
| 09-07锛堜竴锛?| 鍙?cron 棣栬Е鍙戦獙璇侊紙Shadow30Day_EOD 16:35 / GNN_S6_Paper_EOD 16:50锛汦OD 鍚?`python scripts/t3_post_market_check.py` **7 椤逛竴閿?*锛氣懁 鍙?cron 浜у嚭 + S6 闈?skeleton = sys.path 淇缁堥獙 / 鈶?C10 fills 鏂伴矞搴?/ 鈶?B4+D11 杩涘害锛涘惈 jsonl 钀界洏涓庡箓绛夋牳瀵癸級 |
| ~09-09锛堜笁锛?| B4 warmup 7/7 鈫?`phase_b_progressive_enabler.py --check` 璇勪及 USE_MLOPS_PIPELINE 鍚敤 |
| 09-11/12锛堜簲/鍏級 | Sprint 1 鏀跺熬鍒ゅ畾鏉愭枡锛欱1+B2 绋冲畾 鈮? 澶?+ daily_workflow 鉁?+ R10 鉁擄紙**涓嶅惈 D11**锛?9-01 鍙ｅ緞棰勪慨姝ｅ凡瀹屾垚锛?9-12 涓哄懆鍏紝鏉愭枡鍙?09-11 浜ゆ槗鏃ュ唴棰勪骇鍑猴級 |
| 09-13~09-18 | ER-2.x Flag 鍙岀锛圖-3锛屽喕缁撶獥鍓嶅敮涓€绌烘。锛屽弻绛惧姩浣?<0.5 浜哄ぉ锛?|
| 09-14锛堜竴锛?| shadow 绐楀彛棣栦氦鏄撴棩锛圡VSK P5-2 / GNN S6 鍙岀嚎锛泀lib W7.2.9 宸?09-07 鍋滆窇褰掓。 R-6锛?0 鑷劧鏃ョ獥鍙ｈ嚦 10-12锛?|
| 09-17锛堝洓锛?| D11 samples 婊?20/20锛圗OD 鍚庡弻鏉′欢杈炬垚锛?|
| 09-18锛堜簲锛?| **D11 澶嶉獙锛堥鏈?PASS锛夆啋 鍙戝竷闂ㄧ D1-D11 鍏ㄧ豢 鈫?瑙ｉ攣 Sprint3-1锛?0 涓囨祴璇曪紝鍐荤粨璞佸厤锛?* |
| 09-19 鍓?| R-4 钀藉湴锛欳hange Budget 鏈烘妫€鏌ュ叆 engineering_debt_gate + Kill Criteria 缁熶竴琛?鈥?鉁?**鎻愬墠瀹屾垚 09-05**锛圖12 妫€鏌?+ 10 鍗曟祴鍏ㄧ豢锛岀獥鍙ｅ寰呮縺娲伙級 |
| 09-18 鍓?| R-5 钀藉湴锛欴11 澶嶉獙娓呭崟鍒锋柊锛堟暟鎹?椋庨櫓/鎵ц/杩愯惀瀹屾暣鎬у瓙椤癸級鈥?鉁?**鎻愬墠瀹屾垚 09-05**锛堝楠岀増娓呭崟 A~E 宸插叆 d11_reverify_checklist锛?|

## CURRENT QUARTER锛?026 Q4锛?9 ~ 12锛?
### 涓夌嚎鏀舵暃鍘熷垯锛堜笉鍙橈級
- **涓荤嚎 A**锛歷8.7 鍙戝竷涓庣ǔ瀹氾紙鏈€楂樹紭鍏堢骇锛夆€斺€擯hase B 闂ㄧ銆丏11銆佺湡瀹炴垚浜ょН绱€丷elease Blockers
- **鏀嚎 B**锛氱爺绌跺寮猴紙shadow/鐮旂┒妯″紡锛夆€斺€擡TF 鏈熸潈瀵瑰啿 P3-P5銆丟NN銆佽嚜鎴戣繘鍖?- **鏀嚎 C**锛氬伐绋嬪寲鏈嶅姟涓荤嚎鈥斺€旀祴璇?CI/鏂囨。璧勪骇锛屾瘡鍛ㄦ敮绾垮悎璁?鈮? 浜哄ぉ

### Stage 鎵ц閿氱偣锛圧-3 淇鍚庯級
- **Stage A 鍐荤粨鍓嶏紙09-03~09-18锛?*锛氬弻 cron 寮€璺戯紙09-07锛孧VSK/GNN 鍙岀嚎锛夆啋 B4 璇勪及锛垀09-09锛夆啋 Sprint 1 鏀跺熬锛?9-12锛夆啋 ER-2.x 鍙岀锛?9-13~09-18锛夆啋 **D11 澶嶉獙锛?9-18锛岄鏈?PASS锛夆啋 瑙ｉ攣 Sprint3-1锛?0 涓囨祴璇曪紝鍐荤粨璞佸厤锛涘垽鎹 docs/sprint3_capital_upgrade_gate_20260905.md锛?*
- **Stage B 鍐荤粨绐楋紙09-19~12-10锛?*锛歴hadow 鍙岀嚎鐓у父锛圡VSK P5-2 / GNN S6锛泀lib 宸插仠璺戝綊妗ｏ級+ 璧勯噾绾跨伆搴︼紙Sprint3-1 鈫?Sprint3-2 100 涓?shadow 30 澶╋紝鍐荤粨璞佸厤锛? MVSK/GNN 鍙骇鍐崇瓥鏉愭枡 + 鈽?**缁熶竴璇勪及鍛紙10-12 绐楀彛鏀跺熬 EOD 鈫?10-13 鏁版嵁钀藉簱涓庤绠?鈫?10-14~10-16 鍒ゅ畾锛?*锛圥3.3 + MVSK 螖澶忔櫘 + GNN S6 瑙傚療 + Sprint 2 鏀跺熬 鈫?涓€浠藉悎骞惰瘎浼版姤鍛婏紝浣滀负 **鐢熶骇鍒囨崲绐楃嫭绔?Go/No-Go 鐨勫叡鍚岃緭鍏?*锛? 12-10 鍔熻兘鍐荤粨
- **Stage C 鍙戝竷绐楋紙12-10~12-31锛?*锛歊C-1锛?2-11~20锛夆啋 RC-2 閰嶇疆閿佸畾锛?2-21锛夆啋 Release rehearsal锛?2-22~30锛夆啋 **12-31 v8.7 杞欢鍙戝竷锛堜粎杞欢锛?*
- **鐢熶骇鍒囨崲绐楋紙01-02~01-09锛?*锛?00 涓囪祫閲戝崌绾э紙Sprint3-3锛? S12 闃插尽灞傚氨浣?/ MVSK P5-3鈥斺€?*鐙珛 Go/No-Go + 鐙珛鍥炴粴棰勬锛岄€愰」瀹炴柦涓嶆崋缁?*锛圧-3 鍘熷洓椤癸紝qlib 鍒囨崲椤瑰凡 09-07 鍋滆窇褰掓。 R-6 鎾ら攢锛?- **Stage D 2027**锛欸4 Alpha Registry + GNN S7 鍏ュ簱 + S12 鍙傛暟浼樺寲 + ERL Stage 2鈫? + G3 椋庨櫓棰勭畻 regime锛?2-15~03-07 shadow 鈫?03 鏈堝惎鐢ㄥ喅绛栵級

### 绛栫暐鍏嚎锛?026-09 ~ 2027-06锛?
| 绾?| 涓婚 | 2026 鍏抽敭鑺傜偣 | 2027 |
|---|------|--------------|------|
| 鏀剁泭绾?| MVSK P5锛坬lib W7.2.9 宸?09-07 鍋滆窇褰掓。 R-6锛?| shadow 30 澶╋紙09-13~10-12锛夆啋 缁熶竴璇勪及鍛紙10-13 璁＄畻锛夆啋 **瀹炴柦鍚庣疆鍒囨崲绐楋紙R-3锛?* | 璇勪及鏈繃 鈫?璇佷吉褰掓。 |
| 闃插尽绾?| S12 绾槻寰￠闄╁钩浠?| P3.2 褰卞瓙 鈫?P3.3 璇勪及锛?0-13 璧风粺涓€璇勪及鍛級鈫?**骞跺叆涓荤粍鍚堢伆搴﹀垏鎹㈢獥** | 鍙傛暟浼樺寲 01~03 鏈?|
| 杩涘寲绾?| ERL 杩涘寲鈫掑啀骞宠　 | ER-2.x 鍙岀 09-13~18锛汦R-3.x 鍐荤粨绐楀彧瑙傚療 | 12-31 鍚?Stage 2鈫? |
| 鐮旂┒绾?| GNN CHAIN_MOM_60D | S6 绾镐氦鏄?30 澶╋紙09-13~10-12锛夛紱鍏ュ簱鍐荤粨 | S7 鍏ュ簱 01 鏈堣捣 |
| 璧勯噾绾?| p9_200w 鐏板害锛堝喕缁撹眮鍏嶏級 | Sprint3-1锛圖11 鍏ㄧ豢鍚?20 涓囷紝鍒ゆ嵁瑙?docs/sprint3_capital_upgrade_gate_20260905.md锛夆啋 Sprint3-2锛?00 涓?shadow 30 澶╋紝**鍚姩閿氱偣 鈮?026-11-09**锛孯-6 淇锛涘己鍒惰褰曟垚浜ゆ粦鐐瑰垎甯冧綔 3-3 杈撳叆锛夆啋 **Sprint3-3 鍒囨崲绐?01 鏈堝垵锛圧-3锛汫o/No-Go 蹇呯瓟 = 瀹归噺/鍐插嚮鎴愭湰澶嶆牳锛孎16锛?* | 鈥?|
| 鍩哄缓绾?| v8.7.1 G3/G4 | Q4 杩愯惀浠堕獙鏀剁疮绉?| G4锛?1 鏈堬級鈫?G3 shadow锛?2-15~03-07锛夆啋 鍚敤鍐崇瓥锛?3 鏈堬級 |

### Q4 Change Budget锛?9-19 ~ 12-10 鍐荤粨绐楋紝R-4 鎴愭枃锛涙満姊版鏌?09-19 鍓嶅叆 engineering_debt_gate锛?
```
鐢熶骇浠ｇ爜:       0 涓柊 feature
feature flag:   0 涓柊澧?enable
鐢熶骇妯″瀷:       0 涓柊澧?鐢熶骇鍥犲瓙:       0 涓柊澧?(GNN S6/S7 楠岃瘉 shadow 缁х画, 鍏ュ簱鍚庣疆 2027)
閰嶇疆鍙樻洿:       浠呭厑璁?risk / bug fix
shadow:         鏃犻檺鍒?渚嬪:           璧勯噾绾跨伆搴?(鍐荤粨璞佸厤) / bug 淇 / 椋庨櫓涓庢€ц兘浼樺寲
```

### Research Kill Criteria锛圧-4 鎴愭枃锛岃Е鍙戝嵆褰掓。涓嶇画鏈燂紱R-6 琛ュ叏闃堝€?鍒ゅ畾浜?璇佹嵁涓変欢濂楋級

| 椤圭洰 | Kill 鏉′欢 | 闃堝€?/ 鍒ゅ畾浜?/ 璇佹嵁 |
|------|----------|----------------------|
| GNN CHAIN_MOM_60D | S6 瑙傚療鏈?CPCV/绋冲畾鎬т笉鏄捐憲 | S6 绐楀彛婊℃湡璇勪及锛?0-13~10-16 鍒ゅ畾锛夛紱CPCV/绋冲畾鎬ф樉钁楁€у垽瀹氫汉 = 鐮旂┒渚у鏌ワ紱璇佹嵁 = GNN_S6_Paper_EOD jsonl + 璇勪及鎶ュ憡 |
| MVSK P5-3 | 螖澶忔櫘 鈮?0 鎴栧紓甯告崲浠?| 螖澶忔櫘 鈮?+0.15 涓旂粺璁℃樉钁楋紙鏈€灏忔晥搴旈噺锛孯-6 寤鸿鍊硷級涓轰笉 Kill 鍓嶆彁锛涘垽瀹氫汉 = 涓荤嚎+鐮旂┒渚э紱璇佹嵁 = Shadow30Day_EOD jsonl + 褰掑洜鎶ュ憡 |
| qlib_lgb_v2 | ~~涓嶄紭浜庡綋鍓嶇敓浜т俊鍙锋簮锛堝師 V9锛墌~ | **宸?Kill 09-07锛圧-6锛?*锛氬弻璁捐缂洪櫡鍙ｅ緞 FAIL 鈫?鍋滆窇褰掓。涓嶇画鏈燂紱璇佹嵁 = docs/qlib_w729_gap_analysis_20260905.md |
| S12 | 鍥炴挙鎴栨垚鏈秴棰勭畻锛圥3.3 楠屾敹锛?| P3.3 纭獙鏀讹細鍥炴挙<15% / 鎹㈡墜姝ｅ父 / 褰卞瓙骞村寲钀藉洖娴嬪垎甯冨甫锛涘垽瀹氫汉 = 涓荤嚎锛涜瘉鎹?= run_p33_evaluation.py 鎶ュ憡 |
| ERL | 鐏板害鏈熺ǔ瀹氭€т笅闄嶏紙鍋ュ悍搴︽寚鏍囷級 | 鍋ュ悍搴﹁繛缁?N 鏃ヤ綆浜庡熀绾匡紙闃堝€煎緟 ER-2.x 鍙岀鏃跺畾锛夛紱鍒ゅ畾浜?= 鐮旂┒渚э紱璇佹嵁 = Health Score 鎶ヨ〃 |
| 鏂板洜瀛?| ICIR < 闃堝€硷紙S1-S7 闂ㄧ锛?| 娌跨敤 S1-S7 闂ㄧ鏃㈡湁闃堝€硷紱鍒ゅ畾浜?= 闂ㄧ鑷姩鍖栵紱璇佹嵁 = 闂ㄧ鎶ュ憡 |
| 鏂版ā鍨?| live degradation > 闃堝€硷紙妯″瀷閫€褰规爣鍑嗭級 | 娌跨敤妯″瀷閫€褰规爣鍑嗭紱鍒ゅ畾浜?= 鐩戞帶鑷姩鍖栵紱璇佹嵁 = 閫€褰圭洃鎺ф姤琛?|

### 寮€鏀鹃棶棰橈紙浠嶇劧寮€鏀剧殑锛?026-09-05 鑷棫鐗?8 鏉″帇缂╋紱宸茶В鍐抽」闅忓搴斾换鍔￠棴鐜Щ闄わ級

1. **鎯呯华鍥犲瓙鏁版嵁婧愯川閲?* 鈥?涓枃璐㈢粡鏂伴椈瑕嗙洊鐜?鏃舵晥鎬т笉瓒筹紝闇€鏇撮珮璐ㄩ噺婧愭垨鏇夸唬鎯呯华鎸囨爣
2. **绛栫暐瀹归噺澶╄姳鏉?* 鈥?褰撳墠瑙勬ā涓嬪閲忓厖瓒筹紝鎵╁ぇ瑙勬ā闇€閲嶆柊璇勪及鍐插嚮鎴愭湰妯″瀷
3. **D1 鍘嬪姏娴嬭瘯閬楃暀** 鈥?assert_data_validity 1 FAIL 鐙珛璺熻釜锛堜笉闃诲鍙戝竷锛涜瀵熺獥 12-10 璧风畻鍓嶉棴鐜垨鏄惧紡璞佸厤锛岃眮鍏嶇櫥璁拌鍐崇瓥鐧昏 R-6锛屼笉鍐嶆寚鍚?G-2 琛屾敞锛?4. **T6 fail-safe 瀹芥崟鑾?61 澶?*锛?9-08 瀹炴祴锛?9-04 鏇炬竻鑷?29锛?9-06 ec99b61b 璺熻釜鍙ｅ緞鎵╁甯﹀叆瀛橀噺鍥炲崌锛岄潪鏂板琛屼负椋庨櫓锛夆€?AUTO-1 瀵瑰彛娑堝寲锛岀洰鏍?12-10 鍐荤粨鍓嶅洖 鈮?0锛堜笉闃诲鍙戝竷锛孴7 204鈮?50 GREEN锛?
> 宸查棴鐜細C++/Rust 閲嶅啓锛?8-26 ROI 璇佷吉鎼佺疆锛? daily_workflow 鎷嗗垎锛?159 琛岃揪鏍囷級/ Phase B 瑙傚療鏈燂紙B1-B3 宸插惎鐢級/ V9 涓婄嚎鏃堕棿琛紙琚祫閲戠嚎鐏板害鍙栦唬锛? CI 缂哄け鑴氭湰锛圧1+R4 瀹屾垚锛夈€?
### 浜戠 NPC 鑷姩寮€鍙戜换鍔℃睜锛坮oadmap-dev crontab 姣忓伐浣滄棩 16:00 鎺ュ崟锛?
> 浠诲姟绾︽潫锛氣憼 绾唬鐮?娴嬭瘯/鏂囨。/閰嶇疆锛涒憽 涓嶈Е璧勯噾瀹夊叏锛涒憿 涓嶄緷璧栧疄鐩樻暟鎹紱鈶?浜戠鍙獙璇併€?*Q4 鍐荤粨鏈燂紙09-19 璧凤級鍙寫 `[绋冲畾鎬` 鏍囩**銆備緥澶栧垽瀹氫笁闂紙R-6 鎴愭枃锛夛細*鏄惁瑙?Change Budget锛熸槸鍚﹀彲鎷嗕负鏈€灏忕嫭绔嬩换鍔★紵浜戠鑳藉惁闂幆楠岃瘉锛? 鈥?涓夐棶浠讳竴涓嶈繃鍗抽€€鍥炪€?
| 缂栧彿 | 鏍囩 | 浠诲姟 | 浜戠楠岃瘉 |
| --- | --- | --- | --- |
| AUTO-1 | [绋冲畾鎬 | R10/T6 瑁稿鎹曡幏绮剧‘鍖栨壒娆?锛堝鐢?`scripts/_r10_refine_bare_excepts.py` 椋庢牸锛?| ruff BLE001 涓嶆柊澧?+ py_compile |
| AUTO-2 | [鍔熻兘] | LLM 鏉冮檺杈圭晫瑙勮寖鏂囨。 + CI 妫€娴嬫€э紙闈為樆鏂級闂ㄧ | ruff 妫€鏌ュ紩鐢ㄥ畬鏁存€?|
| AUTO-3 | [绋冲畾鎬 | `utils/contracts` parse_symbol() 琛?10+ 绾?stdlib 鍗曟祴 | pytest tests/unit/test_contracts_symbols.py |
| AUTO-4 | [绋冲畾鎬 | 鏈娇鐢ㄥ鍏ユ竻鐞嗭紙`cli/` + `scripts/`锛屼笉纰扮敓浜ф牳蹇冿級 | ruff F401 鑼冨洿娓呴浂 |
| AUTO-5 | [绋冲畾鎬 | 绫诲瀷娉ㄨВ娓愯繘琛ュ叏锛堢嫭绔嬫ā鍧楋級 | mypy 鎶ラ敊鏁颁笉澧?|
| AUTO-6 | [鍔熻兘] | cairn 鐭ヨ瘑灞備氦鍙夊紩鐢紙GH+-2 瀹炵幇锛?| 鑴氭湰 --dry-run 鍙繍琛?|
| AUTO-7 | [绋冲畾鎬 | Chaos 娴嬭瘯鎵╁睍 1~2 涓函 stdlib 鏁呴殰娉ㄥ叆鍗曟祴 | pytest tests/chaos/ 鍏ㄧ豢 |
| AUTO-8 | [绋冲畾鎬 | `config/*.yaml` 杞婚噺 schema 鏍￠獙鑴氭湰 | 閫€鍑?0 |
| AUTO-9 | [绋冲畾鎬 | 鍛ㄦ湡鎬ч潤鎬佷綋妫€锛堝彲閲嶅锛夛細BLE001/F401/F811 澧為噺 + py_compile + 瑁?except 瀹¤锛屽畨鍏ㄩ」鐩存帴淇苟寤?PR | ruff 涓嶆柊澧?+ 鎶ュ憡浜у嚭 |

> 鎺ュ崟绾﹀畾锛氭瘡鏃ヨ鏈妭 + `cairn/LOG.md` 鏈€杩?5 鏉?鈫?鎸?1 椤癸紙浼樺厛鏈€鏃ф湭瀹屾垚锛夆啋 鏈€灏忔敼鍔?+ 琛ユ祴璇?+ 璺戦棬绂?鈫?鎺ㄥ垎鏀缓 PR锛堟爣棰?`AUTO-x`锛夈€?
## PRODUCTION INVARIANTS锛圧-4 鎴愭枃锛?026-09-05锛汻-6 琛ラ獙璇佹。锛氣梿鏈哄櫒妫€鏌?/ 鈻插懆鏈熸紨缁?/ 鈼忎汉宸ュ璁★級

| # | 涓嶅彉閲?| 瀹炵幇閿氱偣 | 楠岃瘉锛圧-6锛氣梿鏈哄櫒妫€鏌?/ 鈻插懆鏈熸紨缁?/ 鈼忎汉宸ュ璁★級 |
|---|--------|---------|------|
| I-01 | 浠讳綍 LLM 涓嶅緱鐩存帴浜х敓 execution order锛堜笁鏉?LLM 璺緞鍧囦负寤鸿/鎶ュ憡鎬ц川锛?| `ai_decision/decision_gate.py` 纭鎺ч棬锛涜鑼冩枃妗?= AUTO-2 | 鈼?CI 闂ㄧ + 鍗曟祴 |
| I-02 | 浠讳綍 research module 涓嶅緱琚?production import 鈼?| G5 鍙岄棬绂?GREEN锛?8-11锛夛紱CI 闅旂闂ㄧ = v8.7.1 P2 椤?| 鈼?CI 闅旂闂ㄧ |
| I-03 | 浠讳綍 shadow strategy 涓嶅緱淇敼 production portfolio | `apply_mvsk_shadow_to_mid_layer` portfolio unchanged=True | 鈼?鍗曟祴鏂█ |
| I-04 | 浠讳綍鏁版嵁寮傚父涓嶅緱浜х敓姝ｅ父浜ゆ槗淇″彿锛坉ata_degraded 鈫?fail-closed锛?| `build_plan_executor.get_emergency_protocol` day_capital_multiplier=0.0 | 鈼?fail-closed 鍗曟祴 |
| I-05 | 浠讳綍 NAV 鏃犳硶 reconciliation 鏃朵笉寰楀崌绾ц祫閲?| 鐢熶骇鍒囨崲绐?Go/No-Go 纭潯浠讹紙R-3锛?| 鈻?鍒囨崲绐?checklist 婕旂粌 |
| I-06 | 浠讳綍妯″瀷鍒囨崲蹇呴』鍙?rollback | T18 GradualRolloutOrchestrator 鍥炴粴瑙﹀彂鍣?+ 鍒囨崲绐楃嫭绔嬪洖婊氶妗?| 鈻?鍥炴粴婕旂粌锛坮ehearsal锛?|
| I-07 | 浠讳綍 feature flag 蹇呴』鍙璁?| flag 娉ㄥ唽琛ㄥ敮涓€鏉冨▉ `config/feature_flags.yaml` + enabler 钀界洏 | 鈼?娉ㄥ唽琛ㄦ牎楠?|
| I-08 | 浠讳綍鐢熶骇閰嶇疆蹇呴』鍙仮澶?| T4 澶囦唤閾撅紙D 鐩?17:30 + manifest SHA256锛? 鎭㈠婕旂粌 | 鈻?鎭㈠婕旂粌锛堣繛缁?7 鏃ワ級 |
| I-09 | 浠讳綍浜ゆ槗蹇呴』瀛樺湪鍙拷婧?source鈫抯ignal鈫抎ecision鈫抩rder鈫抐ill 閾?| FillsStore 浜嬪疄婧愶紙08-08锛? T14 椋庢帶瀹¤ JSONL | 鈼?FillsStore 瀹¤鍗曟祴 |
| I-10 | 浠讳綍鍗曠偣鏁版嵁婧愭晠闅滃繀椤昏繘鍏?fail-safe锛圥0-P6 闄嶇骇閾撅級 | `utils/data_provider.py` 浼樺厛绾ч摼 + degradation_audit | 鈻?鏁呴殰娉ㄥ叆婕旂粌锛圕haos锛?|

## 2027 PLAN

### v8.7.1锛圕ore / Hardening 鎷嗗垎锛孯-1 钀藉畾锛?- **v8.7.1 Core**锛?027-01~03锛夛細P0 褰掑洜鐪熷疄鎬?鉁咃紙09-02锛夈€丳0 Chaos 鉁咃紙09-02锛?0 鐢ㄤ緥锛夈€丳1 椋庨櫓棰勭畻 regime 鎵撻€氾紙G3锛?2-15~03-07 shadow 鈫?03 鏈堝惎鐢ㄥ喅绛栵級銆丳1 Alpha Registry 缁熶竴鍖栵紙G4锛?1 鏈堬級
- **v8.7.1 Hardening**锛堜笌 Core 鍚岀獥锛岃祫婧愯浣?Core锛夛細LLM 鏉冮檺杈圭晫瑙勮寖 + CI 闅旂闂ㄧ锛圛-02 鈼嗭級銆佺敓浜х鎴愭湰妯″瀷鏍搁獙锛圥3锛?- **v8.7.2 鎬ц兘**锛堝緟瀹氾級锛欴ecimal 璧勯噾閾炬敼閫犮€佹灦鏋勪紭鍖?- **杩愯惀浠堕獙鏀剁疮绉?*锛圦4 璧凤級锛欻ealth Score 杩炵画 5 鏃?/ 澶囦唤杩炵画 7 鏃?/ 妫€鏌ュ崟 5 鏃?
### GitHub 闆嗘垚锛堝叏閮ㄥ悗缃?2027锛屽喕缁撴湡鍐呴浂寮曞叆锛?- **Wave 9-GH**锛?1-04~04-30锛? Sprint 18 椤癸級锛歚docs/Wave9_GitHub澧炶ˉ闆嗘垚璁″垝_20260825.md`
- **Wave 10-CTX B**锛?5-03~06-28锛夛細`docs/Wave10_缁忛獙涓婁笅鏂囧眰闆嗘垚璁″垝_20260828.md`
- **Wave 11-B/C**锛?1-04~02-14 / 02-15~02-28锛夛細`docs/楂樹环鍊奸」鐩泦鎴愭帓鏈焈Wave11_20260829.md`
- **Wave 12-B**锛?1-04~03-21锛岄绠?~26 浜哄ぉ锛夛細`docs/github_integration_plan_wave12_20260830.md`
- **Wave 13/14/15 瑁佸喅**锛堥浂浠ｇ爜锛夛細`cairn/github-trending-wave13-20260903.md` / `cairn/github-trending-wave14-20260904.md` / `cairn/github-trending-wave15-20260905.md`
- **2027 鍊欓€夋睜**锛堢櫥璁拌瀵熶笉鑷姩杩?Sprint锛夛細TimesFM锛堝ご鍙凤紝03 鏈?POC 3 浜哄ぉ锛? freqtrade / QuantConnect Lean / akquant / hikyuu / FinGPT / QuantDinger / AutoHedge / openai-agents-python / nanobot / ponytail / academic skills / **awesome-mcp-servers锛堜功绛韭?9-05 鏂板锛?* / **chrome-devtools-mcp锛堜綆浼樎?9-05 鏂板锛孉pache-2.0锛屾帓浣嶅湪 DrissionPage 涔嬪悗锛?*銆?*鍑嗗叆鍓嶇疆妫€鏌ヨ〃 = `pushed_at` 璺濅粖 鈮?2 涓湀**锛坺ipline/backtrader/tushare/wtpy/QuantMuse/abu 宸插姖閫€锛?- **鍑嗗叆鍒ゆ嵁澧炶ˉ锛?9-05锛學ave 15 娌夋穩锛?*锛氶櫎 `pushed_at` 澶栵紝**璁稿彲璇佸繀椤讳负瀹芥澗璁稿彲锛圡IT / Apache-2.0 / BSD / ISC锛?*锛汣opyleft锛圙PL/LGPL/AGPL/SSPL锛夐粯璁ゅ姖閫€浣滀负鐢熶骇渚濊禆锛屼粎鍏佽鍙鍙傝€冩垨鐙珛杩涚▼澶栧伐鍏蜂笖涓嶇綉缁滄湇鍔″寲闆嗘垚銆傞渚嬮€傜敤 = khoj锛圓GPL-3.0锛?7k 鏄熸爣浠嶅姖閫€锛?- **09-05 vnpy 瑁佸喅**锛?*鍔濋€€锛屼笉寮曞叆**銆傝惤鍦版寚鍗?搂3.2 瀹氫负 P0 鐨勯棶棰橀檲杩板凡璇佷吉 鈥斺€?pyautogui/pywinauto 鍏ㄤ粨浠呭瓨浜庢敞閲婏紙GUI 鑷姩鍖栦粠鏈帴绾匡級锛岀▼搴忓寲涓嬪崟閫氶亾宸茬敱 QMT/xtquant 灏变綅锛坄utils/execution/broker_factory.py` + `ms_strategy/src/execution/qmt_broker.py` + `remote_qmt_broker.py`锛夈€傜湡瀹炵己鍙?= W7.2.1 T15銆孮MT paper 楠岃瘉鏈畬鎴愩€嶏紙楠岃瘉缂哄彛锛岄潪鑳藉姏缂哄彛锛夈€傝瑙?`docs/vnpy_鎺ュ叆spec_20260905.md`
- **09-04 Wave 14 缁撹**锛?0 椤?0 椤硅繘 2026 绐楀彛锛堝凡鍦ㄧ敤 2 / 宸叉湁鏇夸唬 4 / 2027 鍊欓€?5 / 鍔濋€€ 3 / 鏃犲叧 16锛?- **09-05 Wave 15 缁撹**锛?0 椤?0 椤硅繘 2026 绐楀彛锛堝凡鍦ㄧ敤 1 / 宸叉湁鏇夸唬 3 / 鍔濋€€ 3 / 鍊欓€夋柊澧?2 / 鍊欓€夊鐜?4 / 鏃犲叧 17锛夛紝**杩炵画涓よ疆闆跺紩鍏?*锛涙湰杞疄璐ㄦ柊澧?= khoj 璁稿彲鍔濋€€ + 鍑嗗叆鍒ゆ嵁澧炶ˉ
- **鎻掍欢涓庡伐鍏烽摼杞ㄩ亾锛圥LG锛?9-08 鐧昏锛屽悗鏈熷垎鎵瑰姞鍏ョ郴缁燂級**锛氭潈濞佹槑缁?= `docs/鎻掍欢涓庡伐鍏烽摼鍚庢湡鎺ュ叆鎺掓湡_20260908.md`銆傚垎灞?= L0 VS Code 鎵╁睍锛堢珛鍗筹紝`.vscode/extensions.json`锛? L1 Skills 宸茬敤缁存寔 / L2 MCP 宸茬敤+鍊欓€夊悗缃?/ L3 涓氬姟寮€婧?~10鈥?5 椤硅繘 2027銆侭atch锛?*0** 鏂囨。+extensions锛?026-09-08锛岄浂鐢熶骇浠ｇ爜锛夆啋 **1** duckdb/tushare/宸ョ▼ UX锛堚墺2027-01-04锛岄敊宄板垏鎹㈢獥锛夆啋 **2** 涓枃鑸嗘儏鎯呮劅+閲囬泦鍘婚噸锛?027 Q1鈥換2锛岄粯璁?shadow锛夆啋 **3** 鎸傞潬 Wave 9/11/12 + TimesFM 03 鏈?POC + chrome-devtools-mcp Q2 鈫?**4** 鎺掗櫎琛紙vnpy/AGPL/GUI 璺緞锛夈€傚閲忚璺祫閲戠嚎涓庡彂甯?Gate锛涗换鍔℃爣 `[PLG]`

## ARCHIVED DECISIONS & POINTERS

### 鍐崇瓥鐧昏锛堟椂闂村簭锛岃鎯呰鍚勬寚閽堬級

| 鍐崇瓥 | 缁撹 | 淇 |
|------|------|------|
| 2026-09-02 鍐崇瓥 1-4 | Q4 鍐荤粨鐢熶骇鍐欏叆鐣?shadow / Wave 11-A 鎺ㄨ繜 2027 / 闆朵镜鍏ヤ袱椤规彁鍓?/ 瀹炵洏 200 涓囧彛寰?| 鈥?|
| 2026-09-03 D-1 | MVSK P5-3 鍒囨崲闅?12-31 鍙戝竷瀹炴柦 | **R-3 淇锛?9-05锛夛細鍚庣疆 01-02~01-09 鐢熶骇鍒囨崲绐?* |
| 2026-09-03 D-2 | qlib 淇″彿婧愬垏鎹紙鍘?V9 鈫?qlib_lgb_v2锛夊悓 D-1 鍙ｅ緞 | **R-3 淇锛?9-05锛夛細鍚庣疆鐢熶骇鍒囨崲绐?*锛?*R-6 鎾ら攢锛?9-07锛夛細qlib 鍙岃璁＄己闄峰彛寰?FAIL 鈫?鍋滆窇褰掓。锛屽垏鎹㈤」涓嶅啀瀹炴柦** |
| 2026-09-03 D-3 | ER-2.x 鍙岀鎻愬墠 09-13~18锛汦R-3.x 鍐荤粨绐楀彧瑙傚療 | 鈥?|
| 2026-09-03 D-4 | 鍙栨秷 ETF 鐙珛璧勯噾鐏板害锛孲12 骞跺叆 p9_200w 鐏板害璺嚎 | **R-3 淇锛?9-05锛夛細Sprint3-3 瀹炴柦鍚庣疆鍒囨崲绐?* |
| 2026-09-03 D-5 | ETF 瀛愮粍鍚?= 涓荤粍鍚堜袱灞傜瓥鐣ュ師鍨嬩笌 shadow 杞戒綋锛屼笉璁剧嫭绔嬪疄鐩樿处鎴?| 鈥?|
| 2026-09-05 R-1 | ROADMAP 鈫?Release Control Board锛堟湰鏂囦欢锛?| 鈥?|
| 2026-09-05 R-2 | Release Gate 鈮?Research Gate 閾佸緥 + Blocker 鐧藉悕鍗?| 鈥?|
| 2026-09-05 R-3 | 12-31 鎷嗘壒娆★細杞欢鍙戝竷涓庣敓浜у垏鎹㈣В缁?| 淇 D-1/D-2/D-4 |
| 2026-09-05 R-4 | Invariants 鎴愭枃 + Change Budget + Kill Criteria锛堟満姊版鏌?09-19 鍓嶏級 | 鈥?|
| 2026-09-05 R-5 | D11 澶嶉獙娓呭崟鎵╁睍 + P3.3 纭獙鏀?+ Sprint3 璧勬湰鍗囩骇闂?| 鈥?|
| 2026-09-05 缁╂晥鐩爣鎷嶆澘 | 瀹炵洏鐩爣 = 骞村寲 8~18% + 鍥炴挙 鈮?0% + 鏈堝害鑳滅巼 鈮?0%锛?姣忓ぉ绋冲畾鐩堝埄 1000"鍙ｅ緞搴熸锛堟暟瀛︿笉鍙锛岀瓑浠峰勾鍖栧鏅?26锛夛紱鏈熻揣 100 涓囪处鎴峰畾浣嶅鍐?濂楀埄杞戒綋銆佹潬鏉?鈮? 鍊嶏紱楠屾敹浠ョ湡瀹炶祫閲戠伆搴︾哗鏁堜负鍑?| **R-6 鍙ｅ緞淇锛?9-07锛夛細鏈堟 鈮?/12锛?5%锛屽洜 8/12=66.7%<70% 涓嶈嚜娲斤級锛涘垎姣?= 瀹為檯鍒颁綅鎬绘潈鐩婏紱璺戣耽閫氳儉 = 鈮ュ悓鏈?CPI** |
| 2026-09-07 R-6 | ROADMAP 璇勫淇鎵规锛堝搴?`roadmap浼樺寲鏀硅繘璇勫鎶ュ憡_20260907.md` 18 椤癸級锛氣憼 G-2 瑙傚療绐楀彛寰勬緞娓?+ D1 璞佸厤閫氶亾锛圥0 F01锛夆憽 qlib 鍋滆窇褰掓。銆佹挙閿€鍒囨崲椤癸紙F10锛夆憿 缁╂晥鍙ｅ緞缁熶竴锛團02/F09锛夆懀 Kill Criteria 涓変欢濂楄ˉ鍏紙F06锛夆懁 缁熶竴璇勪及鍛ㄦ棩鍘?+ shadow 30 澶╁彛寰勬爣娉紙F03/F05锛夆懃 DECISION NEEDED 鑺?+ 椤佃剼鐪熷疄璺緞锛團07锛夆懄 NPC 渚嬪涓夐棶 + PR 楠屾敹绾﹀畾锛團18锛夆懅 Invariants 楠岃瘉妗ｏ紙F14锛夆懆 Sprint3-2 鍚姩閿氱偣 鈮?1-09锛團04锛夆懇 V9 鍛藉悕涓€鑷存€э紙F08锛夆應 鏈熻揣璐︽埛鐘舵€侀敭 + 瀵瑰啿绾匡紙F15锛夆懌 frontmatter 杞箟淇濇寔浠撳唴缁熶竴鏍煎紡锛?4 鏂囦欢鍚屾 `\_`锛岄潪瀛や緥锛屼笉淇紱C1 WARN 澶勭疆鍏?DECISION NEEDED锛夆懍 F16 瀹归噺/鍐插嚮鎴愭湰澶嶆牳鍏ヨ祫閲戠嚎 Sprint3-3 蹇呯瓟 + 3-2 婊戠偣鍒嗗竷寮哄埗璁板綍 | 淇 D-2 / 缁╂晥鐩爣鎷嶆澘 |
| 2026-09-08 R-7 | MVSK fail-fast 闃堝€煎彛寰勫鏍歌惤鍦?鈥?`MVSK_DIFF_THRESHOLD` 0.30鈫?.50锛堟不鐞嗏懁瀛愰泦鍙ｅ緞鏀惧ぇ绾?4.25脳锛?9-07 瀹炴祴鍋ュ悍鏍锋湰 0.2423 杈炬棫闃堝€?80.8%锛?.50/4.25鈮?.118 鎭颁负鏃у彛寰勫仴搴峰尯闂翠笂闄愶紱鏃ф敞閲?鍋ュ悍鍊?0.0-0.1"澶辩湡浣滃簾锛夈€傚洓浠跺锛氫唬鐮佹敞閲?+ 娴嬭瘯鍚屾 4 澶?+ 鐭ヨ瘑鏂囨。鏇存娉ㄨ + 澶嶆牳鏉愭枡瀛樻。銆傛晥鏋滐細09-13 璧锋甯稿瓙闆嗗彛寰勫垎姝т笉鍐嶈瑙﹀彂 latch 鏉€绐楋紝鐪熷疄鑳岀锛堚増瀹屽叏缈昏浆 L2鈮?.0锛変粛鍙潬鎷︽埅 | 渚濇嵁 `docs/weight_diff_l2闃堝€煎彛寰勫鏍竉20260908.md` |
| 2026-09-08 R-8 | **鎻掍欢涓庡伐鍏烽摼鍚庢湡鎺ュ叆鎺掓湡**锛氶€傚悎鏈」鐩殑 VS Code 鎵╁睍 / Agent Skills / MCP / 楂樹环鍊煎紑婧愰泦鎴愮撼鍏?Control Board锛?*鍚庢湡鍒嗘壒鍔犲叆绯荤粺**锛堜笉杩?2026 鐢熶骇浠ｇ爜绐楋級銆侺0 绔嬪嵆钀界洏 `.vscode/extensions.json`锛汱3 涓氬姟闆嗘垚 Batch 1鈥? 鑷?2027-01-04 璧蜂笌 Wave 9/11/12 閿欏嘲锛泇npy/AGPL/GUI 璺緞缁存寔鎺掗櫎銆傛槑缁?= `docs/鎻掍欢涓庡伐鍏烽摼鍚庢湡鎺ュ叆鎺掓湡_20260908.md` | 钀藉疄鐢ㄦ埛銆屽姞鍏ユ帓鏈熴€佸悗鏈熷姞鍏ョ郴缁熴€嶏紱鏈嶄粠 Q4 Change Budget 涓?09-02 鍐崇瓥 2 |
| 2026-09-09 R-9 | **鍥藉€?ETF 鏉冮噸鍙ｅ緞鎷嶆澘**锛氬浗鍊?璐у熀绫昏眮鍏嶄釜鍒?15% 涓婇檺 鈥斺€?`config/risk.yaml` `thresholds.max_weight_by_style: {鍥藉€? 0.30}`锛堢‖涓婇檺锛岃疮閫?EOD Guard6 杩濊鍒ゅ畾涓?Guard7 鍑忎粨鍗曪紱缂虹渷绌哄垯琛屼负涓嶅彉锛夛紱`TARGET_ALLOCATION["鍥藉€?]` 0.22鈫?.25 涓?`tools/add_treasury_etf.py` 鐩爣瀵归綈锛屾秷闄ょ涓夊鍙ｅ緞銆傞厤濂椾慨澶嶏細鍚屼竴鏍囩殑鐨?max_weight 鍑忎粨鍗曚笌椋庢牸鍗曚笉鍐嶅彔鍔狅紙SELL 鍙栬偂鏁版渶澶?/ BUY 鍙栨渶灏忥紝鐩爣鍐茬獊鎵?`needs_decision`锛夛紝娑堥櫎 511010 琚袱鍗曞彔鍔犵牳鍒扮害 8.3% 鐨勮秴璋?| 渚濇嵁 `cairn/trendcast-integration-eod-findings-20260909.md` F-2锛涙彁浜?`4aa26607`锛堜笂闄愬彛寰勶級+ `19a04337`锛堣鍗曞悎骞讹級 |

### 涓撻」鏂囨。鎸囬拡锛堝巻鍙叉槑缁嗗敮涓€鍏ュ彛锛?
| 涓婚 | 杞戒綋 |
|------|------|
| v9.3 缁熶竴鍗囩骇璁″垝 / 鎺掓湡浼樺寲 | `docs/鍗囩骇璺嚎浼樺寲涓庢帓鏈焈20260829.md`锛? 椤硅矾绾夸慨姝?+ 12-10 鍐荤粨绐楄捣婧愶級 |
| D11 澶嶉獙娓呭崟 | `docs/d11_reverify_checklist_20260830.md`锛?9-18 鍓嶆寜 R-5 鍒锋柊锛?|
| 绛栫暐浼樺寲鎺掓湡锛圖-1~D-5 瀹¤璁板綍锛?| `docs/绛栫暐浼樺寲鎺掓湡璁″垝_20260903.md` |
| Production Edition 200 涓囨灦鏋勬柟妗?| `docs/v8.7_Production_Edition_鏋舵瀯鍗囩骇鏂规_200涓囧疄鐩樼増_20260902.md`锛圱1-T5 鉁?09-02锛?|
| 鎺掓湡瀹℃煡鍥炲簲 / 鏋舵瀯瀹℃煡鍥炲簲 | `docs/鎺掓湡瀹℃煡鍥炲簲_杩愯惀鏀舵暃_20260902.md` / `docs/鏋舵瀯瀹℃煡鍥炲簲_v8.7_20260902.md` |
| **ROADMAP 缁撴瀯瀹℃煡鍥炲簲锛圧-1~R-5 渚濇嵁锛?* | `docs/ROADMAP缁撴瀯瀹℃煡鍥炲簲_鍙戝竷娌荤悊_20260905.md` |
| Wave 6/7/8 鏄庣粏锛堝惈 v8.7 鍙戝竷楠屾敹娓呭崟锛?| `docs/楂樹环鍊奸」鐩泦鎴愭帓鏈熻鍒抇20260811.md` 搂7 + `docs/Wave6_鏀跺熬鎶ュ憡_20261231.md` + `cairn/v87-release.md` |
| Wave 7-ERL | `cairn/evolution-rebalance-loop.md` 搂鍗佷笁 |
| Wave 8-LIT锛堚渽 鍏ㄩ儴鎻愬墠 08-24锛?| `docs/绯荤粺鍗囩骇鏂囩尞璋冪爺涓庢帓鏈焈20260823.md` |
| ETF 鏈熸潈瀵瑰啿瀛愭ā鍨?P1-P5 | 鏈枃浠?搂etf_option_submodel + `cairn/etf-option-hedge-model.md` |
| MVSK P1-P5 / shadow 30 澶?| `cairn/mvsk-higher-moment-optimization.md` + `cairn/shadow-30day-validation.md` |
| GNN Wave 5 | `cairn/gnn-supply-chain-factor.md` + `cairn/gnn-supply-chain-factor-wave5-review.md` |
| 鑷垜杩涘寲妗嗘灦 | `docs/鑷垜杩涘寲妗嗘灦/`锛團INENG_ACCEPTANCE_REPORT.md 绛夛級 |
| 褰卞瓙璐︽埛 P3.0 闂ㄧ | `cairn/p3-0-gate.md` |
| 瀹炵洏鍓嶅伐浣滄竻鍗曪紙Sprint3 璧勬湰鍗囩骇闂ㄥ熀纭€锛?| `docs/瀹炵洏鍓嶅伐浣滄竻鍗曚笌鎺ㄨ繘璁″垝_20260824.md` |
| **Sprint3 璧勬湰鍗囩骇闂?checklist锛圧-5锛?* | `docs/sprint3_capital_upgrade_gate_20260905.md` |
| **ER-2.x 鍙岀鎿嶄綔 checklist锛圖-3锛?* | `docs/er2x_dual_sign_checklist_20260905.md` |
| **qlib W7.2.9 缂哄彛鍐崇瓥鏉愭枡锛?9-07 鍋滆窇褰掓。渚濇嵁锛孯-6锛涘弻璁捐缂洪櫡鍙ｅ緞 FAIL锛?* | `docs/qlib_w729_gap_analysis_20260905.md` |
| **vnpy 鍑嗗叆瑁佸喅锛堝姖閫€ + 淇 08-09 鎸囧崡 搂3.2锛?* | `docs/vnpy_鎺ュ叆spec_20260905.md` |
| **Wave 15 鍛ㄦ瑁佸喅锛坘hoj 璁稿彲鍔濋€€ + 鍒ゆ嵁澧炶ˉ锛?* | `docs/GitHub鍛ㄧ儹闂ㄩ」鐩泦鎴恄Wave15_20260905.md` + `cairn/github-trending-wave15-20260905.md` |
| **鎻掍欢涓庡伐鍏烽摼鍚庢湡鎺ュ叆锛圧-8锛孡0鈥揕3 / Batch 0鈥?锛?* | `docs/鎻掍欢涓庡伐鍏烽摼鍚庢湡鎺ュ叆鎺掓湡_20260908.md` + `.vscode/extensions.json` |
| 浠ｇ爜璐ㄩ噺鎺掓湡 Wave 7-QC | `docs/浠ｇ爜璐ㄩ噺鎻愬崌鎺掓湡璁″垝_20260821.md` |
| ECC skills 宸ヤ綔璁″垝锛圵7.3.5 ECC 閫夋嫨鎬у畨瑁? 10-13~10-26锛?| `docs/ECC璧嬭兘閲忓寲绯荤粺宸ヤ綔璁″垝_20260821.md` |
| GitHub 涓夐€傞厤鍣ㄥ緟婵€娲伙紙unsloth/switchyard/openviking, 鍚庣疆 2027锛?| `docs/GitHub鍛ㄧ儹闂ㄩ」鐩泦鎴恄20260821.md`锛?026-09-05 娉ㄨ锛?|

### 鍘嗗彶閲岀▼纰戯紙浠呭畬鎴愭€侊紝杩囩▼瑙?LOG锛?
- [x] v8.6.14 鍥犲瓙搴?GTJA191 瀵规爣锛泇8.5 鍏ㄦā鍧楋紙A- 璇勭骇锛夛紱v8.6 涓夌郴缁熺粺涓€锛?8-03锛?- [x] Wave 1-6 鍏ㄩ儴瀹屾垚锛堣嚜鎴戣繘鍖栨敹灏?/ Phase B B1-B3 鍚敤 / 浠ｇ爜璐ㄩ噺浜旇疆 / T09-T18 椋庢帶+瀹炵洏鍥涗欢濂?/ GNN Gate1 PASS Gate2 璇佷吉鍥為€€ / 12+ 鐢熶骇妯″潡 377+ 娴嬭瘯锛?- [x] Wave 7 Sprint 1 涓讳綋锛圥hase B 鍚敤 / daily_workflow 2159 琛?/ R10 娓呴浂 / G7 瑕嗙洊鐜?0.833 / MVSK+qlib shadow 灏辩华 / 鐪熷疄 LightGBM 妯″瀷钀界洏锛?- [x] 鎵ц闂幆鍥涙尝淇锛堟湡鏉冨鍐?/ 鍐嶅钩琛℃挳鍚?/ fills 椹卞姩 PnL / QMT 鎺ョ嚎楠ㄦ灦锛?- [x] Chaos 鍏満鏅紙40 鐢ㄤ緥锛? Health Score 寮曟搸 + 杩愯惀浠跺洓浠跺锛?9-02锛?- [x] ETF P2 璇氬疄楠岃瘉锛圫6 HONEST 5/6 楠屾敹椤癸級+ S12 绾槻寰￠闄╁钩浠凤紙骞村寲 7.48%/鍥炴挙 2.60%锛孌SR 0.50 鏈繃 鈫?璇氬疄涓嬮檺瀹氫綅锛?- [ ] 璇﹁鏈珶椤癸細Wave 5 S6/S7銆乄ave 7 Sprint 2-4銆丟9 FeatureStore銆乄7.2.1 T15 QMT paper銆乷cr 涓夋鍥哄寲銆乿8.7 鍙戝竷锛堝悇鑺傜偣瑙佷笂鏂?Stage 閿氱偣锛?
---

> **姣忔棩鏌ョ湅璺緞**锛欳URRENT STATE 鈫?DECISION NEEDED 鈫?NEXT 14 DAYS 鈫?RELEASE GATES 鈫?CURRENT QUARTER锛堝惈寮€鏀鹃棶棰橈級銆?> **淇敼绾緥**锛氱姸鎬佸彉鏇村彧鏀?`CURRENT STATE` YAML锛堥檮鏃ユ湡锛夛紱鍘嗗彶杩囩▼鍐?`cairn/LOG.md`锛涙湰鏂囦笉鍐嶅绾?鍙ｅ緞淇娉ㄨ"鈥斺€斾慨姝ｇ洿鎺ユ敼 YAML 骞跺湪鍐崇瓥鐧昏琛ㄧ櫥璁般€?
