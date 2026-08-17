# design 多平台对接

蓝湖 + Figma 是 design 能力「同一能力、多家平台」的落地。
下一刀（MasterGo 或别的设计平台）按本文做，不要再发明第二套隔离。

平台方言、实测踩坑、官方接口表写进 `reference-docs/<平台>.md`（Figma 见
[`reference-docs/figma.md`](../../../reference-docs/figma.md)）。
出口 schema、落盘形状、下游怎么查尺子见
[`reference-docs/design.md`](../../../reference-docs/design.md)。
本文只记**加平台时公共层怎么动、哪里不许动**。

## 产品面

宿主对 Agent 只暴露 **`design_check_auth` + `design_read`**，不按平台拆工具，
也不把预览 / 切图下载做成 MCP 工具。链接进门后 `claims_url` 内部分发。

各家自己打平台，出口同一份信封：

```text
{status, platform, message, ...}
候选：designs[]（id / name / preview_url）
单屏：统一 schema（可含 preview_url）
```

完整收集走 `requirement_collect`，由 `design/source.py` 调 provider 内部的
`get_candidates` / `get_detail` / `download_previews` / `download_slices`。

不要：

- 给 Agent 再暴露 `design_download_*` / `design_get_candidates` / `design_get_detail`
- 为新平台在 `server.py` 加第三个工具
- 把官方 MCP（例如 Figma MCP）配进宿主
- 把 JSON 写进用户项目 `design/` 或 `requirement.md`
- 公共层点名某个 provider，或把某家的折树 / 选切图塞回 `schema.py`
- 把一家的切图启发式当成另一家的业务规则
- OAuth / Plan token（能走 PAT 或账号密码就走现成表单）

独立核对停在对话里看候选 / schema / `preview_url`，**不写**用户项目。
完整收集才落盘。

## 隔离：加目录，不改编排

推荐目录形状（Figma 这份）：

```text
runtime/mcp/design/providers/<name>/
    __init__.py     PLATFORM + claims_url + 再导出下面这组名字
    auth.py         凭证、探活、请求头
    parse.py        认领链接、抠 file / node / 项目 id
    normalize.py    平台原文 → schema.py
    impl.py         check_auth / read / get_candidates / get_detail / 下载
```

蓝湖目录更碎（`api.py` / `design.py` / `download.py` / `session.py`），那是历史形态，
新平台按 Figma 这份来，不要再抄蓝湖那套拆法。

公共层靠扫目录发现，靠 `claims_url` 认领：

| 层 | 文件 | 加平台时 |
| --- | --- | --- |
| 发现 / 路由 | `capability.py` | **零改动** |
| 工具面 | `design/server.py` | **零改动** |
| 中立出口 | `design/schema.py` | 缺字段才扩，两边 mapper 一起改 |
| 完整收集 | `design/source.py` | **零改动**（`provider_of("design", url)`） |
| 编排 | `requirement/core.py` | **零改动** |

禁止在公共代码里写 `from design.providers.mastergo import ...`。
删目录 = 下线该平台；旧缓存里的链接走 `no_provider`，不崩溃，也不静默丢。

`capability.py` 只强制 `PLATFORM` + `claims_url`。design 另外约定这组名字，
`server.py` 和 `source.py` 只通过它们调用：

| 名字 | 职责 |
| --- | --- |
| `claims_url(url) -> bool` | 这条链接归不归我。两家都认 = 路由不确定，必须互斥 |
| `check_auth(url="")` | 探活。返回 `{status, platform, message}`，不回凭证 |
| `read(url, image_id="", output_file="")` | 项目 / 文件 → 候选屏；带 `image_id` 或单屏链接 → 统一 schema |
| `get_candidates(url)` | 完整收集用。返回 `designs[]` |
| `get_detail(url, image_id, output_file)` | 完整收集用。写入规范化 JSON，带 `preview_url` |
| `download_previews(items, output_dir)` | 完整收集用。预览图落到 `design/<屏名>/preview.*` |
| `download_slices(url, image_id, output_dir, ...)` | 完整收集用。切图落到 `design/<屏名>/slices/` |

`read` 的分支抄现有两家：链接无效 → `invalid_input`；认证失败走自家错误分类；
成功走 schema。大稿超过约 120 个节点时，不带 `output_file` 只回精简 `navigation`。

## 路由

`resolve_provider` 的规则已经钉死，新平台不要绕：

1. **给了 URL**：必须有人 `claims_url` 认领。没人认领 → `no_provider`。
   不能因为「现在只有一家」就兜底误路由。
2. **没给 URL**：现在同时有蓝湖和 Figma，不带链接的 `design_check_auth()` 会
   `no_provider`（只有一家时才允许单家回退）。不要为了省事改成「默认蓝湖」。
3. **设置页验证不走 `design_check_auth`**，走 `auth_cli.py <platform>-verify`。

`claims_url` 认域名或链接形状，不要读本地配置才决定认不认——没配认证时
收集脚本仍要能把链接分到正确的能力桶。

## 中立结构

`schema.py` 是唯一出口。mapper 负责折，公共层只卡形状。

| 字段 | 规则 |
| --- | --- |
| `type` | 只许 `text` / `image` / `icon` / `container` / `shape` |
| `frame` | 相对画板左上角，不用平台宇宙坐标 |
| 颜色 | `#RRGGBB`，不要 0–1 小数 RGB |
| `layout` | 有 Auto Layout 才写 `row` / `column`；没有就省略 |
| `text` | 文案放 `content` |
| `asset` | 有切图才写，指向 `slices/...` |

下游只认这套键，不按平台分支读 `layers` / `textInfo` / `absoluteBoundingBox`。
平台原始树只作收集输入，不进缓存、不进用户目录。细则见 `design.md`。

## 收集

JSON 进 `~/.specweaver/cache/design/<platform>/...`，图进用户需求目录：

```text
<需求名称>/
  design/
    <屏名>/
      preview.png
      slices/          # 有切图才建
```

不要 `images/` 扁平目录，也不要 `<platform>-slices/` 前缀。收集不写 `requirement.md`。
切图规则留在各自 provider：蓝湖认自己树上的图片 URL，Figma 认画板内填充 / SLICE /
小矢量；**不要**把一家的启发式套到另一家。

## 认证：必须横向同步

凭证进 `~/.specweaver/.env`。漏抄任何一处，轻则设置页存了 MCP 读不到，
重则 `auth.rs` 的 `ENV_KEYS` 没登记，保存时把别的键写丢。

Token 型抄 Figma（本身抄的 Apifox）：密码框 + 保存后列表里点「验证」。
账号密码型抄蓝湖。**OAuth 没有现成实现**，下一刀能走 PAT 就走 PAT。

### Provider / Python

1. `runtime/.env.example` 加空键。发布敏感词扫描从这里动态读，不要手写第二份。
2. `runtime/mcp/common.py` 的 `CONFIG_KEYS` **和** `PLATFORM_LABELS`。
   `read_config` / `update_config_atomic` 只认 `CONFIG_KEYS`，漏了等于没存。
3. `providers/<name>/auth.py`：探活函数，不回 Token / Cookie / 登录响应。
4. `runtime/scripts/auth_cli.py` 登记 `<name>-verify`（或 login），给 Rust 调。
5. `runtime/mcp/design/meta.json` 的 `authPlatforms` 数组追加平台 id。
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
| 消毒后的图层树 / 切图 golden | `tests/fixtures/design/` |
| 出口 schema：五种 type、相对 frame、切图回写 | `tests/test_design_schema.py` |
| `claims_url`、候选范围、切图选取 | 平台自己的 `tests/test_<name>_provider.py` |
| 设置页探活不回 Token / Cookie | `tests/test_<name>_auth.py` |
| 两家都在时无 URL 不单家回退 | 新平台跟上：给了别人的链接必须 `no_provider` |

测试禁止引用 `todos/`。golden 只放消毒后的结构，不要平台原始整树。

## 下一刀：MasterGo

对标 Figma，仍是 design 能力，**新建 `providers/mastergo/`，不要新建 MCP**。

开工前先确认认证形态：能走 PAT / API Key 就复用 Token 表单；账号密码抄蓝湖。
没拍板之前不要先铺 OAuth。

官方若提供 MCP：SpecWeaver 自己当 HTTP 客户端去调需要的那几个，再包成
`design_read`。不要把官方工具面投影给宿主。

链接认领、候选 / 单屏、`design/<屏名>/` 产物、认证横向清单，全部按本文。
树怎么折、切图怎么选，写 `reference-docs/mastergo.md`，不要堆回能力包 README。
