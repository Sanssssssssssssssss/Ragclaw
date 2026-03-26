# LOCAL_DEV

## 目标
本文件记录当前仓库在本地用于 demo 和学习的最小启动流程。

## 已验证环境
- Windows
- Node.js `v24.14.0`
- npm `11.9.0`
- Python Launcher `py`
- Python `3.13`

## 一次性安装

### 后端
```powershell
cd backend
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 前端
```powershell
cd frontend
npm install
```

## VS Code
- 已提供 `.vscode/settings.json`
- 已提供 `.vscode/tasks.json`
- 已提供 `.vscode/launch.json`
- 已提供 `.vscode/extensions.json`

推荐使用：
- `App: Install all`
- `Backend: Run dev server`
- `Frontend: Run dev server`
- `Demo: Backend + Frontend`

## 一键启动
在仓库根目录执行：

```powershell
.\start-dev.ps1
```

或者：

```powershell
start-dev.cmd
```

如果依赖还没装完，可以执行：

```powershell
.\start-dev.ps1 -InstallIfMissing
```

脚本行为：
- 如果前后端已经在本项目端口上运行，会直接复用，不重复启动
- 启动后会打印前端地址
- 当前端可访问时，会自动打开默认浏览器跳转到前端页面

如果你怀疑前端或后端卡在旧状态里，可以强制重启：

```powershell
.\start-dev.ps1 -Restart
```

## 启动方式

### 启动后端
```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8004 --reload
```

### 启动前端
```powershell
cd frontend
npm run dev
```

## 访问地址
- 前端：http://127.0.0.1:3000
- 后端健康检查：http://127.0.0.1:8004/health
- 知识索引状态：http://127.0.0.1:8004/api/knowledge/index/status

## 当前已验证项
- 后端可启动，`/health` 返回 `{"status":"ok"}`
- 知识索引可建立，当前状态为 BM25 可用、向量索引未启用
- 前端首页可访问，HTTP 状态码为 `200`
- BM25 检索可从本地知识库召回内容

## 当前未验证项
- 真实流式聊天回答
- 真实“知识检索 + LLM 回答”闭环
- 向量检索效果

## Kimi 配置
不要写在 `.env.example` 里。  
请写在真实运行文件 [backend/.env](D:/GPT_Project/RAG_Model/backend/.env) 中，最小可用配置如下：

```env
LLM_PROVIDER=kimi
LLM_MODEL=kimi-k2.5
LLM_API_KEY=你的_kimi_api_key
LLM_BASE_URL=https://api.moonshot.ai/v1

EMBEDDING_PROVIDER=
EMBEDDING_MODEL=
EMBEDDING_API_KEY=
EMBEDDING_BASE_URL=
```

说明：
- 这会把聊天模型切到 Moonshot 官方开放平台上的 Kimi 模型。
- 当前项目的向量检索依赖单独的 embedding provider；如果先不填 embedding，BM25 检索仍可用。
- 如果你后面要做完整向量检索，可以再单独补一个 embedding 服务。

## 原因
- 当前机器没有可用的 `OPENAI_API_KEY`、`DEEPSEEK_API_KEY`、`ZHIPU_API_KEY`、`BAILIAN_API_KEY`
- 在无 Key 情况下，流式聊天接口会返回 `Missing API key for provider zhipu`

## 下一步
1. 选定默认模型供应商
2. 在 `backend/.env` 中写入对应 Key
3. 重新验证聊天与知识问答完整链路
