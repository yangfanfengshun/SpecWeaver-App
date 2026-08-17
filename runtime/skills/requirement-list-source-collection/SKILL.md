---
name: spec-requirement-list-source-collection
description: 从 Tower 成员任务清单读取未完成、已完成或全部任务的最小集字段（标题、链接、分组、项目、截止日期、标签），不读正文评论、不写缓存、不升级为完整收集。用户提供成员清单链接、说「我有哪些任务」「列出未完成」「今天的 Tower 任务」时使用。单条任务链接改走 requirement_read_todo。不调用设计稿或 API 文档。
---

# 成员任务清单采集

## 输入与边界

- 接收 Tower 成员任务清单 URL，路径形如
  `/members/{guid}/todos/uncompleted/`、`/completed/` 或 `/all/`。
- 按 URL 路径真正去拉对应清单，不要改写成未完成。
- 只返回清单最小集；不读取任务正文、评论、附件，不写入缓存，不调用
  `requirement_collect`、设计稿或 API 文档能力。
- 单条任务链接不要用本 Skill，改调 `requirement_read_todo`。
- 向总控返回结果时遵守
  [来源结果协议](~/.specweaver/skills/requirement-collection/references/source-result-contract.md)。

## 读取流程

1. 验证输入是成员任务清单链接；单条任务链接立即停止并提示改用
   `requirement_read_todo`。
2. 调用 `requirement_list_member_todos(url)`。
3. 工具按 URL 中的 `uncompleted` / `completed` / `all` 拉对应页面；`all`
   时条目 `status` 为「未提供」，并在 `unresolved` 说明清单页不细分完成状态。
4. 结果只保留 `todo_id`、`title`、`url`、`status`、`task_type`、`tags`、
   `group`、`project`、`due_date`。标题超过 200 字会被截断。
5. 只以明确 `Bug` Tag 返回 `task_type: bug`；其他情况返回
   `task_type: requirement`，不得按标题或分组猜。
6. 空清单返回 `partial`，把「未解析到任务卡片」写入 `unresolved`，不要当成成功。

## 结果要求

- `items` 只返回最小集字段，不把任务正文复制进对话。
- `artifacts` 为空：本工具不落盘。
- `provenance` 保留清单 URL 与成员 GUID。
- `unresolved` 记录空清单、`all` 状态不细分、延迟加载展开次数；缺截止日期就留空字符串，不编造。
- 认证失效时返回 `auth_expired` 或工具给出的过期提示，引导用户打开 SpecWeaver
  设置页重新配置 Tower；区分 `forbidden`、`network_error` 和 `not_found`，不盲目重试。
- 不输出 Cookie、邮箱、密码、登录响应或其他敏感信息。

独立使用时只返回任务清单摘要，不升级为完整资料收集，也不转入需求分析。
