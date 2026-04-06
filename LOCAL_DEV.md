# LOCAL_DEV

## 作用
这份文档记录当前仓库在本地开发、演示、验证时最常用的启动方式。

如果你只是刚下载仓库，先看 [QUICKSTART.md](/D:/GPT_Project/RAG_Model/QUICKSTART.md)。

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

### 前端
```powershell
cd src/frontend
npm install
```

## VS Code
- [settings.json](/D:/GPT_Project/RAG_Model/.vscode/settings.json)
- [tasks.json](/D:/GPT_Project/RAG_Model/.vscode/tasks.json)
- [launch.json](/D:/GPT_Project/RAG_Model/.vscode/launch.json)
- [extensions.json](/D:/GPT_Project/RAG_Model/.vscode/extensions.json)

## 一键启动
在仓库根目录执行：

```powershell
.\backend\scripts\dev\start-dev.ps1
```

或：

```cmd
.\backend\scripts\dev\start-dev.cmd
```

强制重启：

```powershell
.\backend\scripts\dev\start-dev.ps1 -Restart
```

## 默认地址
- 前端：`http://127.0.0.1:3000`
- 后端：`http://127.0.0.1:8015`
- 健康检查：`http://127.0.0.1:8015/health`
- 知识索引状态：`http://127.0.0.1:8015/api/knowledge/index/status`

## 单独启动
### 后端
```powershell
powershell -ExecutionPolicy Bypass -File .\backend\scripts\dev\start-backend-dev.ps1
```

### 前端
```powershell
powershell -ExecutionPolicy Bypass -File .\backend\scripts\dev\start-frontend-dev.ps1
```

## 运行配置
实际运行配置写在 [backend/.env](/D:/GPT_Project/RAG_Model/backend/.env)。

最常见的本地配置入口：
- `backend/.env`
- [src/backend/runtime/config.py](/D:/GPT_Project/RAG_Model/src/backend/runtime/config.py)

## 常用验证
### 后端模型与检索
```powershell
cd backend
.\.venv\Scripts\python.exe scripts\verify_kimi_connection.py
.\.venv\Scripts\python.exe scripts\verify_tool_agent_connection.py
.\.venv\Scripts\python.exe scripts\verify_vector_retrieval.py
.\.venv\Scripts\python.exe scripts\verify_knowledge_routing.py
```

### 前端浏览器级验证
首次安装 Playwright 浏览器：

```powershell
cd src/frontend
npm run playwright:install
```

运行聊天 UI 验证：

```powershell
cd src/frontend
npm run verify:chat-ui
```

让脚本自动拉起前后端再做验证：

```powershell
powershell -ExecutionPolicy Bypass -File .\backend\scripts\dev\run-chat-ui-verification.ps1
```

## 说明
- 当前一键启动和验证脚本都已经集中到 `backend/scripts/dev/`
- 前端主代码在 `src/frontend/`
- 后端主代码在 `src/backend/`
