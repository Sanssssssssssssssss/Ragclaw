# TASKS

## 待办 Todo
- T-008 形成基线代码与实验代码的分层方案
- T-009 规划多模态 RAG 研究入口
- T-010 规划 GraphRAG / RAGGraph 研究入口
- T-011 建立评测与对比基线
- T-012 选定默认模型供应商并配置本地 API Key
- T-013 验证真实流式聊天回答链路
- T-014 验证“知识检索 + LLM 回答”的完整链路

## 进行中 In Progress
- 暂无

## 已完成 Done
- T-001 初始化项目记忆文件与协作协议落地
- T-002 确认私有仓库创建与上游同步策略（采用新私有仓库 + upstream 合并）
- T-003 拉取并导入参考仓库代码到当前项目
- T-004 记录本地启动与依赖安装流程
- T-005 跑通后端健康检查
- T-006 跑通前端开发环境
- T-007 验证至少一条本地知识检索链路（BM25）

## 阻塞 Blocked
- T-013 真实流式聊天回答链路验证
- T-014 “知识检索 + LLM 回答”的完整链路验证

## 当前阻塞原因
- 当前环境没有可用的 LLM / embedding / web search API Key
- 在无 Key 情况下，流式聊天接口会返回 `Missing API key for provider zhipu`

## 任务说明
- 所有任务完成后需同步更新 `STATE.md`
- 关键结构性变化需同步更新 `DECISIONS.md`
- 新功能进入范围前需能映射到 `REQUIREMENTS.md`
