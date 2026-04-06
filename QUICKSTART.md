# QUICKSTART

这是这个仓库的第一份入口文档。

如果你刚下载 `Ragclaw`，先看这个文件，再决定是否继续看 [LOCAL_DEV.md](/D:/GPT_Project/RAG_Model/LOCAL_DEV.md)。

## 先认文件
- [README.md](/D:/GPT_Project/RAG_Model/README.md)：项目是什么
- [QUICKSTART.md](/D:/GPT_Project/RAG_Model/QUICKSTART.md)：最常用命令和功能入口
- [LOCAL_DEV.md](/D:/GPT_Project/RAG_Model/LOCAL_DEV.md)：更细的本地开发说明
- [backend/scripts/dev](/D:/GPT_Project/RAG_Model/backend/scripts/dev)：所有 PowerShell/CMD 启动与验证脚本
- [backend/benchmarks](/D:/GPT_Project/RAG_Model/backend/benchmarks)：benchmark runner 和 case files
- [src/backend](/D:/GPT_Project/RAG_Model/src/backend)：后端主代码
- [src/frontend](/D:/GPT_Project/RAG_Model/src/frontend)：前端主代码

Codex 专用的本地记忆文件在：
- `.codex/memory/`

这个目录是本地目录，不作为公共仓库入口文档的一部分。

## 5 分钟 Quickstart
1. 安装后端依赖
```powershell
cd backend
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

2. 安装前端依赖
```powershell
cd src/frontend
npm install
```

3. 回到仓库根目录，一键启动
```powershell
.\backend\scripts\dev\start-dev.ps1
```

4. 打开页面
- 前端：`http://127.0.0.1:3000`
- 后端：`http://127.0.0.1:8015`
- 健康检查：`http://127.0.0.1:8015/health`

## 最常用 CLI
### 全量启动
```powershell
.\backend\scripts\dev\start-dev.ps1
```

### 全量重启
```powershell
.\backend\scripts\dev\start-dev.ps1 -Restart
```

### 只启动后端
```powershell
.\backend\scripts\dev\start-backend-dev.ps1 -Port 8015
```

### 只启动前端
```powershell
.\backend\scripts\dev\start-frontend-dev.ps1 -ApiBaseUrl http://127.0.0.1:8015/api
```

### CMD 包装入口
```cmd
.\backend\scripts\dev\start-dev.cmd
```

## Benchmark CLI
### 一键 benchmark 包装脚本
```powershell
.\backend\scripts\dev\run-backend-benchmarks.ps1 -Suite full -Port 8015
```

### 跑 RAG retrieval 子集
```powershell
.\backend\scripts\dev\run-backend-benchmarks.ps1 -Module rag -RagSubtype retrieval -SamplePerType 2 -Port 8015
```

### 直接跑 harness contract benchmark
```powershell
backend\.venv\Scripts\python.exe backend\benchmarks\run_harness_benchmark.py --suite contract
```

### 直接跑 harness integration benchmark
```powershell
backend\.venv\Scripts\python.exe backend\benchmarks\run_harness_benchmark.py --suite integration
```

### 跑 hard 小样本 smoke
```powershell
backend\.venv\Scripts\python.exe backend\benchmarks\run_harness_benchmark.py --suite hard --limit 2
```

### 跑全量 live validation
```powershell
backend\.venv\Scripts\python.exe backend\benchmarks\run_harness_live_validation.py
```

## 常用验证 CLI
### 验证主聊天模型连接
```powershell
cd backend
.\.venv\Scripts\python.exe scripts\verify_kimi_connection.py
```

### 验证工具调用模型链路
```powershell
cd backend
.\.venv\Scripts\python.exe scripts\verify_tool_agent_connection.py
```

### 验证向量检索
```powershell
cd backend
.\.venv\Scripts\python.exe scripts\verify_vector_retrieval.py
```

### 验证知识路由
```powershell
cd backend
.\.venv\Scripts\python.exe scripts\verify_knowledge_routing.py
```

### 浏览器级聊天 UI 验证
```powershell
.\backend\scripts\dev\run-chat-ui-verification.ps1
```

## 主要功能入口
### HTTP / API
- `GET /health`：健康检查
- `POST /api/chat`：聊天与 SSE 主入口
- `GET /api/sessions`：列出会话
- `POST /api/sessions`：创建会话
- `PUT /api/sessions/{session_id}`：重命名会话
- `DELETE /api/sessions/{session_id}`：删除会话
- `GET /api/sessions/{session_id}/messages`：读取消息
- `GET /api/sessions/{session_id}/history`：读取完整历史
- `POST /api/sessions/{session_id}/generate-title`：生成标题
- `POST /api/sessions/{session_id}/compress`：压缩历史
- `GET /api/files` / `POST /api/files`：读取或保存可编辑文件
- `GET /api/skills`：列出技能
- `GET /api/config/rag-mode` / `PUT /api/config/rag-mode`
- `GET /api/config/execution-platform` / `PUT /api/config/execution-platform`
- `GET /api/config/skill-retrieval` / `PUT /api/config/skill-retrieval`
- `GET /api/knowledge/index/status`：知识索引状态
- `POST /api/knowledge/index/rebuild`：重建知识索引
- `GET /api/tokens/session/{session_id}`：会话 token 统计
- `POST /api/tokens/files`：文件 token 统计
- `GET /api/tokens/message-usage/{session_id}`：消息 token 用量

### 后端能力层
- `terminal`
- `python_repl`
- `read_file`
- `fetch_url`
- `skill`

这些能力的主实现都在 [src/backend/capabilities](/D:/GPT_Project/RAG_Model/src/backend/capabilities)。

## 现在的目录心智模型
### 主代码
- [src/backend](/D:/GPT_Project/RAG_Model/src/backend)：后端主代码
- [src/frontend](/D:/GPT_Project/RAG_Model/src/frontend)：前端主代码

### 非主代码
- [backend/storage](/D:/GPT_Project/RAG_Model/backend/storage)：运行产物
- [backend/benchmarks](/D:/GPT_Project/RAG_Model/backend/benchmarks)：benchmark 和 live validation
- [backend/tests](/D:/GPT_Project/RAG_Model/backend/tests)：测试
- [backend/scripts](/D:/GPT_Project/RAG_Model/backend/scripts)：脚本

## 如果你迷路了
- 想启动项目：看 `backend/scripts/dev/`
- 想跑 benchmark：看 `backend/benchmarks/` 和 `backend/scripts/dev/run-backend-benchmarks.ps1`
- 想看后端实现：看 `src/backend/`
- 想看前端实现：看 `src/frontend/`
- 想快速上手：回到这份 [QUICKSTART.md](/D:/GPT_Project/RAG_Model/QUICKSTART.md)
