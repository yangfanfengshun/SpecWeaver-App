---
name: spec-design-source-collection
description: 从设计稿读取项目与设计候选，并在范围确认后保存设计预览图、规范化图层结构和真实切图，按统一来源协议返回可追溯设计证据。用户提供设计稿链接、要求收集或核对设计稿，或总控需求收集流程需要设计资料时使用。不分析业务代码、不生成最终需求文档、不直接开始页面开发。
---

# 设计稿来源采集

## 输入与边界

- 接收设计稿项目/设计链接（Figma 或蓝湖）、采用范围，以及可选的绝对项目输出路径。
- 只负责设计候选与设计证据；不决定需求模式，不生成最终 Markdown。
- 向总控返回结果时遵守
  [来源结果协议](~/.specweaver/skills/requirement-collection/references/source-result-contract.md)。

## 候选与认证

1. 调用 `design_check_auth()` 确认能力状态。
2. `disabled` 表示本机未启用设计稿能力，不等于无设计稿；立即返回，不继续询问或请求。
3. 调用 `design_read(url)`：项目/文件链接返回候选屏，不下载图片。
4. 只有调用方已确认唯一范围时才读详情；存在多组或关联不明时返回候选与歧义，
   不自行选择。
5. 单屏链接，或额外传 `image_id` 时，`design_read` 返回统一 schema（可带
   `preview_url`）。独立核对此处停止，不要把 JSON 写进用户项目 `design/`。

认证失效时返回 `auth_expired`，提示用户打开 SpecWeaver 设置页重新配置 Figma Token
或蓝湖；区分 `forbidden`、`network_error` 和 `not_found`，不得自动改成无设计稿。

## 完整收集已确认设计

用户项目落盘（`design/<屏名>/preview.png`、可选 `slices/`、缓存 JSON）只走
`requirement_collect`，不要自行串下载。清单 `design.items[]` 每屏一条，字段是
`id`、`name`、`platform`、`url`、`preview_file`、可选 `slices_dir`、
`design_cache_file`、`canvas`。

## 结果要求

- `items` 只返回候选摘要或已确认设计摘要，不把完整图层树放入对话。
- `artifacts` 区分 `preview`、`design_facts` 和 `slice`；项目图片使用相对目标目录的
  路径，`design_facts` 使用用户缓存绝对路径。
- 预览图负责整体视觉理解，结构文件负责精确查询；先看 `preview`，再按需查询节点。
  不得通读 JSON，不得把图层树贴进用户文档，不得仅凭视觉猜颜色、间距或字体。
- 查询字段是统一 schema：`type` 为 `text|image|icon|container|shape`，文案在
  `text.content`，坐标在相对画板的 `frame.x/y/w/h`。不要再用 `layers`、`textInfo`、
  `frame.left` 当契约。平台内部怎么解析、怎么切图是各 provider 的事。
- 不输出 Cookie、Authorization、Token 或登录响应。

独立使用时返回设计证据摘要；除非用户明确要求进入开发，否则不调用
`spec-design-implementation`。
