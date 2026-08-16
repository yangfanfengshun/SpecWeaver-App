---
name: spec-requirement-collection
description: 读取需求任务并按用户意图执行快速分析或确定性完整收集。用户提供任务链接、要求分析 Bug、快速梳理需求、收集任务/设计稿/API 来源或生成需求资料目录时使用。完整收集只调用统一脚本写来源文件，成功后默认自动转入 spec-requirement-analysis；用户明确只收集时停止。不分析代码、不执行开发。
---

# 需求资料收集

## 边界

- 收集和分析由两个 Skill 分工；完整收集成功后默认连续执行，不再二次询问。
  若返回 `api_selection_required`，先暂停让用户指定接口，确认前不分析。
- Tower、附件、设计稿和 API 文档的格式、文件名、排序、写入与验证由 MCP 脚本负责。
- Agent 不组合来源 MCP、不改写来源文件、不自行选择歧义候选。
- 每次读取 Tower 都把正文和评论中的普通图片写入用户缓存；不由 Agent 判断是否下载。
- Bug 和快速模式只在对话中回答，不在用户项目生成文件。
- 本 Skill 不生成 `requirement.md`；完整收集成功后自动改用 `spec-requirement-analysis` 生成。
- 用户在收集前明确要求“只收集资料”或“暂不分析”时，收集成功后停止。
- 不制定技术方案、代码范围、开发计划或工期，不开始开发。

维护本 Skill、MCP 或宿主说明时，使用
[流程语义清单](references/workflow-semantics.json) 核对所有受影响入口；运行时仍以本
Skill 和完整收集决策表为准。

## 1. Tower 预读

调用 `requirement_read_todo(url)`。

该工具会把可读原文和机器元数据分别原子写入用户缓存：

```text
~/.specweaver/cache/tower/<任务ID>/
├── tower-raw.md
├── tower-metadata.json
└── images/
    └── tower-image-NNN.<ext>
```

工具结果只包含任务类型、缓存路径、评论/附件/子任务数量、外部来源和未解决事项。

- 工具必须在同一次读取中缓存正文和全部评论里的普通图片；视频、压缩包和其他文件只保留附件索引。
- `tower-metadata.json` 记录图片来源、出现位置、本地路径、哈希和失败状态。
- 工具结果返回 `image_paths` 和图片缓存统计，不把全文或图片二进制返回对话。
- 需要分析 Tower 内容时，由 Agent 读取结果中的 `cache_file`。
- Bug 或快速分析必须查看所有 `image_paths`；图片缓存为 `partial` 时保留缺失证据并明确报告。
- 子任务默认只保留标题和链接；用户明确要求时再单独读取指定子任务。
- 只有明确 `Bug` Tag 才是 `task_type: bug`，其他情况均为普通需求。

## 2. 选择路线

### Bug

`task_type: bug` 时自动进入快速路线：

1. 读取缓存中的 `tower-raw.md`。
2. 查看预读结果中的全部 `image_paths`；不得因正文已有文字而跳过缓存图片。
3. 在对话中输出 Bug 现象、触发条件、期望、影响、证据和未明确事项。
4. 不读取设计稿或 API 文档，不在项目写文件，不生成 `requirement.md`，随后停止。

### 普通需求

- 用户明确要求快速分析：进入快速路线。
- 用户明确要求完整收集：进入完整收集。
- 意图不明确：询问选择“快速分析”或“完整收集并分析”，并说明完整收集成功后会自动
  生成 `requirement.md`；收到回答前暂停。
- 用户回答“不需要完整收集”或同义表达：进入快速路线。

快速路线读取缓存中的 `tower-raw.md` 并查看全部 `image_paths` 后在对话中回答，不读取
设计稿或 API 文档，不在项目写文件，不生成 `requirement.md`。图片缓存失败时明确报告缺失
证据，不得静默忽略。

## 3. 完整收集

开始前完整读取
[完整收集决策表](references/collection-decision-table.md)。该表是状态、暂停点、用户
选择、下一步调用和参数变化的唯一执行依据；下文只说明工具输入与产物结构。

### 3.1 候选与范围

先调用：

```text
requirement_collect(url)
```

脚本只读取 Tower 预读阶段生成的缓存并返回候选，不重复请求 Tower，
不写用户项目。缓存缺失时先返回 Tower 预读步骤。

默认输出目录使用脚本返回的 `suggested_directory_name` 放到 `<项目>/docs/requirement/`
下，不由 Agent 自行清洗任务标题。项目或输出目录不能唯一确定时按决策表暂停。

### 3.2 一次性写入

范围和目录确定后，首次写入只调用统一入口：

```text
requirement_collect(url, output_dir, confirmed_scope)
```

`confirmed_scope` 的固定形状和各分支允许的参数变化以决策表为准。

设计稿项使用脚本返回的 `url`、`image_id` 和名称；API 文档项使用来源 `url`。
目录或项目链接先写入 `api/api-list.md`，整体返回 `api_selection_required`，
暂停等用户从名单指定 `api_ids` 后再写入详情；单接口链接写入
`api/<API-ID>-<接口名称>.json`。收集当时不要勾选接口 ID。

视频、压缩包或已知超大附件只有用户确认后才把
`allow_restricted_attachments` 设为 `true`。

脚本只更新自己管理的来源路径，不删除人工文件。已有输出、失败重试、来源或附件跳过、
接受缺失和取消都严格按决策表处理。

脚本直接在项目写入并验证：

```text
<output_dir>/
├── requirement-raw.md
├── requirement-attachments/
├── api/
│   ├── api-list.md
│   └── <API-ID>-<接口名称>.json
└── design/
    └── <屏名>/
        ├── preview.png
        └── slices/            # 可选，没有切图则没有这个目录
```

机器清单写入
`~/.specweaver/cache/requirement/<任务ID>/<输出目录哈希>/manifest.json`；任务附件
映射直接包含在该清单中，不单独生成附件清单文件。设计稿规范化结构写入
独立缓存
`~/.specweaver/cache/design/<platform>/<image_id>/<设计名称>--<image_id>.json`。任务图片保留在
平台缓存供所有模式查看；完整收集的正式附件、设计预览图和切图仍写入项目目录。

API 文档写在 `api/`：名单是 `api-list.md`，详情仍是 `<API-ID>-<接口名称>.json`。
要看有哪些接口，打开名单即可。名单还没确认详情范围时不要开始分析。

不要调用 `requirement_download_images`、`design_read` 或 `apidoc_read` 自行重组
完整收集。

## 4. 收集结束

根据统一工具的小型结果报告：

- 输出目录；
- Tower、附件、设计稿和 API 数量；
- 验证状态、失败和待确认项。

随后按决策表决定暂停、重试、停止或转入分析，不在本节另建状态分支。
