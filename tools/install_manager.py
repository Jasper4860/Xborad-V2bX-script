#!/usr/bin/env python3
"""Install verified V2bX release bytes without overwriting live configuration."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import zipfile


UPSTREAM = "https://github.com/wyx2685/V2bX"
RELEASE_API = "https://api.github.com/repos/wyx2685/V2bX/releases/tags"
INSTALL_DIR = "usr/local/V2bX"
CONFIG_DIR = "etc/V2bX"
SYSTEMD_UNIT = "etc/systemd/system/V2bX.service"
OPENRC_UNIT = "etc/init.d/V2bX"
HOOK = "etc/letsencrypt/renewal-hooks/deploy/95-v2bx-native-restart"
LEGACY_HOOK_SHA256 = "ae7fb8b1d83998b06b3e5b067a2b9b22df1d7ed6a0df43dc95cbf91fe3509b7c"
SCRIPTS = {
    "install.sh": (f"{INSTALL_DIR}/install.sh", 0o755),
    "V2bX.sh": ("usr/bin/V2bX", 0o755),
    "initconfig.sh": (f"{INSTALL_DIR}/initconfig.sh", 0o644),
    "V2bX.service": (None, 0o644),
    "tools/install_manager.py": (f"{INSTALL_DIR}/install_manager.py", 0o644),
    "tools/anytls_manager.py": (f"{INSTALL_DIR}/anytls_manager.py", 0o644),
    "tools/acme_cert_manager.py": (f"{INSTALL_DIR}/acme_cert_manager.py", 0o644),
    "tools/hy2_pin_manager.py": (f"{INSTALL_DIR}/hy2_pin_manager.py", 0o644),
    "tools/certbot-v2bx-deploy-hook.sh": (HOOK, 0o755),
}
CONFIG_FILES = (
    "config.json", "dns.json", "route.json", "custom_outbound.json",
    "custom_inbound.json", "geoip.dat", "geosite.dat",
)
GEO_FILES = ("geoip.dat", "geosite.dat", "geoip.db", "geosite.db")
OPENRC = """#!/sbin/openrc-run
description="V2bX"
command="/usr/local/V2bX/V2bX"
command_args="server"
command_user="root"
directory="/usr/local/V2bX"
pidfile="/run/V2bX.pid"
command_background="yes"
depend() {
    need net
}
"""
ARCHES = {"x86_64": "64", "amd64": "64", "aarch64": "arm64-v8a",
          "arm64": "arm64-v8a", "s390x": "s390x"}
TAG_RE = re.compile(r"v?[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9._-]+)?")
SHA_RE = re.compile(r"[0-9a-fA-F]{64}")
COMMIT_RE = re.compile(r"[0-9a-fA-F]{40}")


class InstallError(RuntimeError):
    pass


def command(*args, **kwargs):
    try:
        return subprocess.run(args, check=True, timeout=30, **kwargs)
    except (OSError, subprocess.SubprocessError) as exc:
        raise InstallError(f"{args[0]} failed: {exc}") from exc


def download(url, out):
    # -q must be first to ignore ~/.curlrc (e.g. a local insecure setting).
    args = ("curl", "-q", "--fail", "--silent", "--show-error", "--location",
            "--proto", "=https", "--proto-redir", "=https", "--max-redirs", "5",
            "--retry", "2", "--connect-timeout", "12", "--max-time", "180",
            "--output", str(out), "--", url)
    try:
        subprocess.run(args, check=True, timeout=550)
    except (OSError, subprocess.SubprocessError) as exc:
        raise InstallError(f"download failed ({url}): {exc}") from exc


def file_hash(path):
    with path.open("rb") as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(128 * 1024), b""):
            digest.update(chunk)
        return digest.hexdigest()


def release_url(tag, asset):
    return f"{UPSTREAM}/releases/download/{tag}/{asset}"


def fetch_core(work, tag, arch, sha256):
    asset = f"V2bX-linux-{arch}.zip"
    archive = work / "release.zip"
    url = release_url(tag, asset)
    size = None
    if sha256 is None:
        metadata = work / "release.json"
        download(f"{RELEASE_API}/{tag}", metadata)
        try:
            release = json.loads(metadata.read_text(encoding="utf-8"))
            assets = [candidate for candidate in release["assets"] if candidate["name"] == asset]
            if release["tag_name"] != tag or release.get("draft") or len(assets) != 1:
                raise ValueError("release/tag/asset did not match")
            entry = assets[0]
            if entry["browser_download_url"] != url or entry["state"] != "uploaded":
                raise ValueError("unexpected asset URL or state")
            prefix, sha256 = entry["digest"].split(":", 1)
            size = entry["size"]
            if prefix != "sha256" or not SHA_RE.fullmatch(sha256) or type(size) is not int or size <= 0:
                raise ValueError("invalid digest or size")
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise InstallError("GitHub API 缺少匹配版本的可信 SHA-256；请使用独立核验的 --sha256") from exc
    download(url, archive)
    if (size is not None and archive.stat().st_size != size) or file_hash(archive) != sha256.lower():
        raise InstallError(f"{asset} SHA-256 / size mismatch；现有安装未改动")
    return extract_core(archive, work)


def extract_core(path, work):
    required = {"V2bX", *CONFIG_FILES}
    wanted = required.union(GEO_FILES)
    files = {}
    try:
        with zipfile.ZipFile(path) as archive:
            seen = set()
            for entry in archive.infolist():
                name = entry.filename
                pure = PurePosixPath(name)
                mode = (entry.external_attr >> 16) & 0o170000
                if (not name or pure.is_absolute() or ".." in pure.parts or "\\" in name
                        or name in seen or mode not in (0, stat.S_IFDIR, stat.S_IFREG)):
                    raise ValueError(f"invalid ZIP member: {name!r}")
                seen.add(name)
                if name not in wanted:
                    continue
                if entry.is_dir() or not 0 < entry.file_size <= 256 * 1024 * 1024:
                    raise ValueError(f"invalid ZIP file: {name!r}")
                file = work / name
                with archive.open(entry) as source, file.open("xb") as dest:
                    shutil.copyfileobj(source, dest)
                files[name] = file
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
        raise InstallError(f"invalid release archive: {exc}") from exc
    missing = required - files.keys()
    if missing:
        raise InstallError(f"release archive missing: {', '.join(sorted(missing))}")
    files["V2bX"].chmod(0o700)
    return files


def git_command(source, *args):
    return command("git", "--no-replace-objects", "-C", str(source), *args,
                   stdout=subprocess.PIPE).stdout


def stage_scripts(work, source, revision):
    if not source or not revision or not COMMIT_RE.fullmatch(revision):
        raise InstallError("请提供可信 Git 工作副本和 40 位 commit SHA (--scripts-rev)")
    commit = git_command(source, "rev-parse", "--verify", f"{revision}^{{commit}}").decode().strip()
    if commit != revision.lower():
        raise InstallError("--scripts-rev 必须是提交对象本身的完整 commit SHA")
    found = {}
    for rel in SCRIPTS:
        entry = git_command(source, "ls-tree", commit, "--", rel).decode().strip()
        if not entry.startswith(("100644 blob ", "100755 blob ")) or not entry.endswith("\t" + rel):
            raise InstallError(f"固定提交缺少常规文件: {rel}")
        file = work / Path(rel).name
        file.write_bytes(git_command(source, "show", f"{commit}:{rel}"))
        if not file.stat().st_size:
            raise InstallError(f"固定提交文件为空: {rel}")
        if file.suffix == ".py":
            compile(file.read_bytes(), rel, "exec")
        elif file.suffix == ".sh":
            command("bash" if rel != "tools/certbot-v2bx-deploy-hook.sh" else "sh", "-n", str(file))
        found[rel] = file
    return found


def exists(path):
    return path.exists() or path.is_symlink()


class FileChanges:
    """Pre-stage siblings, keeping old files available for in-process rollback."""
    def __init__(self):
        self.changes = []
        self.dirs = []
        self.scratch = []
        self.keep_backups = False

    def mkdir(self, parent):
        if any(path.is_symlink() for path in (parent, *parent.parents)):
            raise InstallError(f"拒绝写入符号链接目录树: {parent}")
        if parent.is_dir():
            return
        if exists(parent):
            raise InstallError(f"不是目录: {parent}")
        self.mkdir(parent.parent)
        parent.mkdir(mode=0o755)
        self.dirs.append(parent)

    def add(self, target, source=None, mode=0o644, link=None):
        self.mkdir(target.parent)
        if exists(target) and (target.is_symlink() or not target.is_file()):
            raise InstallError(f"拒绝替换非普通文件: {target}")
        sibling = Path(tempfile.mkdtemp(prefix=".v2bx-install-", dir=target.parent))
        self.scratch.append(sibling)
        incoming = sibling / "new"
        backup = sibling / "old"
        if link is None:
            shutil.copyfile(source, incoming)
            incoming.chmod(mode)
        else:
            incoming.symlink_to(link)
        if exists(target):
            os.link(target, backup)
        self.changes.append([target, incoming, backup, False])
        return incoming

    def apply(self):
        for item in self.changes:
            target, incoming, _, _ = item
            # Mark before replace so an interrupt immediately after replace is recoverable.
            item[3] = True
            os.replace(incoming, target)

    def restore(self):
        issues = []
        for target, incoming, backup, applied in reversed(self.changes):
            if not applied or exists(incoming):
                continue
            try:
                if backup.exists():
                    os.replace(backup, target)
                else:
                    target.unlink(missing_ok=True)
            except OSError as exc:
                issues.append(f"{target} ({backup}): {exc}")
        self.keep_backups = bool(issues)
        return issues

    def cleanup(self, rolled_back=False):
        if not self.keep_backups:
            for directory in reversed(self.scratch):
                shutil.rmtree(directory, ignore_errors=True)
        if rolled_back:
            for directory in reversed(self.dirs):
                try:
                    directory.rmdir()
                except OSError:
                    pass


class Service:
    def __init__(self, openrc=False, binary=Path("/usr/local/V2bX/V2bX")):
        self.openrc = openrc
        self.binary = binary
        self.target = "V2bX" if openrc else "V2bX.service"

    def snapshot(self):
        if self.openrc:
            try:
                proc = subprocess.run(("rc-service", self.target, "status"), timeout=15,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            except (OSError, subprocess.SubprocessError) as exc:
                raise InstallError(f"OpenRC status failed: {exc}") from exc
            if proc.returncode == 0:
                pid = int(Path("/run/V2bX.pid").read_text().strip())
                return ("active", pid, 0)
            if proc.returncode == 3:
                return ("inactive", 0, 0)
            if proc.returncode == 1 and "crashed" in proc.stdout.lower():
                return ("failed", 0, 0)
            raise InstallError(f"无法确认 OpenRC 服务状态: {proc.stdout.strip()}")
        output = command("systemctl", "show", "--property=ActiveState,MainPID,NRestarts",
                         self.target, stdout=subprocess.PIPE, text=True).stdout.splitlines()
        fields = dict(line.split("=", 1) for line in output if "=" in line)
        try:
            return fields["ActiveState"], int(fields.get("MainPID", "0")), int(fields.get("NRestarts", "0"))
        except (KeyError, ValueError) as exc:
            raise InstallError("无法确认 systemd 服务状态") from exc

    def is_active(self):
        state, _, _ = self.snapshot()
        if state not in ("active", "inactive", "failed"):
            raise InstallError(f"V2bX 服务正在过渡 ({state})，请稍后重试")
        return state == "active"

    def reload(self):
        if not self.openrc:
            command("systemctl", "daemon-reload")

    def start(self, restart):
        action = "restart" if restart else "start"
        if self.openrc:
            command("rc-service", self.target, action)
        else:
            command("systemctl", action, self.target)

    def stop(self):
        if self.openrc:
            command("rc-service", self.target, "stop")
        else:
            command("systemctl", "stop", self.target)

    def verify(self):
        # Longer than the shipped systemd unit's RestartSec=10. A new PID or
        # scheduled restart is a failure, not proof that a process is healthy.
        start = time.monotonic()
        previous = None
        while True:
            state, pid, restarts = self.snapshot()
            if (state != "active" or pid <= 0 or restarts != 0
                    or (previous is not None and previous != pid)):
                raise InstallError("V2bX 未保持运行 (service state/PID/restarts)")
            try:
                if not self.binary.samefile(f"/proc/{pid}/exe"):
                    raise InstallError("V2bX 服务运行的是不同的核心文件")
            except OSError as exc:
                raise InstallError("无法验证 V2bX 主进程的实际可执行文件") from exc
            if time.monotonic() - start >= 12:
                return
            previous = pid
            time.sleep(1)


@contextmanager
def installer_lock(root):
    path = root / "run/lock"
    path.mkdir(parents=True, exist_ok=True)
    fd = os.open(path / "v2bx-installer.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise InstallError("另一个 V2bX 安装/更新正在运行") from exc
        yield
    finally:
        os.close(fd)


class Installer:
    def __init__(self, root, service, arch, openrc=False):
        self.root = Path(root)
        self.service = service
        self.arch = arch
        self.unit = self.root / (OPENRC_UNIT if openrc else SYSTEMD_UNIT)
        self.openrc = openrc

    def run(self, tag=None, *, source=None, revision=None, sha256=None,
            core_only=False, scripts_only=False, start=False):
        if scripts_only and (tag or sha256 or start or core_only):
            raise InstallError("--scripts-only 不更新或启动核心")
        if not scripts_only and (not tag or not TAG_RE.fullmatch(tag)):
            raise InstallError("请指定明确的上游 release tag，例如 v0.4.0；不自动下载最新版")
        if sha256 is not None and not SHA_RE.fullmatch(sha256):
            raise InstallError("--sha256 必须是 64 位十六进制 SHA-256")
        if not scripts_only and self.arch not in ARCHES.values():
            raise InstallError("不支持此 CPU 架构；不回退到 x86-64")
        if not core_only and (not source or not revision or not COMMIT_RE.fullmatch(revision)):
            raise InstallError("请提供可信 Git 工作副本和 40 位 commit SHA (--scripts-rev)")
        if core_only and (source or revision):
            raise InstallError("--core-only 不更新管理脚本")
        with installer_lock(self.root), tempfile.TemporaryDirectory(prefix="v2bx-stage-") as tmp:
            work = Path(tmp)
            binary = self.root / INSTALL_DIR / "V2bX"
            existing = exists(binary)
            if (core_only or scripts_only) and (not existing or not exists(self.unit)):
                raise InstallError("现有核心或服务定义不存在；请从固定提交执行完整安装")
            if existing and not exists(self.unit):
                raise InstallError("已有核心但找不到 V2bX 服务定义；请先检查本机服务配置")
            if not existing and exists(self.unit):
                raise InstallError("已有 V2bX 服务定义但找不到核心；请先检查本机服务配置")
            scripts = {} if core_only else stage_scripts(work, source, revision)
            files = {} if scripts_only else fetch_core(work, tag, self.arch, sha256)
            active = bool(existing and files and self.service.is_active())
            new_unit = bool(files and not exists(self.unit))
            changes = FileChanges()
            service_touched = False
            reload_attempted = False
            try:
                if files:
                    staged_binary = changes.add(binary, files["V2bX"], 0o755)
                    # Use the destination filesystem, not /tmp (which may be mounted noexec).
                    command(str(staged_binary), "version", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    for name in CONFIG_FILES:
                        cfg = self.root / CONFIG_DIR / name
                        if not exists(cfg):
                            mode = 0o600 if name.endswith(".json") else 0o644
                            changes.add(cfg, files[name], mode)
                    # Legacy releases also left geodata next to the binary; keep
                    # existing local copies and seed known missing assets only.
                    for name in GEO_FILES:
                        asset_path = self.root / INSTALL_DIR / name
                        if name in files and not exists(asset_path):
                            changes.add(asset_path, files[name])
                for rel, source_file in scripts.items():
                    destination, mode = SCRIPTS[rel]
                    if not destination:
                        continue
                    dest = self.root / destination
                    if destination == HOOK and exists(dest):
                        current = file_hash(dest) if dest.is_file() and not dest.is_symlink() else None
                        if current == file_hash(source_file) and dest.stat().st_mode & 0o111:
                            continue
                        if current not in (LEGACY_HOOK_SHA256, file_hash(source_file)):
                            print(f"警告：保留自定义 Certbot 部署钩子：{dest}；"
                                  "新托管证书须先检查并手动处理钩子冲突", file=sys.stderr)
                            continue
                    changes.add(dest, source_file, mode)
                alias = self.root / "usr/bin/v2bx"
                if scripts and not exists(alias):
                    changes.add(alias, link="V2bX")
                if new_unit:
                    service_file = scripts["V2bX.service"]
                    if self.openrc:
                        service_file = work / "V2bX.openrc"
                        service_file.write_text(OPENRC)
                    changes.add(self.unit, service_file, 0o755 if self.openrc else 0o644)
                changes.apply()
                if new_unit:
                    reload_attempted = True
                    self.service.reload()
                if files and (active or start):
                    service_touched = True
                    self.service.start(restart=active)
                    self.service.verify()
            except (Exception, KeyboardInterrupt) as exc:
                rollback_errors = []
                if service_touched and not active:
                    try:
                        self.service.stop()
                    except (InstallError, OSError) as error:
                        rollback_errors.append(str(error))
                rollback_errors.extend(changes.restore())
                try:
                    if reload_attempted:
                        self.service.reload()
                    if service_touched and active and not rollback_errors:
                        self.service.start(restart=True)
                        self.service.verify()
                except (InstallError, OSError) as error:
                    rollback_errors.append(str(error))
                changes.cleanup(rolled_back=True)
                if rollback_errors:
                    raise InstallError(f"操作失败: {exc}；回滚需人工检查: {'; '.join(rollback_errors)}") from exc
                raise InstallError(f"操作失败: {exc}；原文件与服务状态已恢复 (restored)") from exc
            changes.cleanup()
            if scripts_only:
                print("固定提交的维护脚本已更新，未重启服务")
            elif active or start:
                print(f"V2bX {tag} 已安装，服务进程已通过启动检查")
            else:
                print(f"V2bX {tag} 已安装，未启动服务；请检查配置后手动启动")
            if new_unit:
                print("新安装未设置开机自启；需要时请运行 V2bX enable")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", nargs="?")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--core-only", action="store_true")
    modes.add_argument("--scripts-only", action="store_true")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--scripts-rev", dest="revision")
    parser.add_argument("--sha256")
    parser.add_argument("--start", action="store_true")
    options = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("必须以 root 运行")
    signal.signal(signal.SIGTERM, lambda *_: signal.raise_signal(signal.SIGINT))
    openrc = Path("/etc/alpine-release").is_file()
    try:
        Installer(Path("/"), Service(openrc), ARCHES.get(platform.machine().lower()), openrc).run(
            **vars(options))
    except (InstallError, OSError, SyntaxError, KeyboardInterrupt) as exc:
        print(f"V2bX: {exc or '操作已中断'}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
