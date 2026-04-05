# LOCAL_DEV

## 目标
这份文档记录当前仓库在本地用于 demo、学习和浏览器验证的最小启动流程。

## 已验证环境
- Windows
- Node.js `v24.14.0`
- npm `11.9.0`
- Python `3.13`

## 一次性安装

### 后端
```powershell
cd backend
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`requirements.txt` 现在包含 Excel 分析依赖 `openpyxl`；如果你之前已经装过依赖，记得再执行一次安装命令补齐它。

### 前端
```powershell
cd src/frontend
npm install
```

## VS Code
- 已提供 [settings.json](D:/GPT_Project/RAG_Model/.vscode/settings.json)
- 已提供 [tasks.json](D:/GPT_Project/RAG_Model/.vscode/tasks.json)
- 已提供 [launch.json](D:/GPT_Project/RAG_Model/.vscode/launch.json)
- 已提供 [extensions.json](D:/GPT_Project/RAG_Model/.vscode/extensions.json)

## 一键启动
在仓库根目录执行：

```powershell
.\start-dev.ps1
```

或：

```powershell
start-dev.cmd
```

如果你怀疑前后端卡在旧状态里，可以强制重启：

```powershell
.\start-dev.ps1 -Restart
```

## 当前默认地址
- 前端：`http://127.0.0.1:3000`
- 后端：`http://127.0.0.1:8015`
- 健康检查：`http://127.0.0.1:8015/health`
- 知识索引状态：`http://127.0.0.1:8015/api/knowledge/index/status`

## 单独启动

### 后端
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\dev\start-backend-dev.ps1
```

### 前端
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\dev\start-frontend-dev.ps1
```

## Kimi 配置
真实运行配置写在 [backend/.env](D:/GPT_Project/RAG_Model/backend/.env)，不要写在 `.env.example`。

最小配置：

```env
LLM_PROVIDER=kimi
LLM_MODEL=kimi-k2.5
LLM_API_KEY=你的_kimi_api_key
LLM_BASE_URL=https://api.moonshot.cn/v1
LLM_TEMPERATURE=1

TOOL_LLM_PROVIDER=kimi
TOOL_LLM_MODEL=moonshot-v1-8k
TOOL_LLM_TEMPERATURE=0

ROUTER_LLM_PROVIDER=
ROUTER_LLM_MODEL=
ROUTER_LLM_API_KEY=
ROUTER_LLM_BASE_URL=
ROUTER_LLM_TEMPERATURE=

EMBEDDING_PROVIDER=local
EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
```

说明：
- 主回答模型默认是 `kimi-k2.5`
- 工具调用和知识路由默认复用更稳的轻量模型链路
- 向量检索默认走本地 embedding，方便学习和 demo

## 验证脚本

### 后端模型与检索
```powershell
cd backend
.\.venv\Scripts\python.exe scripts\verify_kimi_connection.py
.\.venv\Scripts\python.exe scripts\verify_tool_agent_connection.py
.\.venv\Scripts\python.exe scripts\verify_vector_retrieval.py
.\.venv\Scripts\python.exe scripts\verify_knowledge_routing.py
```

### 浏览器级 UI 验证
Playwright 已作为前端开发依赖安装。

首次安装浏览器：

```powershell
cd src/frontend
npm run playwright:install
```

验证聊天区滚动稳定性、knowledge 回答展示和每轮 token 用量展示：

```powershell
cd src/frontend
npm run verify:chat-ui
```

如果你想让验证脚本自行带起前后端：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\dev\run-chat-ui-verification.ps1
```

## 当前说明
- 当前浏览器验证入口已经落库，可以复用
- 在这台机器上，后端冷启动会先重建索引，所以自动化验证比普通接口调用更慢
- 如果 `run-chat-ui-verification.ps1` 超时，优先先手动把前后端拉起，再运行 `npm run verify:chat-ui`
