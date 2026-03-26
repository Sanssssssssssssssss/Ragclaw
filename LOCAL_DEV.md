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

## 原因
- 当前机器没有可用的 `OPENAI_API_KEY`、`DEEPSEEK_API_KEY`、`ZHIPU_API_KEY`、`BAILIAN_API_KEY`
- 在无 Key 情况下，流式聊天接口会返回 `Missing API key for provider zhipu`

## 下一步
1. 选定默认模型供应商
2. 在 `backend/.env` 中写入对应 Key
3. 重新验证聊天与知识问答完整链路
