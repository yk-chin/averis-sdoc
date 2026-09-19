# 部署：Cloud Run（asia-southeast1）+ Vertex AI

服务：`https://shipdoc-api-705106212012.asia-southeast1.run.app`　项目：`eco-world-296707`

| 组件 | 值 |
|---|---|
| 镜像 | `Dockerfile`，`python:3.12-slim`，非 root（uid 10001），Cloud Build 从源码构建 |
| 运行身份 | 服务账号 `shipdoc-run@eco-world-296707.iam.gserviceaccount.com`：`roles/aiplatform.user` + `roles/secretmanager.secretAccessor` |
| LLM | `LLM_PROVIDER=vertex`，`GCP_LOCATION=global`（新加坡 region 缺部分模型），链 `gemini-3.5-flash-lite;gemini-3.5-flash;gemini-3.6-flash`。**云上没有任何 API key**，认证走服务账号 ADC |
| Secret | `API_TOKEN` ← Secret Manager `shipdoc-api-token:latest`；POST 端点要求 `X-API-Key` |
| 规格 | 1 vCPU / 1 GiB / 并发 20 / 超时 300s / 0–3 实例 |
| 上传范围 | `.gcloudignore`：只有 `api/ pipeline/ shipdoc_core/ Dockerfile requirements.txt`，**不含 data/ .env .cache/** |

## 端点

| | 认证 | 说明 |
|---|---|---|
| `GET /` | 无 | 手机可用的测试页 |
| `GET /health` | 无 | `?deep=1` 真调一次 LLM，验证 Vertex 认证 |
| `POST /process` | X-API-Key | 一封邮件 → `decision`（submission 记录）+ `evidence`（规则/LLM 分类依据、附件解析、7 个字段的 FieldResult、可读报告） |
| `POST /batch` | X-API-Key | `{"emails":[…]}`，≤200 封 |
| `GET /report/{email_id}` | 无 | 最近一次结果；存在实例内存，实例回收后 404 |

请求体：`{"email_id","from","subject","body","attachments":[{"name","content_base64"} 或 {"name","text"}]}`。
附件名含 `_SI.`/`_BL.` 时按名分配，否则按内容指纹。

## 重新部署（改完代码）

```powershell
gcloud run deploy shipdoc-api --source . --region asia-southeast1 --platform managed --allow-unauthenticated `
  --service-account shipdoc-run@eco-world-296707.iam.gserviceaccount.com `
  --set-env-vars "LLM_PROVIDER=vertex,GCP_PROJECT=eco-world-296707,GCP_LOCATION=global,GEMINI_MODEL=gemini-3.5-flash-lite;gemini-3.5-flash;gemini-3.6-flash,LLM_MIN_INTERVAL=0,APP_VERSION=<git sha>" `
  --set-secrets API_TOKEN=shipdoc-api-token:latest `
  --memory 1Gi --cpu 1 --concurrency 20 --timeout 300 --min-instances 0 --max-instances 3
```

换 token：`gcloud secrets versions add shipdoc-api-token --data-file=<文件>`，再重新部署（或 `gcloud run services update shipdoc-api --update-secrets API_TOKEN=shipdoc-api-token:latest`）。

## 本地跑同一份代码

`.env` 里 `LLM_PROVIDER=aistudio` + `GEMINI_API_KEY`；`python -m uvicorn api.main:app --port 8090`。不设 `API_TOKEN` 则 POST 端点公开。

## 已知限制

- `/report` 只在实例内存里；多实例或缩容到 0 后失效。持久化需要 Firestore/GCS，未做。
- 无 OCR：扫描件 PDF → `unreadable` 上报。
- LLM 缓存 `.cache/` 在容器内，实例回收即清空（每次冷启动重新计费）。
