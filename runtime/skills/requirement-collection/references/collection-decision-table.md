# 完整收集决策表

本表是 `spec-requirement-collection` 从 Tower 预读进入完整收集、处理暂停点并决定是否转入
`spec-requirement-analysis` 的唯一状态—动作表。按工具真实返回状态匹配，不用自然语言猜测
“大概完成”。

## 执行原则

1. 先匹配当前状态，再执行对应行；不得跨行合并用户确认。
2. “暂停”表示先报告状态、`unresolved` 和可选动作，收到用户明确选择前不继续调用。
3. 用户取消时立即停止；用户明确“只收集”只改变成功后的去向，不把 `partial` 改成
   `success`。
4. 每次重试或跳过都保留同一绝对 `output_dir` 和已确认范围，只有表中列出的字段可以
   改变。
5. 任何跳过或接受缺失都保留在 `confirmed_scope`、清单或最终报告中，不得静默丢弃。
6. API 文档写在 `api/` 下：目录/项目链接先写入 `api-list.md`，整体返回
   `api_selection_required`，暂停等用户指定接口。单接口或已确认的 `api_ids`
   仍按 `<API-ID>-<接口名称>.json`。收集当时不要勾选；没有确认详情范围前不转入分析。

## 状态—动作

| 当前状态 | 前置条件 | 是否暂停 | 用户选择 | 下一步调用 | 参数变化 | 禁止动作 |
| --- | --- | --- | --- | --- | --- | --- |
| Tower 预读 `success` | 已取得 `cache_file`、图片缓存统计和来源线索 | 否 | 无 | `requirement_collect(url)` | 无 | 不重复请求 Tower；不直接写项目 |
| Tower 预读 `partial` | 原文缓存成功，但图片或其他证据存在 `unresolved` | 是 | 重试预读；或明确接受缺失后继续 | 重试时调用 `requirement_read_todo(url)`；接受后调用 `requirement_collect(url)` | 接受缺失不改工具状态；Agent 在当前任务持续保留并报告 `unresolved` | 不把 `partial` 描述成完整；不静默跳过缺失证据 |
| Tower 预读认证、权限、网络或输入失败 | 未形成可用缓存 | 是 | 修复配置后重试；或取消 | `requirement_read_todo(url)` | 只修复用户确认的配置或输入 | 不进入候选发现；不伪造缓存 |
| `cache_missing` 或 `cache_invalid` | `requirement_collect` 无法读取有效 Tower 缓存 | 否；若重读仍失败则暂停 | 无；失败后由用户修复或取消 | `requirement_read_todo(url)`，成功后重新调用 `requirement_collect(url)` | 无 | 不绕过缓存直接完整收集 |
| `scope_ready` | 所有候选唯一，且项目根目录和默认输出目录可唯一确定 | 否 | 使用 `suggested_scope` | `requirement_collect(url, output_dir, confirmed_scope)` | 用 `suggested_scope` 形成完整 `confirmed_scope`；首次写入保持 `replace_existing: false` | 不扩大候选；不省略 `confirmed_scope` 固定字段 |
| `scope_ready` | 输出项目或目录不能唯一确定 | 是 | 确认绝对输出目录；或取消 | 确认后调用写入形式的 `requirement_collect` | 只确定 `output_dir` | 不猜测项目；不写相对路径 |
| `scope_confirmation_required` | 候选存在歧义、需跳过项或需补充范围 | 是 | 选择、跳过、补充或取消 | 范围完整后调用写入形式的 `requirement_collect` | 将选择写入 `design`、`apidoc`、`tower_attachments` 和 `skipped_sources`；首次写入保持 `replace_existing: false` | 不自行选择歧义候选；不扩大范围 |
| 候选阶段的认证、权限、网络或来源失败 | 至少一个待采用来源无法确定候选 | 是 | 修复后重试；明确跳过并说明原因；或取消 | 重试调用 `requirement_collect(url)`；跳过后调用写入形式 | 跳过项从采用数组移除，并追加带 `source`、URL、原因的 `skipped_sources` | 不把失败来源改成“无资料”；不默认跳过 |
| `invalid_input` 或 `invalid_output` | 参数不完整、目录不安全或范围不合法 | 条件性暂停 | 能唯一纠正本次调用参数时纠正；会改变用户范围或目录时先确认；也可取消 | 纠正后重调当前 `requirement_collect` | 只改错误字段；不得顺带改变已确认范围 | 不绕过路径和范围校验；不创建符号链接输出 |
| `existing_output_confirmation_required` | 目标目录已有脚本管理产物 | 是 | 更新同一目录；改用新目录；或取消 | 再次调用写入形式的 `requirement_collect` | 更新同一目录设 `replace_existing: true`；新目录保持 `false` | 不静默覆盖；不删除人工文件 |
| `partial` | 写入完成但来源、附件或产物存在 `unresolved` | 是 | 重试失败项 | 再次调用写入形式的 `requirement_collect` | 保留原目录与范围，设 `replace_existing: true`；当前版本确定性重跑全部已选来源 | 不承诺只请求失败来源；不自动分析 |
| `partial` | 用户明确跳过设计稿或 API 文档来源 | 否，选择已明确 | 再收集 | 再次调用写入形式的 `requirement_collect` | 从对应采用数组移除，追加 `skipped_sources`，设 `replace_existing: true` | 不保留已跳过项为采用来源 |
| `partial` | 用户明确只跳过某个 Tower 附件 | 否，选择已明确 | 再收集 | 再次调用写入形式的 `requirement_collect` | 保持 `tower_attachments: true`，追加该 URL 与原因到 `skipped_sources`，设 `replace_existing: true` | 不误关全部 Tower 附件 |
| `partial` | 用户明确跳过全部 Tower 附件 | 否，选择已明确 | 再收集 | 再次调用写入形式的 `requirement_collect` | 设 `tower_attachments: false`、`replace_existing: true` | 不继续下载 Tower 附件 |
| `partial` | 用户明确接受现有缺失并要求分析 | 否，选择已明确 | 接受缺失并分析 | 读取 `~/.specweaver/skills/requirement-analysis/SKILL.md`，从现有输出目录分析 | 不再调用收集；保留清单中的 `partial`、`unresolved` 和影响 | 不改写成 `success`；不隐去缺失；不修改来源文件 |
| `partial` | 用户取消，或尚未选择处理方式 | 是；取消后停止 | 取消或等待 | 无 | 无 | 不自动重试、跳过或分析 |
| `api_selection_required` | 已写入 `api-list.md`，尚未确认详情范围 | 是 | 从名单指定 `api_ids` | 再次调用写入形式的 `requirement_collect` | 在对应 `apidoc` 项写入非空 `api_ids`，设 `replace_existing: true` | 不自行挑选接口；不把名单当成字段契约；不转入分析 |
| `api_selection_required` | 用户明确不需要 API 详情 | 否，选择已明确 | 跳过 API 文档 | 再次调用写入形式的 `requirement_collect` | 从 `apidoc` 采用数组移除，追加带 `source`、URL、原因的 `skipped_sources`，设 `replace_existing: true` | 不带着空名单转入分析 |
| `success` | 用户此前明确要求只收集或暂不分析 | 否，随后停止 | 无 | 无 | 保留来源与清单 | 不调用 `spec-requirement-analysis` |
| `success` | 用户未要求只收集 | 否 | 无 | 读取 `~/.specweaver/skills/requirement-analysis/SKILL.md`，从已收集目录生成 `requirement.md` | 无；不再调用收集 | 不二次询问是否分析；不修改来源文件 |

## `confirmed_scope` 固定形状

每次写入调用都显式传入全部字段：

```json
{
  "tower_attachments": true,
  "allow_restricted_attachments": false,
  "replace_existing": false,
  "design": [],
  "apidoc": [],
  "skipped_sources": []
}
```

视频、压缩包或已知超大附件只有在用户明确同意后，才能把
`allow_restricted_attachments` 改为 `true`。
