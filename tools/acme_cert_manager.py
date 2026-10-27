#!/usr/bin/env python3
"""Apply real Certbot ACME challenges before allowing a V2bX node to be saved.

This never modifies or restarts V2bX. On failure, restores Cloudflare
credentials without deleting existing certificates or Certbot accounts.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
from urllib import error as urlerror, parse, request

ACME_ROOT = Path("/etc/letsencrypt")
CF_ROOT = Path("/etc/V2bX/acme/cloudflare")
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


def certbot_command(mode, domain, email, credentials=None):
    if mode not in ("http", "dns"):
        raise AcmeError("仅支持 HTTP-01 或 DNS-01 证书申请")
    domain = validate_domain(domain)
    cmd = [
        "certbot", "certonly", "--non-interactive", "--agree-tos",
        "--no-eff-email", "--cert-name", certificate_name(domain),
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
        cmd.extend(["--standalone", "--preferred-challenges", "http"])
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
    if not cert.is_file() or not key.is_file():
        raise AcmeError("签发完成后没有找到证书链/私钥，视为失败")
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert), str(key))
        info = ssl._ssl._test_decode_cert(str(cert))
        expiry = ssl.cert_time_to_seconds(info["notAfter"])
    except (OSError, ssl.SSLError, KeyError, ValueError) as exc:
        raise AcmeError("证书无法加载或私钥不匹配：%s" % exc) from None
    names = [v.lower() for kind, v in info.get("subjectAltName", []) if kind == "DNS"]
    if validate_domain(domain) not in names:
        raise AcmeError("证书的 SAN 域名不包含 %s（实际：%s）" % (domain, names))
    if expiry <= time.time() + 86400:
        raise AcmeError("证书已过期或有效期不足 24 小时")
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(expiry))



def existing_certificate_valid(domain, acme_root=ACME_ROOT, renew_before_days=30):
    """Reuse only a matching unexpired certificate with enough validity.

    Does not call the CA, Cloudflare, or Certbot; never writes a file.
    """
    domain = validate_domain(domain)
    cert, key = certificate_paths(domain, acme_root)
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
        print("发现有效证书，但剩余约 %.1f 天，已接近续期时间；将继续申请/续期。" % days_left, flush=True)
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


def issue_certificate(mode, domain, email="", token=""):
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        raise AcmeError("需要 root 权限申请证书")
    domain = validate_domain(domain)
    if not dependencies_ready(mode):
        raise AcmeError("缺少 Certbot 依赖，请先执行证书依赖检查")
    cred = None
    old = None
    if mode == "dns":
        zone = verify_cloudflare_zone(domain, token)
        print("✓ Cloudflare Token 有权访问域名区域：" + zone, flush=True)
        cred = credentials_file(domain)
        old = update_credentials(cred, token)
        print("✓ 已安全保存用于续期的 Token 文件（0600 权限）", flush=True)
    else:
        print("提示：HTTP-01 需要公网 TCP/80 能直达本机，NAT 高位端口无法替代", flush=True)
    try:
        command = certbot_command(mode, domain, email, credentials=cred)
        run_certbot(command, token)
        cert, key = certificate_paths(domain)
        expires = validate_certificate(cert, key, domain)
    except BaseException:
        if cred is not None:
            rollback_credentials(cred, old)
        raise
    print("✓ Let's Encrypt 证书申请成功 / 现有有效证书已复用", flush=True)
    print("  已通过：域名 SAN / 证书与私钥配对 / 有效期验证")
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
    paths = sub.add_parser("paths")
    paths.add_argument("--domain", required=True)
    reuse = sub.add_parser("reuse", help="只读检查并复用已有可用证书，不访问 CA")
    reuse.add_argument("--domain", required=True)
    reuse.add_argument("--renew-before-days", type=int, default=30)
    args = parser.parse_args()
    if args.action == "deps":
        ensure_dependencies(args.mode)
    elif args.action == "paths":
        cert, key = certificate_paths(args.domain)
        print("%s|%s" % (cert, key))
    elif args.action == "reuse":
        if args.renew_before_days < 0 or args.renew_before_days > 90:
            raise AcmeError("续期阈值必须在 0–90 天之间")
        if not existing_certificate_valid(args.domain, renew_before_days=args.renew_before_days):
            sys.exit(1)
    else:
        token = sys.stdin.readline().strip() if args.mode == "dns" else ""
        issue_certificate(args.mode, args.domain, args.email, token)


if __name__ == "__main__":
    try:
        main()
    except (AcmeError, OSError) as exc:
        print("✗ 证书申请失败，节点配置不会写入：%s" % exc, file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("✗ 用户取消，节点配置不会写入", file=sys.stderr)
        sys.exit(130)
