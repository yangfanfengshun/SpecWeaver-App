---
name: spec-merge
description: 在用户可见的 IDE 终端里启动合并：本地未推送则先 push 再 sw merge，已推送则只跑 sw merge。用户说“合并到 test”“帮我合并”“推送并合并”“合到 develop”“提交完帮我合一下”时使用。禁止合并到 master / main 系列主干。启动终端后不要等待、不要读输出、不要追踪流水线。
---

# 合并分支

Agent 只做两件事：看要不要推送，然后在用户看得见的 IDE 终端里启动**一条**命令。
之后本 Skill 结束。流水线由 `sw merge` 在那个终端里跟踪，用户自己看。

## 边界

- 目标分支默认 `test`，用户明确指定时可以是别的分支。
- **master / main 系列一律拒绝**（含 `master-xxx`、`master_xxx`、`main` 等变体）：
  合进主干等于上线，必须到 GitLab 网页端人工创建 MR 走审核。用户坚持时也不绕道——
  不要改用 `glab` 直调、也不要手工 `git merge` 后推送。
- 不修改代码，不解决冲突，不变基，不 `git commit --amend`，不 `--force` 推送，
  不 `git add`，不 `git pull`。
- 不在对话里执行 `sw merge`，不等待命令结束，不读取终端输出，不轮询 GitLab，
  不按退出码汇报流水线。宿主后来叫醒你，也不要去读日志或总结结果。
- 不泄露 Token、Cookie、`.env` 内容或完整的认证响应。

## 1. 要不要推送

```bash
git rev-parse --abbrev-ref HEAD
git rev-parse --verify --quiet "@{upstream}" || echo "无上游"
git rev-list --count "@{upstream}..HEAD"
```

- 当前分支就是目标分支：停下说明，不能自我合并，不要开终端。
- 没有上游，或本地领先远端：命令里要带推送。
- 其余情况：只跑 `sw merge`。
- 有未提交改动也不要问、不要暂存；它们不会进这次 push。

## 2. 在 IDE 终端里启动一条命令

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
不要提示日报或 Tower。
