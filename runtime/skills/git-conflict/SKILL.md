---
name: spec-git-conflict
description: 仅 GitLab。合到 test 线撞冲突时在本机解：fetch、checkout 该线 test、merge --no-ff 开发分支，冲突列出后问用户谁来改，解完再 push test。用户说“冲突了”“解冲突”“合失败了”“合并有冲突”时使用。不要在 GitLab MR 上点。无冲突的合并走 spec-git-merge。GitHub 仓不要用；不合 master / main 系列。
---

# 解冲突（GitLab）

合到 test 线走本机 `merge --no-ff`，不在网页 MR 上点。无冲突合并不要走这里，转交 `spec-git-merge`。

## 边界

- **只适用于 GitLab。** GitHub 或看不出是 GitLab 时停下，不要改走 `gh`。
- 目标是当前线的 test（命中 flavor 用它的 `test`，否则 `test`）。
- **master / main 系列一律拒绝**（含 `master_yz` 等变体）。用户坚持也不绕道。
- `git merge --no-ff` 爆冲突之后**先问用户谁来改**（用户自己 / Agent），没点头不动冲突文件。
- 不盯流水线、不读 GitLab、不 `--force` 推送、不合主干。
- 不泄露 Token、Cookie、`.env` 或完整认证响应。

## 0. 是不是 GitLab

```bash
git remote get-url origin
```

- URL 含 `github.com`：停下，说明本 Skill 只适用于 GitLab。
- URL 含 `gitlab`：继续。
- 其它：`glab repo view` 成功则继续，否则停下。

## 1. 开发分支和 test 线

```bash
git rev-parse --abbrev-ref HEAD
git rev-parse --show-toplevel
```

记下当前分支为**开发分支**（若用户指定了源分支，用用户的）。当前已经在目标 test 上：停下，不能自我合并。

读仓库根 `.specweaver.yml`（没有则目标 `test`）：

1. 开发分支整串等于某条 `master` 或 `test` → 那条 flavor。
2. 否则在开发分支名里找 `-{slug}-`，**最长 slug 先中**。
3. 否则用户原话整串等于某个 `type` 或 `aliases`。
4. 对不上或对上多条：问用户合进哪条 test，列出表，不准猜。

命中后目标是 flavor.test。不要读 `taro-ci.config.js`。

## 2. 本机合进 test

```bash
git fetch origin
git checkout <test 线>
git merge --ff-only origin/<test 线>
git merge --no-ff <开发分支>
```

本地 test 不能 fast-forward 到 `origin/<test 线>`：停下说明，不要 `reset --hard`。

- **没有冲突**：完成这次 merge 后 `git push origin <test 线>`，告诉用户没有冲突、已推送，流水线看远端。不要再开 `sw merge`。
- **有冲突**：列出冲突文件，问「这次你自己改，还是我改？」。没回答之前不改文件、不 `git add`、不 abort，除非用户要求放弃。

## 3. 谁来改

用户自己改：把冲突文件路径给他，等他说「改完了」「继续」再往下。不要催着读终端以外的文件内容。

Agent 改：只处理列出的冲突文件，按两边意图合并，不顺手改无关代码。解不开就停下问，不要随便选一边覆盖。

## 4. 收尾并 push

```bash
git add <已解的冲突文件>
git diff --name-only --diff-filter=U
```

还有未解文件：不要 commit，继续第 3 节。

全部解开后：

```bash
git commit --no-edit
git push origin <test 线>
```

不要 `--force`。跟用户说已推到 `<test 线>`，流水线远端自己跑，不要 `glab ci`、不要等。
