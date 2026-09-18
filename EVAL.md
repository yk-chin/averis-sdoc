# eval 闭环 —— Docker 路线（写死的命令）

## 一次性：启动打分服务

> ⚠️ docker 包必须解压在 **Documents 或用户目录下**，不能在 `/tmp` 或
> Docker 无法共享的路径，否则挂载为空、`/health` 会显示 `emails: 0`。

```powershell
cd C:\Users\<你的用户名>\Documents\sdoc-hackathon-docker
docker compose up --build
```

第一次会拉镜像，几分钟。看到服务起来后**保持这个窗口开着**。

## 验证服务活着（另开一个终端）

```powershell
curl http://localhost:8080/health
```

必须看到 `"emails": 520`。如果是 `0`，是挂载路径问题——把整个 docker 文件夹
移到 `C:\Users\<你的用户名>\` 下重来。

## 每次改完代码就跑这一条

```powershell
cd C:\Users\<你的用户名>\Documents\averis
python scripts_eval.py .\data --server http://localhost:8080
```

它会：生成 submission.json → POST 到 /submit → 拿回分数 →
追加到 `evals\history.jsonl` → **和上一次对比，退步的指标标 ⚠️**。

## 为什么走 Docker 而不是 score_cli

`/submit` 只返回 scoreboard，`ground_truth` 永远不经任何端点返回
（`REVEAL_GT` 默认关闭）。这是**黑盒打分**，正是主办方设计的用法——
我们看得到分数，看不到答案，系统被迫做到真正泛化。
Final 轮会换数据，这一点会救你。
