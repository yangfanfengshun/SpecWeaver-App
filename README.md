# SpecWeaver

macOS 桌面控制台。在 Claude Code、Codex、Cursor 三个宿主之间开关 SpecWeaver 的
MCP 与 Skills，并集中配置 Tower、蓝湖、Eolink 的认证，不用再手工去改各宿主的配置文件。

> 面向公司内部同事。MCP 和 Skills 都是按公司产品定制的，外部拿到也用不了。

## 系统要求

- macOS 12 (Monterey) 及以上，Intel 和 Apple Silicon 都支持
- [uv](https://docs.astral.sh/uv/)：MCP 是 Python 实现的，靠 uv 启动

先确认 uv 装了没：

```bash
uv --version
```

没有的话：

```bash
brew install uv
```

**这一步不能跳过。** 缺了 uv，App 里的开关照样能开、配置也会正确写进宿主，
但宿主真正去拉起 MCP 时会失败，而且 App 界面上看不出异常。

## 安装

到 [Releases](../../releases/latest) 下载 `SpecWeaver_X.Y.Z_universal.dmg`，
双击打开，把 SpecWeaver 拖进「应用程序」。一个 dmg 通吃两种芯片，都是原生运行。

想核对下载是否完整，每个版本都附了 `.sha256` 边车文件：

```bash
shasum -a 256 -c SpecWeaver_X.Y.Z_universal.dmg.sha256
```

### 首次打开被系统拦住

应用没有做 Apple 签名公证，首次打开会被 Gatekeeper 拦下，提示往往是
**「SpecWeaver 已损坏，无法打开」**——文件没坏，这就是未签名应用的表现。

在终端执行一行即可：

```bash
xattr -dr com.apple.quarantine /Applications/SpecWeaver.app
```

或者去「系统设置 → 隐私与安全性」，找到被拦截的提示点「仍要打开」。

装新版本后要再执行一次，因为新下载的文件会带上新的 quarantine 标记。

## 使用

**首页**是 MCP 和 Skills 的开关。每一行右侧是 Codex / Claude / Cursor 三个独立开关，
按需要单独打开：

- 打开 MCP：把对应条目写进该宿主的配置文件，关闭时只移除这一条，不动你的其他配置
- 打开 Skill：软链到该宿主的 skills 目录。宿主里看到的名字统一带 `spec-` 前缀
  （比如 `spec-git-commit`），跟你已有的同名 Skill 不会互相覆盖

**设置页**配置数据源认证。Tower、蓝湖、Eolink 都支持账号密码登录，也可以直接粘 Cookie，
填完能当场验证是否有效。只用其中一两个宿主的话，「平台配置」里可以把其余的开关隐藏掉。

改完开关需要**新开一个宿主会话**才会生效。

## 卸载

删掉 `/Applications/SpecWeaver.app` 之前，建议先在 App 里把所有开关关掉，
这样投影到各宿主的配置和软链会被干净移除。

`~/.specweaver/` 目录里存着你配置的认证信息，卸载时不会自动删除。
确实要清干净的话：

```bash
rm -rf ~/.specweaver
```
