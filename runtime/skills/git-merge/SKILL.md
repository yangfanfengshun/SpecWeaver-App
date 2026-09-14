---
name: spec-git-merge
description: 仅 GitLab。在用户可见的 IDE 终端里启动合并：本地未推送则先 push 再 sw merge，已推送则只跑 sw merge。用户说“合并到 test”“帮我合并”“推送并合并”“合到 develop”“提交完帮我合一下”时使用。禁止合并到 master / main 系列主干。启动终端后不要等待、不要读输出、不要追踪流水线。冲突了或用户说冲突了，转交 spec-git-conflict，本 Skill 不解冲突。GitHub 仓不要用；开分支走 spec-git-branch，提交走 spec-git-commit。
---

# 合并分支（GitLab）

Agent 只做两件事：看要不要推送，然后在用户看得见的 IDE 终端里启动**一条**命令。
之后本 Skill 结束。流水线由 `sw merge` 在那个终端里跟踪，用户自己看。

## 边界

- **只适用于 GitLab。** 先检查远端，GitHub 或看不出是 GitLab 时停下说明，不要改走 `gh` / PR。
- 目标分支默认是当前线的 test（有 `.specweaver.yml` 且能唯一命中 flavor 时用它的 `test`，
  例如 `test_yz`；否则 `test`）。用户给出完整分支名时用用户的。
- 用户说「合到 test」且已唯一命中 flavor：目标是 **flavor.test**，不是字面分支名 `test`。
- **master / main 系列一律拒绝**（含 `master-xxx`、`master_xxx`、`main` 等变体，
  包括 `master_yz`）：合进主干等于上线，必须到 GitLab 网页端人工创建 MR 走审核。
  用户坚持时也不绕道——不要改用 `glab` 直调、也不要手工 `git merge` 后推送。
- 不修改代码，不解决冲突，不变基，不 `git commit --amend`，不 `--force` 推送，
  不 `git add`，不 `git pull`。冲突了或用户说冲突了，转交 `spec-git-conflict`，然后结束。
- 不在对话里执行 `sw merge`，不等待命令结束，不读取终端输出，不轮询 GitLab，
  不按退出码汇报流水线。宿主后来叫醒你，也不要去读日志或总结结果。
- 不泄露 Token、Cookie、`.env` 内容或完整的认证响应。

## 0. 是不是 GitLab

```bash
git remote get-url origin
```

- URL 含 `github.com`：停下，说明本 Skill 只适用于 GitLab。
- URL 含 `gitlab`：继续。
- 其它：跑 `glab repo view`；成功则当 GitLab，失败则停下，不要改走 `gh`。

## 1. 目标分支

```bash
git rev-parse --show-toplevel
git rev-parse --abbrev-ref HEAD
```

读仓库根 `.specweaver.yml`（没有就当传统 `master` / `test`）：

1. 当前分支整串等于某条 `master` 或 `test` → 那条 flavor。
2. 否则在分支名里找 `-{slug}-`，**最长 slug 先中**。
3. 否则用户原话整串等于某个 `type` 或 `aliases`。
4. 对不上或对上多条：默认目标 `test`；有多条 flavor 时列出表来问，不准猜成 `weapp`。

命中 flavor 后，默认 `--target` 用它的 `test`。用户指定了完整分支名则覆盖。

## 2. 要不要推送

```bash
git rev-parse --abbrev-ref HEAD
git rev-parse --verify --quiet "@{upstream}" || echo "无上游"
git rev-list --count "@{upstream}..HEAD"
```

- 当前分支就是目标分支：停下说明，不能自我合并，不要开终端。
- 没有上游，或本地领先远端：命令里要带推送。
- 其余情况：只跑 `sw merge`。
- 有未提交改动也不要问、不要暂存；它们不会进这次 push。

## 3. 在 IDE 终端里启动一条命令

把下面**整行**丢进用户可见的 IDE 终端并执行，然后立刻结束（Cursor 用后台终端、
不要 await；其他宿主用等价的「用户能看见、不要等待」的启动方式）。

未推送：

```bash
git push -u origin <当前分支> && sw merge
```

已推送：

```bash
sw merge
```

换目标分支时在 `sw merge` 后加 `--target <分支>`。指定源分支时用
`sw merge <分支>`。master / main 系列会被命令再拒绝一次，不要试图绕过。

`sw` 不存在时说明它随 SpecWeaver App 分发；缺 `glab` 或 `jq` 时让用户看命令自己的提示，
不要改去直调 GitLab API。

跟用户只说一句：命令已在终端启动，结果看终端。不要复述计划，不要报 MR，
不要提示日报或 Tower。终端里若是冲突导致合并不了，等用户再说冲突时转交 `spec-git-conflict`，
不要自己去读终端输出。
