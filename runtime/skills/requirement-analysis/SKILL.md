---
name: spec-requirement-analysis
description: 分析已经由 SpecWeaver 收集并验证的需求任务、设计稿和 API 文档来源，综合业务规则、设计事实、API 契约、冲突与缺失并生成 requirement.md。spec-requirement-collection 完整收集成功后自动转入，或用户明确要求分析需求、生成需求文档、重新分析已有来源目录时使用。不重新收集来源、不修改来源文件、不分析代码、不执行开发。
---

# 需求分析

## 边界

- 只分析收集清单明确采用的来源；项目保存人类/开发可用文件，用户缓存保存机器清单
  与规范化设计结构。
- 不调用需求、设计稿、API 文档 MCP 或 `requirement_collect` 重新收集；只允许调用
  `requirement_get_manifest` 定位已有清单。
- 不修改 `requirement-raw.md`、缓存清单、附件、设计结构或 API 原始文件。
- 只生成或更新 `requirement.md`，不生成 `api.md`、`design-context.json` 或开发计划。
- 不把推断写成来源事实；不确定、冲突和缺失必须单列。
- 分析结束后必须询问是否对照知识库制订开发计划；在用户明确同意前，不调用
  `spec-knowledge-plan`，不写 `dev-plan.md`，不开始开发。用户同意但当前对话里
  没有这张 Skill 时，提示去 SpecWeaver Skills 页打开它，仍不得在本 Skill 里
  写计划或读 `.knowledge`。

开始前完整读取 [需求文档模板](references/requirement-template.md)。

## 1. 输入门槛

从完整收集转入时，先完整读取
[`spec-requirement-collection` 的完整收集决策表](~/.specweaver/skills/requirement-collection/references/collection-decision-table.md)，
只有表中明确指向本 Skill 的分支可以进入。除此之外，满足以下入口并提供或能唯一定位
已收集目录：

- 用户明确要求分析、生成需求文档或重新分析已有来源。

项目目录至少包含：

```text
requirement-raw.md
```

1. 从 `requirement-raw.md` 读取任务 URL，以 URL 和绝对项目目录调用
   `requirement_get_manifest`，再读取返回的缓存清单。只支持 `schema_version: 3`；
   其他版本报告不兼容并暂停。
2. 验证清单中声明成功的相对路径仍在目录内且真实存在。
3. `verification.status` 必须是 `success`；`failed`、缺失或未知值都报告具体问题并
   暂停，不得静默重新收集。
4. 已采用 API 文档来源时，读取清单指向的 `api/` 文件。`api-list.md` 只是名单，
   不是字段契约；详情 JSON（`specweaver_schema` 以 `-api` 结尾）才是。只有名单、
   没有详情 JSON 时跳过 API 部份，不得把目录表写成接口契约，也不得重新收集。
   缺文件按缺失处理。
5. 清单整体或单个来源为 `partial` 时，可以分析已收集事实，但必须在文档显著标出
   缺失、失败状态和影响。`success`、`not_applicable`、`skipped` 依清单原样处理；
   其他来源状态按失败事实记录，不改写成“无资料”。

## 2. 读取来源

只读取清单明确引用的路径，不通过 `design/*`、`images/*` 或 `api/*` 通配读取目录中
未被清单采用的旧文件或人工文件：

1. `requirement-raw.md`：正文、独立评论、引用、子任务、附件位置和外部来源。
2. `requirement.attachments` 中的出现位置映射与成功附件：核对出现位置；需要视觉事实时
   查看图片。
3. `design.items[]`：用 `id` / `url` / `platform` / `canvas` 索引屏；先看
   `preview_file`，理解页面、状态和信息层级。
4. `design.items[].design_cache_file`：只在需要精确值时按节点/区域查询统一 schema
   （`text.content`、`frame.x/y/w/h`、`fill`、`layout`）。不要通读 JSON，不要把
   图层树贴进 `requirement.md`，不要再用 `layers` / `textInfo` / `frame.left`。
5. `apidoc.items[].path`：名单读 `api-list.md`；详情读 JSON。只有名单没有详情时
   跳过 API 部份。文件缺失按缺失处理，不重新收集。

只在需求结论确实依赖时加载大型结构或附件，避免把全部原始对象复制进对话。

## 3. 综合规则

- Tower 明确陈述、设计事实、API 契约分别标注来源。
- 同一规则被多个来源支持时合并结论并列出全部证据。
- 来源冲突时并列展示，不擅自裁决；记录需要用户确认的决策。
- 可以从多个事实推出结论，但必须标记为“推导”并写清依据。
- 不按行业经验补全验收条件、异常流程、权限、状态、接口或视觉细节。
- 子任务只有标题和链接时，不假装已读取子任务正文。

## 4. 生成与验证

按模板在收集目录生成 `requirement.md`：

- 删除所有占位说明和无关章节；
- 链接到项目内现有来源文件，使用相对于 `requirement.md` 的路径；不要把机器缓存
  路径写进面向用户的来源索引；
- 只写需求事实、规则、场景、验收描述、设计/API 证据、冲突、缺失和范围边界；
- 不写技术方案、代码改动、开发步骤、工期或插件内部流程。

完成前验证：

1. 每条关键结论都能追溯到本地来源或用户明确补充；
2. 所有 Markdown 链接和图片路径真实存在；
3. 文档没有声明未收集或失败的来源已完成；
4. 文档没有 Cookie、密码、Token、Authorization 或登录响应；
5. `requirement.md` 之外的来源文件没有被修改。

验证通过后报告文档路径、采用来源、主要冲突和缺失。

然后必须问一句：是否对照当前项目知识库制订开发计划。冲突或缺失还没确认完时，先提醒
把这些对齐，再问要不要出计划。用户没明确说要，就停在这里。

用户同意后：

1. 当前对话能用 `spec-knowledge-plan`：不要在本 Skill 里写计划或读 `.knowledge`
   原文；转交它，由主对话派一次 Subagent 去写 `dev-plan.md`。
2. 找不到这张 Skill：停下来告诉用户去 SpecWeaver Skills 页打开「对照知识库写开发
   计划」，分析到此结束。不得自己写 `dev-plan.md`，不得读 `.knowledge` 原文。
