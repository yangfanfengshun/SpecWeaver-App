#!/bin/sh
# SpecWeaver 安装脚本。
#
#   curl -fsSL https://raw.githubusercontent.com/yangfanfengshun/SpecWeaver-App/main/install.sh | sh
#
# 默认装最新版。要指定版本：
#
#   SPECWEAVER_VERSION=1.2.3 sh -c "$(curl -fsSL <上面那个 url>)"
#
# 走 `curl | sh` 时执行它的是 sh 而不是 bash，shebang 根本不生效，
# 所以全文只用 POSIX 语法：没有 [[ ]]、没有数组、没有 pipefail。

set -eu

REPO="yangfanfengshun/SpecWeaver-App"
APP_NAME="SpecWeaver.app"
WORK=""
MOUNT=""

say() { printf '==> %s\n' "$1"; }
die() { printf 'ERROR: %s\n' "$1" >&2; exit 1; }

cleanup() {
  # 先卸载再删目录，否则挂载点会残留占用
  if [ -n "$MOUNT" ] && [ -d "$MOUNT" ]; then
    hdiutil detach "$MOUNT" -quiet 2>/dev/null || true
  fi
  [ -n "$WORK" ] && rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

# ---------------------------------------------------------------- 环境

[ "$(uname -s)" = "Darwin" ] || die "只支持 macOS"

MACOS_MAJOR=$(sw_vers -productVersion | cut -d. -f1)
if [ "$MACOS_MAJOR" -lt 12 ]; then
  die "需要 macOS 12 (Monterey) 及以上，当前是 $(sw_vers -productVersion)"
fi

for bin in curl shasum hdiutil; do
  command -v "$bin" >/dev/null 2>&1 || die "缺少 $bin"
done

# ---------------------------------------------------------------- 版本

VERSION="${SPECWEAVER_VERSION:-}"
if [ -n "$VERSION" ]; then
  case "$VERSION" in
    [0-9]*.[0-9]*.[0-9]*) ;;
    *) die "版本号格式不对：${VERSION}" ;;
  esac
  API_URL="https://api.github.com/repos/${REPO}/releases/tags/v${VERSION}"
  say "查询 v${VERSION}"
else
  API_URL="https://api.github.com/repos/${REPO}/releases/latest"
  say "查询最新版本"
fi

# 不依赖 jq——同事机器上不一定有。先把紧凑 JSON 按逗号拆行，
# 否则 sed 的贪婪匹配会在一行里跨字段乱抓。
RELEASE_JSON=$(curl -fsSL "$API_URL" | tr ',' '\n') \
  || die "查询失败，看看有哪些版本：https://github.com/${REPO}/releases"

if [ -z "$VERSION" ]; then
  VERSION=$(printf '%s\n' "$RELEASE_JSON" \
    | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"v\([^"]*\)".*/\1/p' | head -1)
  [ -n "$VERSION" ] || die "查不到最新版本，可用 SPECWEAVER_VERSION 指定"
fi

# 产物名不写死。早期版本是 _x64.dmg，现在是 _universal.dmg，
# 以后还可能变——硬编码的话装旧版会 404，还会报出误导性的「版本不存在」。
DMG_NAME=$(printf '%s\n' "$RELEASE_JSON" \
  | sed -n 's/.*"name"[[:space:]]*:[[:space:]]*"\(SpecWeaver_[0-9][^"]*\.dmg\)".*/\1/p' \
  | head -1)
[ -n "$DMG_NAME" ] || die "v${VERSION} 里没有 dmg 安装包"

# 幂等：已经是目标版本就不折腾
for dir in /Applications "$HOME/Applications"; do
  plist="${dir}/${APP_NAME}/Contents/Info.plist"
  if [ -f "$plist" ]; then
    installed=$(/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" \
      "$plist" 2>/dev/null || echo "")
    if [ "$installed" = "$VERSION" ]; then
      say "已经是 ${VERSION}，无需重装：${dir}/${APP_NAME}"
      exit 0
    fi
  fi
done

# ---------------------------------------------------------------- 下载

BASE_URL="https://github.com/${REPO}/releases/download/v${VERSION}"
WORK=$(mktemp -d)

say "下载 ${DMG_NAME}"
curl -fL --progress-bar -o "${WORK}/${DMG_NAME}" "${BASE_URL}/${DMG_NAME}" \
  || die "下载失败：${BASE_URL}/${DMG_NAME}"
curl -fsSL -o "${WORK}/${DMG_NAME}.sha256" "${BASE_URL}/${DMG_NAME}.sha256" \
  || die "下载校验文件失败"

say "校验 sha256"
# 边车里记的是纯文件名，必须在同目录下比对
(cd "$WORK" && shasum -a 256 -c "${DMG_NAME}.sha256" >/dev/null) \
  || die "sha256 不匹配，文件可能损坏或被篡改，已中止"

# ---------------------------------------------------------------- 安装

if [ -w /Applications ]; then
  DEST="/Applications"
else
  DEST="${HOME}/Applications"
  mkdir -p "$DEST"
  say "没有 /Applications 写权限，改装到 ${DEST}"
fi

MOUNT=$(mktemp -d)
say "挂载并安装到 ${DEST}"
hdiutil attach -nobrowse -quiet "${WORK}/${DMG_NAME}" -mountpoint "$MOUNT" \
  || die "挂载 dmg 失败"

[ -d "${MOUNT}/${APP_NAME}" ] || die "dmg 里找不到 ${APP_NAME}"

# 直接 cp 到已存在的 .app 会和旧文件合并，留下上个版本的残余，所以先整个替换掉
if [ -e "${DEST}/${APP_NAME}" ]; then
  rm -rf "${DEST:?}/${APP_NAME}"
fi
cp -R "${MOUNT}/${APP_NAME}" "${DEST}/"

hdiutil detach "$MOUNT" -quiet
MOUNT=""

# 应用未做签名公证。不剥掉 quarantine 的话，首次打开会被 Gatekeeper 拦下并提示
# 「已损坏」——文件没坏，只是未签名的表现。
xattr -dr com.apple.quarantine "${DEST}/${APP_NAME}" 2>/dev/null || true

# ---------------------------------------------------------------- 命令行

# sw 随 App 一起分发，装完就能用。指向 .app 内的脚本而不是 ~/.specweaver/，
# 后者要等 App 首次启动同步完才存在。
SW_SRC="${DEST}/${APP_NAME}/Contents/Resources/runtime/scripts/sw"
if [ -f "$SW_SRC" ]; then
  chmod +x "$SW_SRC" 2>/dev/null || true

  # 优先用系统目录，没权限就退到用户级，不为了一个软链去要 sudo
  if [ -d /usr/local/bin ] && [ -w /usr/local/bin ]; then
    LINK_DIR="/usr/local/bin"
  else
    LINK_DIR="${HOME}/.local/bin"
    mkdir -p "$LINK_DIR"
  fi

  if [ -e "${LINK_DIR}/sw" ] \
    && [ "$(readlink "${LINK_DIR}/sw" 2>/dev/null || echo)" != "$SW_SRC" ]; then
    say "${LINK_DIR}/sw 已被别的程序占用，跳过；需要的话手动改名后重装"
  else
    ln -sf "$SW_SRC" "${LINK_DIR}/sw"
    say "命令行入口：${LINK_DIR}/sw"
    case ":${PATH}:" in
      *":${LINK_DIR}:"*) ;;
      *) printf '   %s 不在 PATH 里，加一行到 ~/.zshrc：\n' "$LINK_DIR"
         printf '   export PATH="%s:$PATH"\n' "$LINK_DIR" ;;
    esac
  fi
fi

# ---------------------------------------------------------------- 收尾

say "安装完成：${DEST}/${APP_NAME}"

SW_MISSING=""
command -v glab >/dev/null 2>&1 || SW_MISSING="glab"
command -v jq >/dev/null 2>&1 || SW_MISSING="${SW_MISSING:+${SW_MISSING} }jq"
if [ -n "$SW_MISSING" ]; then
  printf '\n'
  printf '提示：sw merge 需要 %s，装一下：brew install %s\n' "$SW_MISSING" "$SW_MISSING"
  printf '   （只影响命令行合并，App 本身的功能不受影响）\n'
fi

if ! command -v uv >/dev/null 2>&1 \
  && [ ! -x "${HOME}/.local/bin/uv" ] \
  && [ ! -x /opt/homebrew/bin/uv ] \
  && [ ! -x /usr/local/bin/uv ]; then
  printf '\n'
  printf '⚠️  没有检测到 uv。MCP 是 Python 实现的，靠它启动。\n'
  printf '   缺了它，App 里的开关照样能开、配置也会写进宿主，\n'
  printf '   但宿主真正拉起 MCP 时会失败，而且界面上看不出异常。\n'
  printf '\n'
  printf '   装一下：brew install uv\n'
  printf '   或者：  curl -LsSf https://astral.sh/uv/install.sh | sh\n'
fi

printf '\n打开应用：open "%s/%s"\n' "$DEST" "$APP_NAME"
