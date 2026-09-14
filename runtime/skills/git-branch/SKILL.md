---
name: spec-git-branch
description: 仅 GitLab。从 Issue 拉开发分支：先建 Issue（或用已有 Issue），远端从该线的 master 只建分支不建 MR，再本机 fetch 后 checkout。用户说“开分支”“拉分支”“建 issue 拉分支”“从 issue 拉个分支”“新建开发分支”时使用。标题必须带 -yf；有 .specweaver.yml 时还要带对应 slug。不提交、不 push、不合并。GitHub 仓不要用；合并走 spec-git-merge，冲突走 spec-git-conflict。
---

# 从 Issue 拉分支（GitLab）

对齐网页：先建 Issue，再 Create branch（不是 Create merge request and branch）。

## 边界

- **只适用于 GitLab。** GitHub 或看不出是 GitLab 时停下，不要改走 `gh`。
- 只建 Issue 和远端分支，然后本机 fetch / checkout。不提交、不 push、不合、不建 MR。
- 不要用 `sw glab mr for` / `sw glab mr create --related-issue`（那是分支+MR）。
- 源分支默认 `master`。仓库根有 `.specweaver.yml` 且写了 `master`，就用文件里的名字替换，不要从当前 HEAD 拉。用户明确指定源时用用户的。
- 工作区不干净则停下，不 stash、不把别人的改动捎进新分支。
- 调 GitLab API 一律 `sw glab ...`，不要直接 `glab`。不要 `glab auth login`，不要读、不要 `source` `~/.specweaver/.env`。
- 不泄露 Token、Cookie、`.env` 或完整认证响应。缺 `sw` 时说明它随 SpecWeaver App 分发；缺 `glab` 或提示 GitLab 尚未配置时，让用户看命令提示去 App 设置页。

## 0. 是不是 GitLab

```bash
git remote get-url origin
git status --porcelain
```

- URL 含 `github.com`：停下，说明本 Skill 只适用于 GitLab。
- URL 含 `gitlab`：继续。
- 其它：`sw glab repo view` 成功则继续，否则停下。
- `git status --porcelain` 非空：停下，先让用户处理未提交改动。

## 1. master / test 映射

```bash
git rev-parse --show-toplevel
```

读仓库根 `.specweaver.yml`。没有文件或是空的：源分支 `master`，标题不加 slug。
有内容就当替换表，**不要挑选、不要猜**：

- 写了 `master`：拉分支的源用这个名字，不再用 `master`
- 写了 `test`：本 Skill 不用它；合并/冲突走文件里的 `test`
- 写了 `slug`：Issue 标题带上它
- `aliases` 只是给人看的，不参与选择

```yaml
aliases: [驿站微信, 驿站-微信]
master: master_yz
test: test_yz
slug: weapp-yz
```

## 2. Issue 标题

新建 Issue 时标题由三截拼成：`{topic}-{slug}-yf`（无 slug 则 `{topic}-yf`）。
`{iid}-` 由 GitLab 建分支时自动加，不要写进标题。

1. 用户给 topic（或整段标题）。已有 Issue 编号/链接则跳过本节，用 Issue 现成标题，不改名。
2. 去掉末尾已有的 `-yf`（避免重复）。
3. 文件里写了 `slug`，且标题里还没有这段连续 slug：在末尾加上 `-{slug}`。
4. 加上 `-yf`。这是开发者标记，不能省。
5. 描述、标签、指派人不问、不填，除非用户写了。

## 3. 建 Issue 和远端分支

已有 Issue：

```bash
sw glab issue view <iid> --output json
```

记下 `iid` 和标题，不要再 `issue create`。

新建：

```bash
sw glab issue create --title "<标题>" --description "" --no-editor --yes
```

从输出或 `sw glab issue list --search "<标题>"` 拿到 `iid`。不要打开编辑器。

远端只建分支（`ref` 是第 1 节的源分支，名字 `{iid}-{标题}`，标题已是短横线形式则不要再 slug 一遍）：

```bash
sw glab api -X POST "projects/:id/repository/branches" \
  -f "branch=<iid>-<标题>" \
  -f "ref=<源分支>"
```

分支已存在则不要报成失败，继续 checkout。不要用 `sw glab mr for`。

## 4. 本机 checkout

```bash
git fetch origin <分支名>
git checkout <分支名>
```

本地已有同名分支：checkout 后 `git merge --ff-only origin/<分支名>`。不能 fast-forward 则停下问，不要 `--hard` 清掉。

跟用户报告：Issue 编号、分支名、源分支。不要接着提交或合并。
