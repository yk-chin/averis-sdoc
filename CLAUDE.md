# CLAUDE.md — shipdoc 项目约定

## 当前状态（2026-09-19，tag `day1-hardened`，commit 见 git log）

**分数**：v2 数据集 final_score **1.0000**，四轴全满（stage1_macro_f1 / defect_f1 / end_to_end / esc_precision）。
轨迹：0.7660（规则基线）→ 0.8896（LLM 兜底）→ 0.8914（意图判别上报）→ 1.0000（港口 / ON BEHALF OF / PDF 交错三处修法）。

**扰动测试**（`scripts_perturb.py`，`docs/PERTURBATION_REPORT.md`）：P1–P5 六项全部 1.0。
加固前 P4b = 0.68（LOCODE 表太小）、P5 = 0.30（附件只认文件名），已由 R2 / R4 修复。

**架构**：
- 分类：规则优先（`pipeline/classify.py`，只读本邮件正文）→ 置信度 < 0.80 交 Gemini（`pipeline/classify_llm.py`，pydantic 强校验、模型降级链、内容哈希缓存 `.cache/`）。v2 上 rule 232 / llm 288。
- 附件：文件名标记 > 内容指纹 > 都失败 NEEDS_REVIEW（`pipeline/run.py`）。
- 比对：`shipdoc_core/`（纯函数、零 LLM）。港名优先于 LOCODE；公司名先切 `|` 与 `ON BEHALF OF`；LOCODE 表 45 条。
- 上报：缺附件时按意图分流——"compare the SI and draft BL" → `missing_attachment`；"please send the draft BL" → OK；拿不准 → 上报。

**已知未做**（`docs/FINAL_ROUND_RISKS.md`）：R1 Vertex 冒烟（需本机 gcloud 登录）、R3 法人限定词规则、R5 交错伪影泛化、R6 值在下一行。
**成本参数**：`scripts_calibration.py` 顶部的 cost_missed=8 / false_alarm=1 / review=0.35 是假设值，9/21 Workshop 2 向 Averis 求证。

**测试**：`python -m pytest tests/ -q` → 55 passed。

## 唯一的进度指标

每次改完代码都跑：

```
python scripts_eval.py .\data --server http://localhost:8080
```

结果追加到 `evals/history.jsonl`。任何一轴下跌立即回滚。扰动跑分用 `scripts_perturb.py`，**不写** history.jsonl。

## 硬性约束

- **不读 `ground_truth.json`，不读数据生成器**（`sdoc-hackathon-docker/data_v2/*.py`）。只通过 `/submit` 读聚合分数，不做逐邮件探测。
- **`data/`、`submission*.json`、`.env`、`.cache/` 不进 repo**（已在 `.gitignore`）。
- 改 `shipdoc_core/` 必须：纯函数、零 LLM、配单测、先跑 eval 确认不掉分。
- 规则只依据业务语义写；针对某几封邮件的补丁要在注释里如实标明（如 `parse_doc._deinterleave`）。

## 本机环境

- Python：`C:\Users\Admin\AppData\Local\Programs\Python\Python312\python.exe`（不在 PATH）。
- 打分服务：无 Docker，用 uvicorn 直接跑主办方 `server/app.py` 在 8080（数据指向 `sdoc-hackathon-docker/data_v2`）。`/health` 应返回 `emails: 520`。
- LLM：`.env` 里 `LLM_PROVIDER=aistudio`（免费额度，`LLM_MIN_INTERVAL=6.5`），模型链 `gemini-3.6-flash,gemini-3.5-flash,gemini-3.5-flash-lite`；交付切 `vertex`（`GCP_PROJECT` 已填）。
- 用户不熟悉命令行：每一步执行完报告结果；出错贴完整错误，不自行乱改。
