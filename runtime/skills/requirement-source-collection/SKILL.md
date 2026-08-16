---
name: spec-requirement-source-collection
description: 从需求任务读取元信息、正文、独立评论、子任务标题、附件索引和外部来源线索，缓存正文与评论图片，确定性生成可读原文、机器元数据和图片目录并返回小型摘要。仅在用户明确要求只读取或核对任务原文、总控流程内部预读，或旧任务明确点名本 Skill 时使用。普通需求入口由 spec-requirement-collection 处理。不选择模式、不读取其他平台、不生成需求分析文档。
---

# 需求任务来源采集

## 输入与边界

- 接收需求任务 URL（当前支持 Tower）。
- 只负责任务原始事实格式化和用户缓存；不决定快速/完整模式，不调用设计稿或 API 文档能力。
- 不在用户项目写文件，不生成 `requirement.md`，不发布任务评论。
- 向总控返回结果时遵守
  [来源结果协议](~/.specweaver/skills/requirement-collection/references/source-result-contract.md)。

## 读取流程

1. 验证输入是需求任务链接。
2. 调用 `requirement_read_todo(url)`；工具必须同时缓存正文和全部评论中的普通图片。
3. 工具把正文、全部评论、引用、子任务标题、附件索引和外部链接写到
   `~/.specweaver/cache/tower/<任务ID>/tower-raw.md`，把脚本继续编排所需字段写到
   同目录 `tower-metadata.json`，把普通图片写到 `images/`；可读原文不嵌入 Base64
   元数据，视频、压缩包和其他文件不自动下载。
4. 只以明确 `Bug` Tag 返回 `task_type: bug`；其他情况返回
   `task_type: requirement`，不得按标题或分类猜。
5. 工具结果只保留缓存路径、数量、来源摘要、读取状态和未解决事项，不把全文复制进
   MCP 返回。
6. 独立核对内容时由 Agent 读取 `cache_file` 并查看全部 `image_paths`；图片失败时把
   缺失证据写入 `unresolved`，不得静默忽略。

## 结果要求

- `items` 只返回任务 ID、标题、`task_type` 和各类数量。
- `artifacts` 返回用户缓存中的 `tower-raw.md`、`tower-metadata.json` 与成功缓存的图片。
- `provenance` 保留任务 URL、缓存路径和读取完整性。
- `unresolved` 记录缺失字段、附件失败或来源关联不明，不用经验补齐。
- 认证失效时返回 `auth_expired`，提示用户打开 SpecWeaver 设置页重新配置 Tower；区分
  `forbidden`、`network_error` 和 `not_found`，不盲目重试。
- 不输出 Cookie、邮箱、密码、登录响应或其他敏感信息。

独立使用时只返回任务来源摘要和缓存路径，不升级为完整资料收集。
