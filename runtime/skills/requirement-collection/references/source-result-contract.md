# 来源结果协议

每个 `<platform>-source-collection` Skill 向总控返回同一逻辑结构。结果可直接存在于
当前任务上下文，不要求额外写入 JSON 文件。

```json
{
  "source": "<platform>",
  "status": "success",
  "scope": {},
  "items": [],
  "artifacts": [],
  "provenance": [],
  "unresolved": []
}
```

## 字段

| 字段 | 约束 |
| --- | --- |
| `source` | 稳定的小写平台标识，例如 `tower`、`lanhu`、`eolink` |
| `status` | 使用下方固定状态，不使用模糊自然语言 |
| `scope` | 调用方确认的采用范围、未采用范围和确认依据 |
| `items` | 平台事实摘要；大型原始对象必须落盘或保留在工具侧 |
| `artifacts` | 真实存在的本地文件、类型、稳定编号、来源和状态 |
| `provenance` | 原始 URL、平台 ID、出现位置及事实到来源的映射 |
| `unresolved` | 缺失、冲突、歧义、失败原因和需要用户确认的事项 |

固定状态：

```text
success
partial
missing_config
disabled
auth_expired
forbidden
network_error
not_found
api_error
invalid_input
verification_required
compatibility_error
skipped
not_applicable
```

完整收集的人类可读 `artifacts` 路径使用相对于需求资料目录的路径；Tower 预读产物、
需求清单和蓝湖规范化结构使用用户缓存绝对路径。平台工具需要写项目文件时仍传入
绝对输出目录。
任何字段都不得包含 Cookie、邮箱密码、Token、Authorization、登录响应或完整请求头。
