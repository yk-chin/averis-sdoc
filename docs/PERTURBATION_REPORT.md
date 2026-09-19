# 扰动测试报告（Perturbation Report）

> 目的：回答「v2 数据集上的 1.0 是泛化还是拟合」。
> 方法：在**不改变语义**的前提下扰动数据集副本，重跑 pipeline，用主办方 `/submit` 打分。
> 脚本：`scripts_perturb.py`（副本在 `.cache/perturb/`，原始 `data/` 只读，不读 ground_truth，不写 `history.jsonl`）。
> 日期：2026-09-19 · 代码：加固前 `65d498b` → 加固后 `88d7eed`（tag `day1-hardened`）

## 扰动项

| 项 | 扰动内容 | 为什么算"语义不变" |
|---|---|---|
| P1 | 字段标签换同义写法，SI/BL 各用不同别名（Port of Loading → Load Port → POL …），每封轮换 | 同一字段的行业常用标签 |
| P2 | 公司后缀写法：SI 用变体 A（`Co Ltd` / `Pte Ltd` / `Sdn. Bhd.` / `L.L.C.`），BL 用变体 B（`Company Limited` / `Pte. Limited` / `Sendirian Berhad`） | 同一法人 |
| P3 | 重量单位：SI 换算成 MT（2 位小数），BL 换算成 LBS（1 位小数） | 数值等价，误差 < 0.1% 容差 |
| P4a | 港口：SI 去掉 UN/LOCODE 只留名字，BL 不动 | 名字即港口 |
| P4b | 港口：BL 只留 UN/LOCODE（如 `KEMBA`）——仅对名字与代码在真实 LOCODE 表里一致的值 | 代码即港口；主办方埋的"改名留码"缺陷值不动，避免抹掉缺陷 |
| P5 | 附件文件名去掉 `_SI` / `_BL` 标记（`email_001_SI.txt` → `email_001_doc_a.txt`），inbox 引用同步改 | 文件内容不变 |

覆盖率：P1–P4 修改 txt / xlsx / docx 附件（222/250），**PDF 附件（28 个）未扰动**；P4 只影响 txt，因为 xlsx/docx 里的港口值本来不带代码。P5 覆盖全部 250 个附件。

## 结果：加固前 vs 加固后

| 扰动 | 加固前 final | 加固后 final | 加固前明细 | 加固后明细 |
|---|---|---|---|---|
| baseline（未扰动） | 1.0000 | 1.0000 | 四轴 1.0 | 四轴 1.0 |
| P1 字段标签同义轮换 | 1.0000 | 1.0000 | — | — |
| P2 公司后缀写法 | 1.0000 | 1.0000 | — | — |
| P3 重量单位 MT / LBS | 1.0000 | 1.0000 | — | — |
| P4a 港口 SI 只留名字 | 1.0000 | 1.0000 | — | — |
| **P4b 港口 BL 只留 LOCODE** | **0.6805** | **1.0000** | defect_f1 0.653 · end_to_end 0.500 | 四轴 1.0 |
| **P5 附件名去掉 `_SI`/`_BL`** | **0.3000** | **1.0000** | defect_f1 0.000 · end_to_end 0.000 · esc_P 0.155 | 四轴 1.0 |

（四轴 = stage1_macro_f1 / defect_f1 / end_to_end / esc_precision）

## 两个被实锤的脆弱点与修法

**P5 → R2 附件识别只看文件名。** 文件名一变，`has_si / has_bl` 全 False，109 封比对一封都没做，end_to_end 归零、129 封被错误上报。
修法（`pipeline/run.py` `classify_attachments()`）：文件名标记 > 内容指纹 `detect_doc_type()` > 都失败留 unassigned，槽位缺失时上报 NEEDS_REVIEW（读不出 → `unreadable`，读得出但非 SI/BL → `wrong_doc_type`）。

**P4b → R4 UN/LOCODE 表只有 22 条。** BL 写纯代码 `KEMBA` 时，不在表里的代码被当成港名，与 SI 的 `MOMBASA` 不等 → 误报；46 封缺陷邮件里 23 封字段集因此不精确。
修法（`shipdoc_core/normalize.py` `_LOCODE_NAME`）：+23 条真实 UN/LOCODE。**只补确认为真实行业数据的条目，不从数据集反推**——数据集里 `AUFRE→BUSAN`、`KEMBA→TUTICORIN` 这类映射本身就是主办方埋的缺陷；`IDBUA`（Buatan）无法确认为官方代码，未收录。

两处修法各配回归测试（共 55 个测试全绿），正常 eval 四轴保持 1.0。

## 结论

- 字段别名解析、公司名归一、重量单位换算、港名匹配在扰动下**全部站住**——这部分是真泛化。
- 附件命名与 LOCODE 表是**结构性假设**，v2 数据从未触发，只有扰动测试能暴露；现已修复并由 P5 / P4b 守住。
- 未覆盖：PDF 附件内容扰动（28 个）；标签值换行布局（R6）；PDF 字符交错伪影的其它标签（R5）。见 `docs/FINAL_ROUND_RISKS.md`。

---

### For slides (English summary)

**Perturbation testing.** To check whether our 1.0 on the v2 set reflects generalisation rather than fitting, we re-scored the pipeline on six semantics-preserving perturbations of a copy of the dataset (original data untouched, ground truth never read): label synonyms rotated per document (P1), company-suffix spelling varied differently on SI vs BL (P2), weights converted to MT on SI and LBS on BL (P3), UN/LOCODE stripped from SI (P4a), BL reduced to LOCODE only (P4b), and attachment filenames stripped of their `_SI`/`_BL` tags (P5). P1–P4a held at 1.000. P4b fell to 0.68 and P5 to 0.30, exposing two structural assumptions the normal evaluation could never trigger: a 22-entry LOCODE table and filename-based attachment routing. After adding 23 verified UN/LOCODEs and a content-fingerprint fallback for attachments (55 unit tests, normal eval unchanged), all six perturbations score 1.000.
