---
name: spec-git-release
description: 仅 GitLab。用户说「上线」「灰度」「上灰度」「发灰度」「发上线」「开上线 MR」「开灰度 MR」「合并到 master」「合并到主干」「合到 master」时使用。只开 MR，不合并。上线和合并到主干用 sw release online，灰度用 sw release gray。禁止 sw merge、禁止 glab mr merge、禁止本机 merge 后推主干。GitHub 仓不要用。合测试线走 spec-git-merge，解 test 冲突走 spec-git-conflict。
---

# 上线与灰度（GitLab）

Agent 只做这些事：确认是 GitLab、解析目标分支、看要不要推送，然后在对话里执行**一条**命令。
命令一返回就把 `MR !编号  链接` 告诉用户。之后本 Skill 结束。不合并，不看流水线。

## 边界

- **只适用于 GitLab。** 先检查远端，GitHub 或看不出是 GitLab 时停下说明，不要改走 `gh` / PR。
- 用户说「四件套」或只说合到 test 时不要从这里开始。测试线合并走 `spec-git-merge`。
- 上线、发上线、合并到 master、合并到主干、合到 master：模式用 `online`。
- 灰度、上灰度、发灰度：模式用 `gray`。
- 不调用 `sw merge`，不执行合并，不在本机 `git merge` 后推主干。冲突留在 MR 上，不转 `spec-git-conflict`（那条只解 test 线）。
- 不修改代码，不解决冲突，不变基，不 `git commit --amend`，不 `--force` 推送，
  不 `git add`，不 `git pull`。
- 直接在对话里执行 `sw release`，等它返回，把结果里的 MR 链接交给用户。
  这条命令只发一次建 MR 的请求就结束，耗时和普通命令一样，不存在要盯着看的长时间输出。
- 不轮询 GitLab，不查流水线，不按退出码决定是否继续追问。MR 建好就停。
- 调 GitLab 一律走 `sw release`。不要 `glab auth login`，不要读、不要 `source` `~/.specweaver/.env`。
  指派人由命令自己决定，Skill 不读 `GITLAB_ASSIGNEE`。
- 不泄露 Token、Cookie、`.env` 内容或完整的认证响应。

## 0. 是不是 GitLab

```bash
git remote get-url origin
```

- URL 含 `github.com`：停下，说明本 Skill 只适用于 GitLab。
- URL 含 `gitlab`：继续。
- 其它：跑 `sw glab repo view`；成功则当 GitLab，失败则停下，不要改走 `gh`。

## 1. 目标分支

```bash
git rev-parse --show-toplevel
git rev-parse --abbrev-ref HEAD
```

读仓库根 `.specweaver.yml`。没有文件、文件是空的、或没写 `master`：目标用 `master`。
写了 `master`：用那个值（例如 `master: master_yz`）。不要读 `test`、`slug`、`aliases`，也不要从当前 HEAD 猜。

用户这次给了完整目标分支名则用用户的。执行命令时始终带上 `--target <这个名字>`。

## 2. 标题

用户这次给了标题就原样用，后缀用户自己带，命令行加上 `--title`。
没给则不要自己编标题，留给 `sw release` 取相对目标分支最早一条提交。

## 3. 指派人

用户这次点了名，把那个字符串原样传 `--assignee`，用户名或中文姓名都可以。
`sw release` 会把不像用户名的名字按姓名查成 GitLab username：唯一才用，没有或重名就停下。
Skill 自己不查、不翻译。没点名就不要传 `--assignee`，也不要读 `.env`。

## 4. 要不要推送

```bash
git rev-parse --abbrev-ref HEAD
git rev-parse --verify --quiet "@{upstream}" || echo "无上游"
git rev-list --count "@{upstream}..HEAD"
```

- 当前分支就是目标分支：停下说明，不能对自己开 MR。
- 没有上游，或本地领先远端：命令里要带推送。
- 其余情况：只跑 `sw release`。
- 有未提交改动也不要问、不要暂存；它们不会进这次 push。

## 5. 在对话里执行一条命令

自己执行，不要开终端、不要丢给用户。这条命令只建 MR，几条 API 请求就返回。

未推送：

```bash
git push -u origin <源分支> && sw release <online|gray> --target <目标>
```

已推送：

```bash
sw release <online|gray> --target <目标>
```

用户给了标题就追加 `--title <标题>`。用户点了名就追加 `--assignee <用户名>`。
源分支不是当前分支时，把源分支放在模式后面：`sw release online <分支> --target <目标>`。

`sw` 不存在时说明它随 SpecWeaver App 分发；缺 `glab` 或 `jq` 时让用户看命令自己的提示，
不要改去直调 GitLab API。

成功时命令末尾会打一行 `MR !编号  链接`，把它原样转给用户。命令报错就把命令自己的
提示转述一遍，不要重试、不要换别的写法再试一次。
