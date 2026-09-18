# shipdoc-core

Averis x Monash Hackathon 2026 — 航运单证核对的**确定性内核**。

零 LLM、零网络、纯函数。同样输入永远得到同样输出，可单测、可审计、可在 Q&A 里逐行解释。

## 为什么要有这一层

多数队伍会把 SI 和 BL 一起丢给 LLM 问"哪里不一样"。那样做不可复现、不可审计、会幻觉出假警报——
而用例明确要求 *identifying the right discrepancies **without creating false alarms***。

本包承担比对的全部职责，LLM 只负责抽取：

```
LLM / Vision  →  抽取字段 + 置信度 + 原文出处
                        ↓  schema 强校验
shipdoc_core  →  别名归一 → 值规范化 → 逐字段比对 → 判定 + 证据
                        ↓
置信度路由    →  高置信自动出报告 / 低置信转人工复核
```

## 模块

| 模块 | 职责 |
|---|---|
| `fields.py` | 七字段本体 + 别名表 + **近义陷阱标签**（Place of Receipt ≠ Port of Loading） |
| `normalize.py` | 公司名 / 港口(含 UN-LOCODE) / 数量 / 重量(含单位换算) 规范化 |
| `compare.py` | 确定性比对器，输出带证据的 `FieldResult` 与 `ComparisonReport` |
| `evaluate.py` | PRF、混淆矩阵、字段级指标、**置信度校准 (ECE)**、**成本敏感阈值优化** |

## 环境准备

```bash
python -m pip install openpyxl python-docx pdfplumber pytest google-genai pydantic
cp .env.example .env              # 填入 GEMINI_API_KEY 等（见 .env.example 注释）
```

主办方数据集**不在本仓库中**（已被 `.gitignore` 排除）。把主办方 bundle 的 `inbox/` 和
`attachments/` 放到 `./data/` 下：

```
data/
  inbox/          520 个 *.json
  attachments/    250 个附件（txt / pdf / xlsx / docx）
```

## 运行

```bash
python pipeline/run.py ./data submission.json
```

## 快速验证

```bash
python -m pytest tests/ -q        # 28 passed
```

```python
from shipdoc_core import compare_documents
rep = compare_documents("EM-001", si_fields, bl_fields)
print(rep.render())
# Email: EM-001
#   [MISMATCH] Container Count: SI: 3 / BL: 4  — 集装箱数量不同：SI 3 / BL 4
```

## 自评方式（黑盒打分）

我们使用**主办方提供的 Docker 打分服务**（`sdoc-hackathon-docker`，`docker compose up`）
的 `POST /submit` 端点做自评：

```bash
python scripts_eval.py ./data --server http://localhost:8080
```

该脚本生成 `submission.json` → POST 到 `/submit` → 拿回分数 → 追加到
`evals/history.jsonl`，并与上一次对比。

**我们仅读取 `/submit` 返回的聚合分数**（`final_score`、`stage1_macro_f1`、
`defect_f1`、`end_to_end`、`esc_precision`），**不读取、不解析、不复制
`ground_truth.json` 的任何内容**。打分服务默认 `REVEAL_GT` 关闭，ground truth 只在
服务端内部使用，不经任何端点返回——这正是主办方设计的黑盒用法，我们看得到分数、
看不到答案，系统必须真正泛化。

## 设计不变量

1. `compare.py` 与 `normalize.py` **禁止** import 任何 LLM / 网络库。
2. 拿不准就 `UNDETERMINED` → 转人工，**绝不猜**。
3. 仅格式差异必须判为 `NORMALIZED_MATCH`，**不得报为 mismatch**。
4. 每个判定都带 `reason` 与规范化前后的值，可追溯。
