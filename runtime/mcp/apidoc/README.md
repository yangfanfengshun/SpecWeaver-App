# apidoc 多平台对接

Eolink + Apifox 是本仓库第一套「同一能力、多家平台」的落地。
下一刀（Postman 或别的 API 平台）按本文做，不要再发明第二套隔离。

平台方言、实测踩坑、官方工具表写进 `reference-docs/<平台>.md`（Apifox 见
[`reference-docs/apifox.md`](../../../reference-docs/apifox.md)）。
本文只记**加平台时公共层怎么动、哪里不许动**。

## 产品面

宿主对 Agent 只暴露 **`apidoc_auth` + `apidoc_read`**，不按平台拆工具。
链接进门后 `claims_url` 内部分发。各家自己打平台，出口同一份信封：

```text
{status, platform, message, data?}
data.kind = "list" | "detail"
```

不要：

- 把官方 MCP / OpenAPI 工具配进宿主（收集脚本调不到，Agent 还会选错）
- 为新平台在 `server.py` 加第三个工具
- 把一家的字段塞进另一家的原文结构（例如让 Postman 去填 `apiInfo.baseInfo`）
- 在第一次收集目录/项目链接时就按接口 ID 勾选详情
- 开放写接口（创建 / 更新 / 导入）

写还没开。读通、收集通、分析闸通，才算接上。

## 隔离：加目录，不改编排

```text
runtime/mcp/apidoc/providers/<name>/
    __init__.py     PLATFORM + claims_url + 再导出下面这组名字
    auth.py         凭证、探活、请求头
    client.py       parse_url / check_auth / read / auth_error
    normalize.py    平台原文 → schema.py 的 list / detail
```

公共层靠扫目录发现，靠 `claims_url` 认领：

| 层 | 文件 | 加平台时 |
| --- | --- | --- |
| 发现 / 路由 | `capability.py` | **零改动** |
| 工具面 | `apidoc/server.py` | **零改动** |
| 中立信封 | `apidoc/schema.py` | 缺字段才扩，两边 mapper 一起改 |
| 名单落盘 | `apidoc/catalog.py` | **零改动**（折成 `api/api-list.md`） |
| 完整收集 | `apidoc/source.py`、`requirement/core.py` | **零改动**（`provider_of("apidoc", url)`） |

禁止在公共代码里写 `from apidoc.providers.postman import ...`。
删目录 = 下线该平台；旧缓存里的链接走 `no_provider`，不崩溃，也不静默丢。

`capability.py` 只强制 `PLATFORM` + `claims_url`。apidoc 另外约定这组名字，
`server.py` 和收集脚本只通过它们调用：

| 名字 | 职责 |
| --- | --- |
| `claims_url(url) -> bool` | 这条链接归不归我。两家都认 = 路由不确定，必须互斥 |
| `parse_url(url)` | 从链接抠 `project_id` / `folder_id` / `api_id`。能抠 ID 就抠 ID，不要按名字搜 |
| `check_auth()` | 探活。返回 `{status, platform, message}`，不回凭证 |
| `read(url, api_id=None)` | 目录/项目 → `kind: list`；单接口或带 `api_id` → `kind: detail` |
| `auth_error(exc)` | HTTP 走 `common.http_error_result`，只传自家状态码表 |
| `current_settings()` | 读 `CONFIG_KEYS` 里的凭证，给 client 用 |

`read` 的分支抄现有两家：链接无效 → `invalid_input`；认证失败走 `auth_error`；
成功用 `schema.success`。空目录是成功名单（`items: []`），不是 `not_found`。

## 路由

`resolve_provider` 的规则已经钉死，新平台不要绕：

1. **给了 URL**：必须有人 `claims_url` 认领。没人认领 → `no_provider`。
   不能因为「现在只有一家」就兜底误路由。
2. **没给 URL**：apidoc 用 Eolink 兜底（主力）。`capability.py` 的单家回退规则
   不动；裸数字 ID 进 Eolink 后先打详情，没有再当项目名单，两边都没有就停。
3. **设置页验证不走 `apidoc_auth`**，走 `auth_cli.py <platform>-verify`。

`claims_url` 认域名或链接形状，不要读本地配置才决定认不认——没配认证时
收集脚本仍要能把链接分到正确的能力桶。Eolink 私有化靠域名含 `eolink` 或
fragment 形状兜底，是特例，别抄成「先读 BASE_URL 再认领」。

## 中立结构

`schema.py` 是唯一出口。mapper 负责折，公共层负责字段名和空值省略。

| `kind` | 最少要有 |
| --- | --- |
| `list` | `source_url`、`location`、`items[]`（`api_id` / `name` / `method` / `path`） |
| `detail` | `api_id`、`name`、`method`、`path`、`location`、`request.parameters`、JSON 体另有 `request.schema`、`responses` |

`method` 大写。`in` 取值：`path` / `query` / `header` / `body`。
平台扩展字段（`x-apifox-*`、mock、preprocessor）丢掉，不进详情。

## 收集

产物都在 `api/` 下，不要改到输出根目录：

- 目录 / 项目链接 → `api/api-list.md`，整体 `api_selection_required`
- 单接口链接，或范围里带了 `api_ids` → `api/<API-ID>-<接口名称>.json`

第一次收集目录/项目时不要问用户勾选接口。总控拿到名单后再问；确认前不分析。
本机不另存平台原文。

## 认证：必须横向同步

凭证进 `~/.specweaver/.env`。漏抄任何一处，轻则设置页存了 MCP 读不到，
重则 `auth.rs` 的 `ENV_KEYS` 没登记，保存时把别的键写丢。

Token 型抄 Apifox（本身抄的 GitLab）：密码框 + 保存后列表里点「验证」。
账号密码型抄 Eolink。**OAuth 没有现成实现**，下一刀能走 API Key 就走 API Key。

### Provider / Python

1. `runtime/.env.example` 加空键。发布敏感词扫描从这里动态读，不要手写第二份。
2. `runtime/mcp/common.py` 的 `CONFIG_KEYS` **和** `PLATFORM_LABELS`。
   `read_config` / `update_config_atomic` 只认 `CONFIG_KEYS`，漏了等于没存。
3. `providers/<name>/auth.py`：探活函数，不回 Token / Cookie / 登录响应。
4. `runtime/scripts/auth_cli.py` 登记 `<name>-verify`（或 login），给 Rust 调。
5. `runtime/mcp/apidoc/meta.json` 的 `authPlatforms` 数组追加平台 id。
   MCP 卡片任一平台已配置即可过门禁，不要收窄成单个字段。

### Rust

6. `src-tauri/src/auth.rs`：`ENV_KEYS` 必须带上新键；再加 status / save / verify / clear。
7. `src-tauri/src/commands.rs` 暴露命令。
8. `src-tauri/src/lib.rs` 的 `generate_handler!` 逐个列出。漏了前端 `invoke` 会报未知命令。

### 前端

9. `src/constants/config.ts` 的 `CONFIG_PLATFORMS`。
10. `src/utils/tauri.ts` 一组 invoke。
11. `authModalConfigs.ts` 一份弹窗；Token 型 `fields: []` + `passwordField`。
12. `platformActions.ts` 的 `VERIFY_ACTIONS`（已保存凭据复验）。
13. `ConfigPanel.tsx`：`AUTH_MODALS` 加一行，`useReset` 加一条清空。
14. `ConfigList.tsx` 的 `rowStateById`。
15. `PlatformAuth/platformAuth.ts` 的 `PlatformAuthStatusById`。
16. `PlatformAuthProvider.tsx`：`EMPTY_STATUSES`、`refreshStatuses` 的 `Promise.all`。
    测试里的 mock 状态对象也要带上新键，否则类型过不了。

改完一处，把上面当清单再扫一遍。这个仓库栽过的就是「当时那份改对了，后来抄的那份没跟上」。

## 测试

测协议和路由，不打真网。

| 要钉住的 | 放哪 |
| --- | --- |
| 消毒后的名单 / 详情 / 空目录原文 | `tests/fixtures/apidoc/` |
| mapper：字段、空名单、扩展字段丢掉 | `tests/test_apidoc_normalize.py` |
| `claims_url`、缺头、`ProviderDiscovery` | 平台自己的 `tests/test_<name>_provider.py` |
| 设置页探活不回 Token | `tests/test_<name>_auth.py` |
| 两家都是 provider；无 URL 不单家回退 | `test_apidoc_normalize.py` 里已有同类断言，新平台跟上 |

空目录和「资源不存在」不是同一种失败，有人话响应就按成功空名单测一条。

## 下一刀：Postman

对标 Apifox，仍是 apidoc 能力，**新建 `providers/postman/`，不要新建 MCP**。

开工前先确认认证形态：Remote OAuth 是四个候选里唯一要新建基础设施的；
Local API Key 才能复用现在的 Token 表单。没拍板之前不要先铺 OAuth。

官方若提供 MCP / OpenAPI 元工具：SpecWeaver 自己当 HTTP 客户端去调需要的那几个，
再包成 `apidoc_read`。不要把官方工具面投影给宿主。

链接认领、list/detail、`api/` 产物、认证横向清单，全部按本文。
平台怎么打、测过哪些接口，写 `reference-docs/postman.md`，不要堆回 `todos/`。
