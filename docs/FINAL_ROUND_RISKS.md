# Final 轮风险清单

> 现状：v2 数据集上 final_score 1.0000（分类 / 缺陷 / 端到端 / 上报 四轴全满）。
> 这份清单回答一个问题：**今天每处修法里，哪些依赖了这个数据集的具体形态？换数据后会怎么翻车？**
> 每项标注：假设 → 位置 → 若不成立会怎样（影响哪个指标）→ 可能性 → 建议动作。
> 优先级按「影响 × 可能性 × 修复成本」排。

---

## P0 —— 赛前必须做

### R1. LLM 可用性：整个分类层的单点故障
- **假设**：跑 eval 时 Gemini 至少有一个模型可用。
- **位置**：`pipeline/classify_llm.py`；`run.py` 里 `conf < 0.80` 的 288 封（55%）全靠它。
- **若不成立**：全部退回规则 → macro-F1 从 1.00 掉回 **0.58**（final -0.13）。今天的实测：免费 key 上 `gemini-3.6-flash` / `3.5-flash` 几乎全程 503/429，286/288 封是 `3.5-flash-lite` 扛下来的；中途 Wi-Fi 断了 40 分钟，126 封失败。
- **可能性**：高。免费额度 + 单一 Wi-Fi，final 当天不可控。
- **动作**：
  1. **切 Vertex**（`.env` 已填 `GCP_PROJECT`，改 `LLM_PROVIDER=vertex`，本机 `gcloud auth application-default login`），付费配额、无限速、`LLM_MIN_INTERVAL=0`。**Vertex 路径目前一次都没跑过，必须提前冒烟。**
  2. 保留 AI Studio key 作为第二 provider（当前代码是二选一，若要自动切换需加 ~15 行）。
  3. 拿到 final 数据后**第一件事**就是跑一遍 pipeline 把缓存填满，再做任何调试。

### R2. 附件识别完全依赖文件名 `_SI.` / `_BL.`
- **假设**：SI/BL 附件的文件名含 `_SI.` / `_BL.`（v2 数据 250 个附件 100% 如此）。
- **位置**：`run.py` `classify_attachments()` / `decide()` 的 `has_si` / `has_bl`。
- **若不成立**（`SI_5RSG-19787.pdf`、`draft-bl.docx`、`docs.pdf`）：`has_si=has_bl=False` → 所有比对请求判 `missing_attachment` 或（若正文是索要文件）判 OK → **end_to_end 归零、esc_precision 崩**。这是全链路最脆的一根线。
- **可能性**：中。同一主办方、同一生成器，大概率沿用；但生成器 `edgecases.py` 的存在说明他们会故意变形。
- **动作**：文件名匹配失败时退回**内容指纹**——`parse_doc.detect_doc_type()` 已经能从正文认出 SI/BL，只需在 `classify_attachments()` 里把"按文件名分配"改成"文件名优先、内容兜底"。约 15 行 + 3 条测试。**建议现在就做。**

### R3. 公司名相似度阈值压线
- **假设**：不同法人主体的相似度 ≤ 0.75，同一主体的写法差异 ≥ 0.94。
- **位置**：`shipdoc_core/compare.py` `PARTY_FUZZY_LOW = 0.75` / `HIGH = 0.94`。
- **实测**：`APRIL FINE PAPER TRADING` vs `APRIL FINE PAPER TRADING (MIDDLE EAST) FZE` 相似度 **正好 0.750**，靠 `<=` 判成 MISMATCH（email_145，gold 确认是缺陷）。数据池 27 个主体里这是唯一一对落在灰区边缘的。
- **若不成立**：final 里同一对实体换个写法（多一个逗号）→ 0.76 → UNDETERMINED → `NEEDS_REVIEW/missing_value` → 该邮件 **e2e 漏报 + esc 误报**，一封邮件同时伤两轴。
- **可能性**：高。这两个实体都在生成器的 shipper 池里，final 几乎必然再出现。
- **动作**：加一条确定性规则放在模糊匹配之前——**一方是另一方的前缀，且多出来的部分含法人限定词**（`(MIDDLE EAST)`、`FZE`、`SDN BHD`、`PTE LTD`…）→ 直接 MISMATCH（不同法人）。不改阈值，不影响其它对。约 10 行 + 2 条测试。

---

## P1 —— 建议赛前做，成本低

### R4. 港口代码表只有 22 条
- **假设**：港口值形如 `NAME, COUNTRY (CODE)`，名字总是在。
- **位置**：`normalize.py` `_LOCODE_NAME`；今天改为**港名优先**后，代码只在一侧没有名字时才起作用。
- **实测**：数据里出现 32 个代码，**24 个不在表里**。今天没出事是因为每个值都带名字。
- **若不成立**（一侧只写 `KEMBA`，另一侧写 `MOMBASA, KENYA`）：裸代码不在表里 → 被当成名字 `KEMBA` → 与 `MOMBASA` 不等 → **误报 MISMATCH**。
- **动作**：把数据里出现的 24 个代码按**真实 UN/LOCODE** 补进表（不能从数据反推——`AUFRE→BUSAN`、`KEMBA→TUTICORIN` 这些映射本身就是主办方埋的缺陷）。10 分钟。

### R5. `_deinterleave` 只认一种交错前缀
- **假设**：PDF 字符交错伪影只发生在 `Notify Party/Intermediate Consignee` 这一个标签上。
- **位置**：`pipeline/parse_doc.py` `INTERLEAVED_LABEL = ^Party/Intermediate\s+Cons…`。
- **若不成立**（交错发生在 `Shipper/Exporter` 或 `Consignee`）：值变成乱码 → 模糊相似度 ~0.3 → **误报 MISMATCH**，且是 3 封起跳（生成器每种 edge case 埋 3–5 封）。
- **动作**：泛化判据——值同时含大小写、去掉小写后剩余 ≥ 3 个大写字母、且小写字母按顺序是某个已知标签别名的子串 → 去小写还原。约 15 行 + 测试。中等成本，建议做。

### R6. `Label:` 后值在下一行
- **假设**：`Label: Value` 同行（v2 数据 100%；只有 3 行值为空，都是占位符）。
- **位置**：`parse_doc.py` `LABEL_LINE`；空值 → `BLANK` → `missing_value` 上报。
- **若不成立**（`Shipper:\nABC CO LTD`）：七个字段全被判"缺失" → 整封 `NEEDS_REVIEW/missing_value` → **e2e 漏报 + esc 误报**，而且是系统性的（每封都中）。
- **动作**：值为空且下一行不是标签行时，取下一行为值。约 6 行 + 1 条测试。

### R7. 比对请求的规则分支没有 LLM 复核
- **假设**：正文命中 `COMPARE_PAT`（check/verify/confirm … BL/documents）的邮件一定是 BL_COMPARISON。
- **位置**：`classify.py` 返回 0.80 ≥ 阈值，**是唯一一条不经 LLM 的非附件路径**。
- **若不成立**（发票邮件写 "please confirm the documents for invoice 123"）：误判 BL_COMPARISON，且无附件 → 走意图判别 → 大概率 OK；分类错一封 macro-F1 掉 ~0.003。
- **可能性**：中低。v2 的 75 封发票邮件没有一封命中。
- **动作**：可接受；或把无附件时的置信度降到 0.79 让 LLM 复核，代价 +91 次调用/轮。**Vertex 切换后建议开。**

---

## P2 —— 知道即可，暂不动

### R8. `own_text()` 完全忽略标题
- 规则层不看 subject（正文为空才用）。若 final 的某类邮件正文只有 "See subject"，规则拿不准 → 交 LLM（LLM 看得到标题）。有兜底，可接受。

### R9. 引用邮件截断规则
- `QUOTED_PAT` 在 `From:` / `_____` / `Original Message` 处截断。若正文中间出现 "From: Port Klang"，会截掉后半句。v2 无此情况。可接受。

### R10. 缺附件意图判别的词表
- `DOCS_REQUESTED_PAT` = send/provide/share/forward/resend/issue；`DOCS_IN_HAND_PAT` = attached/enclosed/compare/check/verify/confirm。两个都命中或都不命中 → **默认上报**（安全方向：只伤 esc_precision，不伤 final）。可接受。

### R11. SI_REQUEST 的语义假设
- 95 封 "Please find Shipping instruction for X" 被标为 SI_REQUEST 是从 gold 一致性**推断**的，不是主办方定义。v2 上验证为正确；final 同一生成器，风险低。

### R12. 重量容差 0.1%
- v2 埋的重量缺陷最小 500 kg（差分布：500×3 / 1000×6 / 2000×3），远大于容差。若 final 埋 10 kg 级差异会漏。可能性低——生成器用的是整千整百。

### R13. 港名等价 = 词集包含
- `PORT KLANG WESTPORT ⊇ PORT KLANG` 判同港。理论上 `PORT SAID` vs `PORT` 也会判同——但单独一个 `PORT` 不会出现。可接受。

### R14. 同名不同码判 MISMATCH
- `SINGAPORE (SGSIN)` vs `SINGAPORE (SGSIN)` 没问题；若 final 用了同港的两个合法代码（罕见）会误报。可接受。

### R15. 规则层若失去 LLM 的退化质量
- `INVOICE_PAT` 会被 SI 里的 "3 Original invoice" 命中，这是 R1 退化到 0.58 的主因。若想让"无 LLM"时也有 0.8+，需在正则里排除 "Documents Required" 段。属于 R1 的降级预案，Vertex 稳定则不必做。

### R16. 时间预算
- `LLM_MIN_INTERVAL=6.5` 下 520 封 ≈ 31 分钟；断网重跑靠缓存续上。Vertex 后归零。

### R17. 扫描件 / 图片 PDF
- 无 OCR。图片 PDF → `office_to_text` 空 → `unreadable` 上报（安全方向）。v2 的 5 封 unreadable 全部抓到。若 final 大量用扫描件，e2e 会掉但不会误报。

---

## 建议的执行顺序

| 顺序 | 项 | 预计 | 需要 |
|---|---|---|---|
| 1 | R1 Vertex 冒烟 | 10 min | 本机 `gcloud` 登录（你操作） |
| 2 | R2 附件内容指纹兜底 | 20 min | — |
| 3 | R3 法人限定词规则 | 15 min | — |
| 4 | R4 补港口代码表 | 10 min | — |
| 5 | R6 值在下一行 | 10 min | — |
| 6 | R5 交错泛化 | 20 min | — |
| 7 | 全量回归：51 测试 + eval 仍为 1.0 | 5 min | — |

每一步改完都跑 `python scripts_eval.py .\data --server http://localhost:8080`，任何一轴下跌立即回滚。
