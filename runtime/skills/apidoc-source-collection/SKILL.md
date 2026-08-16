---
name: spec-apidoc-source-collection
description: 从 API 文档链接或 API ID 读取项目、分组、接口、请求参数、响应字段、枚举和示例，并按统一来源协议返回可追溯 API 事实。用户提供 API 资料、要求单独核对接口定义，或总控需求收集流程需要 API 证据时使用。不推断未提供契约、不读取其他平台、不生成最终 API 文档。
---

# API 文档来源采集

## 输入与边界

- 接收 API 文档 URL 或总控已确认的来源范围（当前支持 Eolink、Apifox）。
- 只负责 API 来源事实；不决定需求模式，不生成 `requirement.md`。完整收集写入
  `api/`：名单 `api-list.md`，详情 `<API-ID>-<接口名称>.json`。
- 向总控返回结果时遵守
  [来源结果协议](~/.specweaver/skills/requirement-collection/references/source-result-contract.md)。

## 收集流程

1. 调用 `apidoc_read`，传 `url`。目录或项目链接返回名单（`data.kind = list`），
   完整收集写成 `api/api-list.md`，收集当时不要问用户勾选接口 ID；总控在
   `api_selection_required` 暂停后再收集详情。
   单接口链接返回详情（`data.kind = detail`），写成 `api/<API-ID>-<接口名称>.json`。
2. 只有数字、没有链接时传 `api_id`：走 Eolink，先详情再项目名单，两边都没有就停。
3. 独立核对且用户明确只要某一条、又有链接时，可以额外传 `api_id` 读详情。
4. 大型返回结果保存后提取接口/字段摘要，不在对话中打印或截断完整响应。
5. 相同 API ID 只保留一个条目，但保留它在不同来源中的每次引用。

## 结果要求

- `items` 每项至少包含 API ID、名称、方法、路径和文档状态。
- 字段事实保留方向、字段路径、类型、单位/枚举、必填条件、示例、说明和来源。
- `artifacts` 只记录调用方明确要求保存且真实存在的来源文件。
- `unresolved` 明确记录等待后端、资料缺失、冲突或字段歧义，不生成空接口。
- 认证失效时返回 `auth_expired`，提示用户打开 SpecWeaver 设置页重新配置对应平台；区分
  `forbidden`、`network_error` 和 `not_found`，不得自动改成无 API。
- 不输出 Cookie、Token、Authorization 或登录响应。

独立使用时只返回 API 事实摘要，不分析调用代码或制定接入方案。
