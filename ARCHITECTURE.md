# ARCHITECTURE

## 架构状态
当前为基线导入后的第一版，已结合实际代码结构补充本地运行与检索实现要点。

## 目标架构原则
- 本地优先
- 文件可审计
- Skill-first 检索优先
- 基线稳定与实验扩展分离
- 文档化决策优先于口头约定

## 初步技术方向

### 参考基线
- 后端：Python + FastAPI + SSE
- 前端：Next.js + React + TypeScript
- 检索：Skill-first + 向量检索 + BM25 + 融合
- 编排：LangGraph / Agent 工作流

### 我们的初步分层
- 根目录 `*.md`：长期记忆与项目管理层
- `backend/`：FastAPI API、LangGraph agent、memory、knowledge、skills、tools
- `frontend/`：Next.js 前端工作台
- `docs/`：补充设计与学习材料
- `.vscode/`：本地 demo / 学习用编辑器配置
- 未来预留 `experiments/`：新 RAG 方向实验层

## 模块设计草案

### 1. 项目治理模块
- 负责项目记忆文件维护
- 负责任务、状态、决策管理

### 2. 基线应用模块
- 负责复现参考仓库中的最小可运行系统
- 目标包括前端、后端、知识目录、技能目录与配置文件
- 当前已确认的运行方式：
  - 后端使用 `backend/.venv` + `uvicorn app:app --port 8004`
  - 前端使用 `frontend` 下 `npm run dev`，默认访问 `http://127.0.0.1:3000`

### 3. 检索编排模块
- 保留 Skill-first 主链路
- 在证据不足时触发混合检索兜底
- 后续在此处挂接多模态与图结构扩展
- 当前已确认的实现细节：
  - `knowledge_retrieval/indexer.py` 会同时准备 BM25 与向量索引
  - 没有 embedding key 时，BM25 仍可用，向量索引会关闭
  - 知识问答最终回答仍依赖 LLM，因此“完整知识问答”需要可用 API Key

### 4. 实验扩展模块
- 用于隔离多模态 RAG、GraphRAG / RAGGraph、评测实验
- 原则上不直接污染已验证的基线主链路

## 接口原则
- 优先保留可直接启动与可直接验证的接口
- 外部接口变更需有明确动机和记录
- 新实验模块尽量通过适配层接入，而非直接改坏基线

## 数据与目录原则
- 文档、知识、技能、配置优先以文件落盘
- 生成物和缓存应有明确目录
- 实验数据与正式基线数据尽量隔离

## 当前未决架构问题
- 是否严格保留上游目录结构
- 是否需要在初始化阶段就引入 `experiments/` 分层
- 默认采用哪家模型供应商作为开发与 demo 标配
