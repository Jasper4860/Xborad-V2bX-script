#!/usr/bin/env bash
# Run this launcher from a trusted local checkout, never from a mutable remote pipe.
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
umask 077

if [[ ${1:-} == --help || ${1:-} == -h ]]; then
    cat <<'EOF'
用法（可信本地 Git 工作副本）：
  sudo bash ./install.sh v0.4.0 --scripts-rev <40位提交SHA> [--sha256 <64位SHA-256>] [--start]
  sudo bash ./install.sh --scripts-only --scripts-rev <40位提交SHA>
需要 bash、python3、git；核心下载还需要 curl 和系统 CA 证书。
核心版本必须明确指定。已有服务仅在原本运行或显式 --start 时启动。
EOF
    exit 0
fi
if [[ $EUID -ne 0 ]]; then
    echo '错误：必须以 root 运行。' >&2
    exit 1
fi
for dependency in python3 git; do
    if ! command -v "$dependency" >/dev/null; then
        echo "错误：请先通过系统软件包管理器安装 $dependency。" >&2
        exit 1
    fi
done

revision=''
args=("$@")
for ((i=0; i<${#args[@]}; i++)); do
    case "${args[i]}" in
        --scripts-rev)
            if [[ -n "$revision" || $((i+1)) -ge ${#args[@]} ]]; then
                echo '错误：请指定一次 --scripts-rev <完整提交 SHA>。' >&2
                exit 1
            fi
            revision="${args[i+1]}"
            ((i+=1))
            ;;
        --source|--source=*|--core-only|--)
            echo '错误：此启动器不接受该选项。' >&2
            exit 1
            ;;
    esac
done
if [[ ! "$revision" =~ ^[[:xdigit:]]{40}$ ]]; then
    echo '错误：请从可信本地 Git 工作副本运行，并指定 --scripts-rev <40 位提交 SHA>。' >&2
    exit 1
fi
if [[ ! -f "${BASH_SOURCE[0]}" ]]; then
    echo '错误：不支持管道或进程替换安装；请使用可信本地 Git 工作副本。' >&2
    exit 1
fi
source_dir="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
resolved="$(git --no-replace-objects -C "$source_dir" rev-parse --verify "${revision}^{commit}")"
if [[ "$resolved" != "${revision,,}" ]]; then
    echo '错误：--scripts-rev 必须是提交对象本身的完整 SHA。' >&2
    exit 1
fi
entry="$(git --no-replace-objects -C "$source_dir" ls-tree "$resolved" -- tools/install_manager.py)"
if [[ "$entry" != '100644 blob '*$'\ttools/install_manager.py' && "$entry" != '100755 blob '*$'\ttools/install_manager.py' ]]; then
    echo '错误：固定提交不含可用的安装管理器。' >&2
    exit 1
fi
tmp="$(mktemp -d /tmp/v2bx-launch.XXXXXXXX)"
trap 'rm -rf -- "$tmp"' EXIT
git --no-replace-objects -C "$source_dir" show "$resolved:tools/install_manager.py" > "$tmp/install_manager.py"
python3 -I "$tmp/install_manager.py" --source "$source_dir" "$@"
