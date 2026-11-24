#!/usr/bin/env python3
"""Issue and validate Certbot certificates, and deploy verified renewals to V2bX."""
import argparse
from contextlib import contextmanager
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from urllib import error as urlerror, parse, request

ACME_ROOT = Path("/etc/letsencrypt")
CF_ROOT = Path("/etc/V2bX/acme/cloudflare")
DEPLOYED_ROOT = Path("/etc/V2bX/acme/deployed")
CONFIG_PATH = Path("/etc/V2bX/config.json")
UNIT = "V2bX.service"
SERVICE_LOCK = Path("/run/lock/v2bx-installer.lock")
DEPLOY_HOOK = ACME_ROOT / "renewal-hooks/deploy/95-v2bx-native-restart"
# Match the stock hook installed by the same verified release. A legacy or
# customized script may not deploy snapshots and is never silently overwritten.
DEPLOY_HOOK_SHA256 = "0877986459b221358848d0310d732ed1890a1a528978078964668c20bbb6e7ad"
LINEAGE_RE = re.compile(r"v2bx-[a-z0-9][a-z0-9.-]{0,89}")
DOMAIN_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class AcmeError(Exception):
    pass


def validate_domain(raw):
    name = raw.strip().lower()
    if "." not in name or len(name) > 253 or not all(
        DOMAIN_LABEL.fullmatch(part) for part in name.split(".")
    ):
        raise AcmeError("证书域名不正确，应填写完整的可管理域名，例如 jp.wuya.me")
    return name


def certificate_name(domain):
    domain = validate_domain(domain)
    name = "v2bx-" + domain
    if len(name) > 90:
        name = "v2bx-" + domain[:55].rstrip(".-") + "-" + hashlib.sha256(domain.encode()).hexdigest()[:12]
    return name


def certificate_paths(domain, acme_root=ACME_ROOT):
    location = Path(acme_root) / "live" / certificate_name(domain)
    return location / "fullchain.pem", location / "privkey.pem"


def zone_candidates(domain):
    parts = validate_domain(domain).split(".")
    return [".".join(parts[i:]) for i in range(len(parts) - 1)]


def verify_cloudflare_zone(domain, token, opener=None):
    if not TOKEN_RE.fullmatch(token):
        raise AcmeError("Cloudflare Token 不能为空或包含非法字符")
    opener = opener or request.urlopen
    for zone in zone_candidates(domain):
        url = "https://api.cloudflare.com/client/v4/zones?name=" + parse.quote(zone) + "&per_page=50"
        req = request.Request(url, headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
        })
        try:
            with opener(req, timeout=15) as response:
                data = json.load(response)
        except urlerror.HTTPError as exc:
            raise AcmeError("Cloudflare API 返回 HTTP %s，请检查 Token 和 Zone:Read 权限" % exc.code) from None
        except (OSError, ValueError) as exc:
            raise AcmeError("访问 Cloudflare API 失败：%s" % exc) from None
        if not data.get("success"):
            raise AcmeError("Cloudflare Token 权限不足，需 Zone:Read 和 DNS:Edit")
        if any(str(record.get("name", "")).lower() == zone for record in data.get("result", [])):
            return zone
    raise AcmeError(
        "Cloudflare Token 无权管理 %s 所在的 DNS 区域；如果是 NAT 服务商域名，"
        "请选择你自己在 Cloudflare 管理的域名（如 jp.wuya.me），Xboard SNI 也使用该域名。" % domain
    )


def plugin_installed():
    if not shutil.which("certbot"):
        return False
    try:
        p = subprocess.run(["certbot", "plugins", "--text"], capture_output=True,
                           text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return p.returncode == 0 and "dns-cloudflare" in (p.stdout + p.stderr)


def dependencies_ready(mode):
    return bool(shutil.which("certbot") and shutil.which("openssl") and
                (mode != "dns" or plugin_installed()))


def ensure_dependencies(mode):
    if dependencies_ready(mode):
        print("✓ 已检测到 Certbot / OpenSSL 及对应插件", flush=True)
        return
    if not shutil.which("apt-get"):
        raise AcmeError("当前系统缺少 Certbot，请先手动安装 Certbot 和 DNS 插件")
    pkgs = ["certbot", "openssl"]
    if mode == "dns":
        pkgs.append("python3-certbot-dns-cloudflare")
    print("缺少组件，需要 apt-get 安装：" + " ".join(pkgs))
    if input("是否现在安装？[y/N]：").strip().lower() != "y":
        raise AcmeError("已取消依赖安装，未更改 V2bX 配置")
    for cmd in (["apt-get", "update"], ["apt-get", "install", "-y", *pkgs]):
        print("正在执行：" + " ".join(cmd), flush=True)
        try:
            subprocess.run(cmd, check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise AcmeError("依赖安装失败：%s" % exc) from None
    if not dependencies_ready(mode):
        raise AcmeError("Certbot 或 DNS 插件仍不可用，请检查系统包安装状态")


def standalone_port_available():
    """Check both address families without stopping existing listeners."""
    for family, address in ((socket.AF_INET, ("0.0.0.0", 80)),
                            (socket.AF_INET6, ("::", 80))):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                if family == socket.AF_INET6:
                    probe.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(address)
        except OSError as exc:
            if family == socket.AF_INET6 and exc.errno in (
                errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT, errno.EADDRNOTAVAIL,
            ):
                continue
            return False
    return True


def certbot_command(mode, domain, email, credentials=None, webroot=None):
    if mode not in ("http", "dns"):
        raise AcmeError("仅支持 HTTP-01 或 DNS-01 证书申请")
    domain = validate_domain(domain)
    if mode == "dns" and webroot is not None:
        raise AcmeError("DNS-01 不接受 HTTP Webroot")
    cmd = [
        "certbot", "certonly", "--non-interactive", "--agree-tos",
        "--no-eff-email", "--no-directory-hooks", "--cert-name", certificate_name(domain),
        "-d", domain, "--keep-until-expiring",
    ]
    if email:
        if not re.fullmatch(r"[A-Za-z0-9._%+~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", email):
            raise AcmeError("Let's Encrypt 邮箱格式无效")
        cmd.extend(["--email", email])
    else:
        cmd.append("--register-unsafely-without-email")
    if mode == "dns":
        if not credentials:
            raise AcmeError("缺少 Cloudflare Token 凭据文件")
        cmd.extend([
            "--dns-cloudflare", "--dns-cloudflare-credentials", str(credentials),
            "--dns-cloudflare-propagation-seconds", "60",
        ])
    else:
        if webroot is not None:
            webroot = Path(webroot)
            if not webroot.is_absolute() or webroot == Path("/") or not webroot.is_dir():
                raise AcmeError("Webroot 必须是该站点明确配置的、已存在的绝对目录")
            cmd.extend(["--webroot", "--webroot-path", str(webroot.resolve())])
        else:
            cmd.append("--standalone")
        cmd.extend(["--preferred-challenges", "http"])
    return cmd


def credentials_file(domain, root=CF_ROOT):
    return Path(root) / (certificate_name(domain) + ".ini")


def update_credentials(path, token):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise AcmeError("Cloudflare 凭据文件路径不安全，拒绝覆盖")
    if path.parent.is_symlink():
        raise AcmeError("Cloudflare 凭据目录不允许是符号链接")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    original = path.read_bytes() if path.exists() else None
    fd, staged = tempfile.mkstemp(dir=path.parent, prefix=".cf-token-")
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(("dns_cloudflare_api_token = " + token + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, path)
    finally:
        if os.path.exists(staged):
            os.unlink(staged)
    return original


def rollback_credentials(path, original):
    if original is None:
        path.unlink(missing_ok=True)
    else:
        fd, staged = tempfile.mkstemp(dir=path.parent, prefix=".cf-rollback-")
        try:
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(original)
            os.replace(staged, path)
        finally:
            if os.path.exists(staged):
                os.unlink(staged)


def validate_certificate(cert, key, domain):
    cert, key = Path(cert), Path(key)
    if not all(path.is_absolute() and path.is_file() for path in (cert, key)):
        raise AcmeError("签发完成后没有找到证书链/私钥，视为失败")
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert), str(key))
        info = ssl._ssl._test_decode_cert(str(cert))
        expiry = ssl.cert_time_to_seconds(info["notAfter"])
        starts = ssl.cert_time_to_seconds(info["notBefore"])
    except (OSError, ssl.SSLError, KeyError, ValueError) as exc:
        raise AcmeError("证书无法加载或私钥不匹配：%s" % exc) from None
    names = [v.lower() for kind, v in info.get("subjectAltName", []) if kind == "DNS"]
    requested = validate_domain(domain)
    wildcard = "*." + requested.split(".", 1)[1]
    if requested not in names and wildcard not in names:
        raise AcmeError("证书的 SAN 域名不包含 %s（实际：%s）" % (domain, names))
    if starts > time.time():
        raise AcmeError("证书尚未生效，请确认本机时间和证书")
    if expiry <= time.time() + 86400:
        raise AcmeError("证书已过期或有效期不足 24 小时")
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(expiry))



def existing_certificate_valid(domain, acme_root=ACME_ROOT, renew_before_days=30):
    """Reuse only a matching unexpired certificate with enough validity.

    Does not call the CA, Cloudflare, or Certbot; never writes a file.
    """
    domain = validate_domain(domain)
    cert, key = paths_for_use(domain, acme_root)
    if not cert.is_file() or not key.is_file():
        print("尚未找到该域名的已有 Certbot 证书，需要继续申请。", flush=True)
        return False
    try:
        expiry_text = validate_certificate(cert, key, domain)
        # Convert parsed UTC time to seconds independent of the machine timezone.
        import calendar
        expiration = calendar.timegm(time.strptime(expiry_text, "%Y-%m-%d %H:%M:%S UTC"))
    except (AcmeError, ValueError) as exc:
        print("发现证书，但校验失败，不能直接复用：" + str(exc), flush=True)
        return False
    days_left = (expiration - time.time()) / 86400
    if days_left <= renew_before_days:
        print("发现有效证书，但剩余约 %.1f 天，已接近续期时间；将检查原有续期配置。" % days_left, flush=True)
        return False
    print("✓ 检测到之前已签发且可复用的证书（不再申请）", flush=True)
    print("  域名：" + domain, flush=True)
    print("  证书：" + str(cert), flush=True)
    print("  私钥：" + str(key) + "（仅显示路径）", flush=True)
    print("  到期：" + expiry_text + "（剩余约 %d 天）" % int(days_left), flush=True)
    renewal_conf = Path(acme_root) / "renewal" / (certificate_name(domain) + ".conf")
    if not renewal_conf.is_file():
        print("⚠ 未找到 Certbot 自动续期配置；本次可复用，但需检查后续续期。", flush=True)
    return True


def renewal_config(domain, acme_root=ACME_ROOT):
    return Path(acme_root) / "renewal" / (certificate_name(domain) + ".conf")


def renewal_authenticator(conf):
    # Certbot renewal files are ConfigObj with nested sections. The authenticator
    # is a simple key in the top-level [renewalparams] block.
    in_params = False
    for line in conf.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("["):
            if line.startswith("[["):
                break
            in_params = line == "[renewalparams]"
        if in_params and re.match(r"^authenticator\s*=", line):
            return line.split("=", 1)[1].split("#", 1)[0].strip().strip("'\"")
    raise AcmeError("无法确定 Certbot 已有续期验证方式，请手动检查该证书的续期配置")


def managed_paths(name, root=DEPLOYED_ROOT):
    if not LINEAGE_RE.fullmatch(name):
        raise AcmeError("V2bX 证书标识不正确")
    current = Path(root) / name / "current"
    return current / "fullchain.pem", current / "privkey.pem"


def paths_for_use(domain, acme_root=ACME_ROOT, deployed_root=DEPLOYED_ROOT):
    pair = managed_paths(certificate_name(domain), deployed_root)
    return pair if os.path.lexists(pair[0].parent) else certificate_paths(domain, acme_root)


def private_directory(path):
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = path.lstat()
    if path.is_symlink() or not path.is_dir() or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise AcmeError("受管证书目录须由当前用户拥有、权限 0700，且不能是符号链接：" + str(path))


@contextmanager
def deployment_lock(name, root):
    parent = managed_paths(name, root)[0].parent.parent
    private_directory(Path(root))
    private_directory(parent)
    fd = os.open(parent / ".deploy.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield parent
    finally:
        os.close(fd)


def current_generation(parent):
    current = parent / "current"
    if not os.path.lexists(current):
        return None
    if not current.is_symlink():
        raise AcmeError("受管证书 current 不是符号链接，拒绝覆盖")
    target = os.readlink(current)
    if not re.fullmatch(r"pair-[a-z0-9_]+", target) or not (parent / target).is_dir():
        raise AcmeError("受管证书 current 目标不正确，拒绝覆盖")
    return target


def point_to_generation(parent, target):
    # Use a private temporary directory so an interrupted deploy cannot leave an
    # ambiguous current pointer or clobber a pre-existing file.
    with tempfile.TemporaryDirectory(prefix=".pointer-", dir=parent) as directory:
        link = Path(directory) / "link"
        link.symlink_to(target)
        os.replace(link, parent / "current")


def copy_validated_generation(parent, cert, key, domains):
    generation = Path(tempfile.mkdtemp(prefix="pair-", dir=parent))
    try:
        cert_copy, key_copy = generation / "fullchain.pem", generation / "privkey.pem"
        for source, target in ((cert, cert_copy), (key, key_copy)):
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as out, source.open("rb") as inp:
                shutil.copyfileobj(inp, out)
                out.flush()
                os.fsync(out.fileno())
        for domain in domains:
            validate_certificate(cert_copy, key_copy, domain)
        return generation
    except BaseException:
        shutil.rmtree(generation)
        raise


def stage_certificate(name, cert, key, domains, root=DEPLOYED_ROOT):
    """Enroll only a fresh lineage. Existing deployments are updated by the hook."""
    with deployment_lock(name, root) as parent:
        if current_generation(parent) is not None:
            raise AcmeError("该证书已由续期钩子维护，不应直接覆盖当前证书")
        generation = copy_validated_generation(parent, cert, key, domains)
        point_to_generation(parent, generation.name)
    return managed_paths(name, root)


def certificate_digest(cert):
    return hashlib.sha256(cert.read_bytes()).hexdigest()


def legacy_previous_certificate(cert, acme_root):
    resolved = cert.resolve()
    archive = Path(acme_root).resolve() / "archive" / cert.parent.name
    filename = re.fullmatch(r"fullchain([1-9][0-9]*)\.pem", resolved.name)
    if resolved.parent == archive and filename and int(filename[1]) > 1:
        previous = archive / ("fullchain%d.pem" % (int(filename[1]) - 1))
        if previous.is_file():
            return previous
    return None


def systemctl(*args):
    try:
        return subprocess.run(["systemctl", *args], check=False, timeout=45).returncode == 0
    except (OSError, subprocess.TimeoutExpired) as exc:
        print("V2bX 续期钩子：systemctl 失败：" + str(exc), file=sys.stderr)
        return False


def require_automatic_renewal(hook=DEPLOY_HOOK):
    if (hook.is_symlink() or not hook.is_file() or not os.access(hook, os.X_OK)
            or hook.stat().st_uid != 0 or hook.stat().st_mode & 0o022
            or certificate_digest(hook) != DEPLOY_HOOK_SHA256):
        raise AcmeError("自动续期需要配套的官方执行钩子；请先更新安装包或选择已有证书（不覆盖自定义钩子）")
    for timer in ("certbot.timer", "snap.certbot.renew.timer"):
        if systemctl("is-active", "--quiet", timer) and systemctl("is-enabled", "--quiet", timer):
            return
    raise AcmeError("未找到正在运行且已启用的 Certbot 续期 timer；请先确认现有续期计划，或使用已有证书")


@contextmanager
def service_lock():
    # Cooperate with the installer, including all service and rollback steps.
    # Certbot itself never holds this lock, so deploy hooks cannot deadlock
    # with the issuing process.
    fd = os.open(SERVICE_LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def service_identity():
    try:
        result = subprocess.run(
            ["systemctl", "show", UNIT, "--property=ActiveState,SubState,MainPID,NRestarts"],
            check=True, capture_output=True, text=True, timeout=5,
        )
        fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        pid = int(fields.get("MainPID", "0"))
        if fields.get("ActiveState") == "active" and fields.get("SubState") == "running" and pid > 0:
            return pid, int(fields["NRestarts"])
    except (OSError, subprocess.SubprocessError, KeyError, ValueError):
        pass
    return None


def service_stable(settle=12):
    baseline = service_identity()
    if baseline is None:
        return False
    deadline = time.monotonic() + settle
    while time.monotonic() < deadline:
        time.sleep(min(1, max(0, deadline - time.monotonic())))
        if service_identity() != baseline:
            return False
    return True


def deploy_renewal(lineage, config_path=CONFIG_PATH, acme_root=ACME_ROOT,
                   deployed_root=DEPLOYED_ROOT):
    """Use only exact Certbot/managed paths referenced by the local config."""
    lineage = Path(lineage)
    live = Path(acme_root) / "live"
    name = lineage.name
    if lineage.parent != live or not LINEAGE_RE.fullmatch(name):
        return 0
    if not Path(config_path).is_file():
        return 0
    direct = (lineage / "fullchain.pem", lineage / "privkey.pem")
    deployed = managed_paths(name, deployed_root)
    try:
        with service_lock():
            return deploy_referenced_pair(config_path, direct, deployed, name, acme_root, deployed_root)
    except (AcmeError, OSError, ValueError) as exc:
        print("V2bX 续期钩子：%s；请检查日志和证书路径。" % exc, file=sys.stderr)
        return 1


def deploy_referenced_pair(config_path, direct, deployed, name, acme_root, deployed_root):
    managed_domains, legacy_domains = set(), set()
    data = json.loads(Path(config_path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("Nodes"), list):
        raise AcmeError("配置中缺少 Nodes 数组")
    for node in data["Nodes"]:
        config = node.get("CertConfig") if isinstance(node, dict) else None
        if not isinstance(config, dict) or config.get("CertMode") != "file":
            continue
        pair = config.get("CertFile"), config.get("KeyFile")
        for paths, domains in ((deployed, managed_domains), (direct, legacy_domains)):
            expected = tuple(map(str, paths))
            if pair == expected:
                raw_domain = config.get("CertDomain")
                if not raw_domain and paths == direct:
                    # Older direct references may omit CertDomain. Recover
                    # only an unambiguous name from our known lineage format.
                    candidate = name.removeprefix("v2bx-")
                    if certificate_name(candidate) == name:
                        raw_domain = candidate
                if not isinstance(raw_domain, str) or not raw_domain:
                    raise AcmeError("引用该证书的节点没有有效 CertDomain")
                domains.add(validate_domain(raw_domain))
            elif pair[0] == expected[0] or pair[1] == expected[1]:
                raise AcmeError("引用该证书的节点未配置完整配对路径，未触碰服务")
    if not managed_domains and not legacy_domains:
        return 0
    for domain in sorted(managed_domains | legacy_domains):
        validate_certificate(*direct, domain)
    digest = certificate_digest(direct[0])

    with deployment_lock(name, deployed_root) as parent:
        previous = current_generation(parent) if managed_domains else None
        if managed_domains and not previous:
            raise AcmeError("未找到上次验证过的受管证书，未碰服务")
        managed_changed = bool(managed_domains and certificate_digest(deployed[0]) != digest)
        legacy_changed = False
        stamp = parent / "legacy.sha256"
        if legacy_domains:
            if stamp.is_symlink():
                raise AcmeError("旧证书的指纹记录是符号链接，跳过")
            recorded = stamp.read_text(encoding="ascii").strip() if stamp.is_file() else ""
            old_cert = legacy_previous_certificate(direct[0], acme_root) if not recorded else None
            if old_cert:
                recorded = certificate_digest(old_cert)
            if not recorded:
                print("V2bX 续期钩子：无法确认旧布局的证书确有变更，未重启。", file=sys.stderr)
            legacy_changed = bool(recorded and recorded != digest)
        if not managed_changed and not legacy_changed:
            return 0
        active = systemctl("is-active", "--quiet", UNIT)
        replacement = None
        if managed_changed:
            replacement = copy_validated_generation(parent, *direct, sorted(managed_domains))
            point_to_generation(parent, replacement.name)
        if active and (
            not systemctl("try-restart", UNIT) or not service_stable()
        ):
            if replacement is not None:
                point_to_generation(parent, previous)
                shutil.rmtree(replacement)
                if not legacy_changed and systemctl("restart", UNIT) and service_stable():
                    raise AcmeError("新证书重启验证失败，已切回旧证书并恢复服务")
                raise AcmeError("已切回受管旧证书；请检查 V2bX 服务（仍有直接引用的旧布局或恢复失败）")
            raise AcmeError("重启失败；原配置未改变，Certbot 旧版本仍在 archive，需人工恢复")
        if legacy_changed:
            stamp.write_text(digest + "\n", encoding="ascii")
            stamp.chmod(0o600)
        if replacement is not None:
            # Retain the immediate previous generation for manual recovery.
            for candidate in parent.glob("pair-*"):
                if (candidate.name not in (previous, replacement.name)
                        and re.fullmatch(r"pair-[a-z0-9_]+", candidate.name)
                        and not candidate.is_symlink() and candidate.is_dir()):
                    shutil.rmtree(candidate)
        print("V2bX 续期钩子：%s 已验证并%s。" %
              (name, "重启活动服务" if active else "更新证书（服务未启用，不启动）"))
        return 0

def run_certbot(command, token=""):
    print("=== 开始真实签发 / 校验证书 ===", flush=True)
    try:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, encoding="utf-8",
                                errors="replace", bufsize=1)
    except OSError as exc:
        raise AcmeError("无法启动 Certbot：%s" % exc) from None
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            print("  " + (line.replace(token, "[令牌已隐藏]") if token else line).rstrip(), flush=True)
        code = proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait()
        raise
    if code:
        raise AcmeError("Certbot 申请失败（退出码 %s），请根据上面的实际验证日志处理" % code)


def issue_certificate(mode, domain, email="", token="", webroot=None,
                      acme_root=ACME_ROOT, deployed_root=DEPLOYED_ROOT):
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        raise AcmeError("需要 root 权限申请证书")
    domain = validate_domain(domain)
    cert, key = certificate_paths(domain, acme_root)
    conf = renewal_config(domain, acme_root)
    has_lineage = conf.is_file()
    if has_lineage:
        authenticator = renewal_authenticator(conf)
        print("保留已有 Certbot 验证方式和凭据：" + authenticator, flush=True)
        selected = paths_for_use(domain, acme_root, deployed_root)
        if selected != (cert, key) and (not cert.is_file() or
                certificate_digest(selected[0]) != certificate_digest(cert)):
            raise AcmeError("受管证书与 Certbot 当前证书不同，未替换；请检查续期钩子")
        cert, key = selected
        try:
            expires = validate_certificate(cert, key, domain)
        except AcmeError as exc:
            raise AcmeError("已有证书不可用，请按原配置在 Certbot 中续期或选择其他有效证书：" + str(exc)) from None
        print("沿用仍有效的证书，不运行可能含有服务操作的旧续期钩子。")
        print("到期：" + expires + "；如临近到期，请检查原续期计划与日志。")
        return cert, key
    else:
        if any(os.path.lexists(path) for path in (cert, key, conf)):
            raise AcmeError("发现证书文件但没有可用续期配置；不会覆盖，请选择使用已有证书")
        if not dependencies_ready(mode):
            raise AcmeError("缺少 Certbot 依赖，请先执行证书依赖检查")
        require_automatic_renewal()
        cred = None
        old = None
        if mode == "dns":
            zone = verify_cloudflare_zone(domain, token)
            print("✓ Cloudflare Token 有权访问域名区域：" + zone, flush=True)
            cred = credentials_file(domain)
            old = update_credentials(cred, token)
            print("✓ 已安全保存用于续期的 Token 文件（0600 权限）", flush=True)
        else:
            if webroot is None and not standalone_port_available():
                raise AcmeError("TCP/80 无法绑定；指定该域名已在使用的 Webroot，或使用已有证书")
            print("提示：HTTP-01 需要该域名公网 TCP/80 可访问；不会停止服务或修改防火墙", flush=True)
        try:
            command = certbot_command(mode, domain, email, credentials=cred, webroot=webroot)
            run_certbot(command, token)
        except BaseException:
            # If Certbot created a renewal configuration that references the new
            # file, do not remove it underneath the lineage.
            if cred is not None and not conf.is_file():
                rollback_credentials(cred, old)
            raise
    expires = validate_certificate(cert, key, domain)
    if not conf.is_file():
        raise AcmeError("签发结束但未找到 Certbot 续期配置，未注册自动部署")
    cert, key = stage_certificate(certificate_name(domain), cert, key, [domain], deployed_root)
    print("✓ 证书 SAN / 证书与私钥配对 / 有效期验证成功")
    print("  证书文件：" + str(cert))
    print("  私钥文件：" + str(key) + "（私钥内容不显示）")
    print("  到期时间：" + expires)
    return cert, key


def main():
    parser = argparse.ArgumentParser(description="V2bX 同步证书签发与失败保护")
    sub = parser.add_subparsers(dest="action", required=True)
    deps = sub.add_parser("deps")
    deps.add_argument("--mode", choices=["dns", "http"], required=True)
    issue = sub.add_parser("issue")
    issue.add_argument("--mode", choices=["dns", "http"], required=True)
    issue.add_argument("--domain", required=True)
    issue.add_argument("--email", default="")
    issue.add_argument("--webroot", type=Path)
    tracked = sub.add_parser("tracked", help="是否存在原有 Certbot 续期配置")
    tracked.add_argument("--domain", required=True)
    check = sub.add_parser("validate", help="校验手工提供的证书和私钥")
    check.add_argument("--domain", required=True)
    check.add_argument("--cert", type=Path, required=True)
    check.add_argument("--key", type=Path, required=True)
    deploy = sub.add_parser("deploy", help="部署 Certbot 已成功续期的证书")
    deploy.add_argument("--lineage", type=Path, required=True)
    paths = sub.add_parser("paths")
    paths.add_argument("--domain", required=True)
    reuse = sub.add_parser("reuse", help="只读检查并复用已有可用证书，不访问 CA")
    reuse.add_argument("--domain", required=True)
    reuse.add_argument("--renew-before-days", type=int, default=30)
    args = parser.parse_args()
    if args.action == "deps":
        ensure_dependencies(args.mode)
    elif args.action == "deploy":
        sys.exit(deploy_renewal(args.lineage))
    elif args.action == "tracked":
        sys.exit(0 if renewal_config(args.domain).is_file() else 1)
    elif args.action == "validate":
        print("✓ 已有证书通过 SAN、私钥配对及有效期验证，到期：" +
              validate_certificate(args.cert, args.key, args.domain))
    elif args.action == "paths":
        cert, key = paths_for_use(args.domain)
        print("%s|%s" % (cert, key))
    elif args.action == "reuse":
        if args.renew_before_days < 0 or args.renew_before_days > 90:
            raise AcmeError("续期阈值必须在 0–90 天之间")
        if not existing_certificate_valid(args.domain, renew_before_days=args.renew_before_days):
            sys.exit(1)
    else:
        token = (sys.stdin.readline().strip() if args.mode == "dns" and
                 not renewal_config(args.domain).is_file() else "")
        issue_certificate(args.mode, args.domain, args.email, token, args.webroot)


if __name__ == "__main__":
    try:
        main()
    except (AcmeError, OSError) as exc:
        print("✗ 证书申请失败，节点配置不会写入：%s" % exc, file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("✗ 用户取消，节点配置不会写入", file=sys.stderr)
        sys.exit(130)
