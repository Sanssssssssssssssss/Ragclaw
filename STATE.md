# STATE

## 当前状态
- 日期：2026-03-26
- 私有远程仓库已创建：`Sanssssssssssssssss/RAG_Model`
- 当前仓库已将上游 `Skill-First-Hybrid-RAG` 作为 `upstream` 合并到 `main`
- 后端虚拟环境与前端依赖已安装完成
- VS Code 本地开发配置已补齐
- 后端健康检查通过
- 知识索引状态验证通过：`indexed_files=11`，`bm25_ready=true`，`vector_ready=false`
- 前端开发服务器可启动，首页可访问
- 根目录一键启动脚本已补齐
- 启动脚本会在端口已被本项目占用时自动复用现有前后端进程
- 启动脚本会打印前端地址，并在前端可访问时自动打开默认浏览器
- 若前端或后端卡在旧状态，可用 `.\start-dev.ps1 -Restart` 强制重启
- Kimi 已作为可选默认聊天模型接入配置层
- 本地运行配置文件 `backend/.env` 已初始化，可直接填写 Kimi Key
- 前端主要阅读区域字号已放大，更适合 demo
- 已新增 `backend/scripts/verify_kimi_connection.py` 用于直连验证
- 当前提供的 Kimi key 会触发 `401 Invalid Authentication`

## 本轮完成后预期状态
- 已完成私有主仓初始化、上游基线导入、本地依赖安装和基础可访问性验证
- 可以进入“补 API Key 并验证完整聊天/知识问答链路”的阶段

## 下一步
1. 更换为 Moonshot 开放平台可用的 Kimi API Key
2. 重新验证流式聊天接口与知识问答完整链路
3. 评估是否修复 `stream=false` 时错误返回为空内容的问题
4. 开始规划实验层与基线层的分层边界

## 风险
- 无 embedding key 时仅能使用 BM25，无法验证向量检索效果
- 当前 Kimi key 返回 `401 Invalid Authentication`，无法完成真实回答链路
- `stream=false` 的聊天接口在出错时返回空内容，可能影响 API 级 demo 排错体验

## 接班说明
- 新一轮开始前先阅读：
  1. `PROJECT_BRIEF.md`
  2. `REQUIREMENTS.md`
  3. `ARCHITECTURE.md`
  4. `DECISIONS.md`
  5. `TASKS.md`
  6. `STATE.md`
- 若聊天内容与文档冲突，以最新确认的文档为准，并在本文件记录冲突处理结果
