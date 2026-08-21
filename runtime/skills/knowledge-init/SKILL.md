---
name: spec-knowledge-init
description: 在当前仓库根目录建立 .knowledge 知识库，供后续开发计划按 tag 查询。用户说「建立知识库」「初始化 .knowledge」「给这个项目建知识目录」时使用。不修改业务代码，不查询知识库写开发计划。
---

# 在新项目建立知识库

## 边界

- 只在当前 Git 仓库根目录创建或更新 `.knowledge/`。
- 不把本项目正文写进 SpecWeaver 插件；插件只提供本 Skill 和查询用法。
- 不修改 `src/` 业务代码。已有 `.knowledge/` 时先报告现状，得到确认再覆盖或增量。
- 抽不出证据的业务事实不要写成卡片，标「待确认」或留缺口。
- 未经用户明确要求，不 `git add`、不提交。确保 `.knowledge/` 未被 `.gitignore` 忽略即可。
- 脚本只倒入口表，不评判、不写 `.knowledge`。卡片仍由本 Skill 写。

## 1. 确认仓库

1. 确认当前目录是 Git 仓库根。`src` 若在子目录（如 `client/`），`.knowledge` 仍放仓库根，`paths` 带前缀。
2. 读 `package.json`、`README.md`、`AGENTS.md` / `CLAUDE.md`（有就读）。
3. 看 `src/` 顶层：`pages/`、`components/`、`_pages/`、`lib/modules_base/`、请求层位置。
4. 判定技术代际，写入卡片，不要写成「通用 Web / 通用小程序」：
   - Web：Umi 2 / 3 / 4，Antd 3 / 4 / 5
   - 小程序：Taro 2 / Taro 3 + KbPage / Taro 3 + NutUI 等
5. 用一句话写出项目是做什么的，作为业务目录的 `name`。
6. `AGENTS.md` 当线索。有入口表时以表和源码为准；文档有、表里没有的，标「机制在、页面未建」。没表时以源码和真实入口为准，文档和代码冲突标待确认。

## 2. 先跑入口探针，再分路

先跑脚本，用结果决定怎么写业务卡。不要先猜项目类型。

```bash
python3 <本Skill目录>/scripts/inventory.py --cwd <git根>
```

默认 JSON。脚本不评判、不写 `.knowledge`。

### 有表：Umi/Antd 后台或 Taro（`status: ok` 且 `kind` 为 `umi` / `taro`）

**对着表填卡，不准自己另扫 `routes.ts` / `app.config` 当入口表。**

- `path` / `name`：路由和菜单名，业务卡挂这里
- `hide_in_menu` / `redirect`：隐藏或跳转，仍要交代或显式不入库
- `page_file`：页面壳
- `impl_file` / `impl_imports`：壳子实际挂上的实现（Umi 常在 `_pages`）。目录名只进 `paths`，**不要按它归类**

不从 `src` 目录长出模块。同一 `path` 前缀可合成一张卡；独立用户目标仍要拆（登录和 `/f` 不要合成）。组件在 A、菜单在 B，归 B，tag 打在入口那张卡上。

### 没表：其它项目（`status: no_routes`）

没有 `config/routes.ts` 也没有 `app.config`。不要停，走摸代码：读 `App` / 导航常量 / `_pages` 顶层 / 真实页面入口，再写业务卡。归类仍跟**产品入口**走，不跟目录名走。结束报告标明「未绑路由表」。

底座卡两条路一样：摸本仓库实际在用的封装，不依赖入口表。

## 3. 目录结构

新项目通常没有现成知识 md。本 Skill 必须同时写**原文**和**目录**，不能只丢两份 JSON。入口表 JSON 不要写进 `.knowledge/`。

```text
.knowledge/
  catalog-stack.json
  catalog-project.json
  stack/                 # 底座原文，一张卡一篇
    table.md             # 本仓库若是表格就叫 table，不要抄成 long-list
    page.md
  project/               # 业务原文，一张卡一篇
    order.md
```

- `catalog-*.json` 只是检索表。`source` 必须指向 `.knowledge/stack/` 或 `.knowledge/project/` 下真实存在的 md。
- md 才是知识正文。没有对应 md 的卡不许入库。
- 底座只描述**这个仓库正在用的**那一代，不引用其他仓库的组件名。

## 4. 卡片字段

每张卡固定这些字段，与查询脚本一致：

```json
{
  "id": "table",
  "name": "分页表格",
  "tags": ["列表", "分页", "ProTableExtend"],
  "use_when": "何时用",
  "use_when_not": "何时不用",
  "source": ".knowledge/stack/table.md",
  "paths": ["src/..."],
  "constraints": ["必须遵守的禁区"]
}
```

`id`、`name`、md 文件名跟**本仓库**的说法走：PC 后台表格叫 `table`，Taro 长列表叫 `long-list`，NutUI 仓库 `List` 就叫 `list`。禁止把别的端的 id 当通用名。

`tags`：2–5 个用户会说的词，可加真实组件名；不要把路径当 tag。同一业务 tag 尽量只出现在一张卡上（订单不要打「退款」，若待退款在资产中心）。

`use_when_not`：写易混的邻能力，不要把 `constraints` 再骂一遍。

`constraints` **只放硬约束**：项目规则写明的，或绕了会做错的技术不变量。下面这些放 md 正文并打标，不要进 `constraints`：

- 主流写法、推荐但未强制
- 历史例外（登录页、`pages` 里仍有业务）
- 待确认
- 相对时间（禁止「今天」「目前仍有效」）

JSON 根对象：

```json
{
  "id": "stack 或项目短名",
  "layer": "stack | project",
  "name": "可读名称",
  "cards": []
}
```

## 5. 哪些进库

进底座目录（两条路都摸代码，不靠入口表）：

- 页面怎么建、代码放哪（`pages` 壳 vs `_pages` 等本仓库事实）
- 列表 / 表单 / 弹窗 / 请求实际用的组件或封装名
- 明确禁止（不要绕过现有封装去用裸组件）
- 仓库若有 `src/models/`（或等价全局状态），补一张状态卡

进业务目录：

- 有表：表里的每一行（含 hideInMenu、无布局外链页）
- 没表：真实可进的页面/Tab/模块入口，不要只抄 `AGENTS.md` 的模块表
- 模块名、主路径、这条链路何时用
- 角色、核心契约（有代码或文档依据）
- 跨页流程的入口 → 出口（能指到目录即可）

不要进：

- 当前这一单需求的 F-01、验收
- 猜的接口字段
- 把 Antd / Taro 官网教程整篇搬进来

## 6. 原文怎么写

每篇 md 从入口表指出的文件（有表时）或本仓库代码抽，不复制官网。固定短结构：

```markdown
# 名称

## 何时用 / 何时不用
## 路径
## 硬约束
## 现状与例外
## 待确认
## 证据
```

`证据` 写清读了哪个文件。底座卡至少：定义文件 + 一处真实业务引用。不要虚构封装根组件（有 `_utils` 不等于有页面壳）。有表时页面实际干什么以 `page_file` / `impl_file` 为准，不要用路由 name 脑补；没表时打开入口组件看它实际挂了谁。

## 7. 怎么抽第一批

1. 先跑入口探针。有表则沿表取证；没表则摸导航/页面入口。再写 md，最后登记 JSON。
2. 底座覆盖高频能力即可：页面怎么建、实际在用的列表/表单/弹窗/请求、有 models 则状态。不要凑篇数。id 先看真实 import，再用本仓库的词。
3. 业务按**独立用户目标**拆卡。有表时不是机械「每一行一张卡」，redirect 行显式不入库。
4. 同名能力以本仓库为准。卡片 id/name 用本仓库的词。
5. 引用次数为 0 的封装不要写成推荐；可写「待确认是否废弃」。
6. 覆盖够了就停：有表则每一行都有卡或显式不入库；没表则真实可进的入口都有卡。高频底座已有、易混入口已拆开。
7. 写完验收，不通过就补：
   - JSON 能解析；id 不重复；每张卡有 md；`source` / `paths` 存在
   - 推荐组件有非零业务引用
   - 有表：业务卡入口来自 `path`，Tab/子模块以 `impl_imports` 为准
   - 没表：业务卡读过入口组件，Tab/子模块和跨模块挂载已写明
   - `constraints` 里没有推断和相对时间
8. `.knowledge/` 不要被 `.gitignore` 忽略。不暂存、不提交。

## 8. 结束时报告

- 判定的技术代际和项目一句话
- 走了哪条路：有表（`kind`、行数）还是没表（`no_routes`）
- 有表：对照表，每条 `path` → 哪张业务卡，或「不入库 + 原因」
- 没表：写了哪些入口、依据哪个导航/页面文件
- 写了哪些 md、两份目录各多少张卡
- 标了待确认的条目
- 下一步应由人过一眼：有表看对照表有没有漏行；没表看入口有没有漏；底座组件名是否指对
