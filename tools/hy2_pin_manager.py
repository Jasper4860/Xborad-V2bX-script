#!/usr/bin/env python3
"""Hysteria2 self-signed leaf certificate + SHA-256 pin manager for native V2bX.

This tool never touches Xboard, the firewall, or the V2bX systemd service.
A self-signed certificate is reused, NOT regenerated, on repeated calls.
"""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import tempfile
from urllib.parse import quote, urlencode

DEFAULT_ROOT = Path("/etc/V2bX/certs/hy2")
DEFAULT_CONFIG = Path("/etc/V2bX/config.json")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class PinError(Exception):
    pass


def domain(value):
    name = value.strip().lower().rstrip(".")
    if not name or len(name) > 253 or "." not in name:
        raise PinError("证书名称/SNI 必须是完整域名，例如 jp.example.com")
    if not all(HOST_LABEL.fullmatch(part) for part in name.split(".")):
        raise PinError("无效域名：只支持 ASCII 字母、数字、连字符和点号")
    return name


def server_host(value):
    if not value:
        return ""
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return domain(value)
    return value


def port_number(value):
    if not value:
        return ""
    if not value.isdecimal() or not 1 <= int(value) <= 65535:
        raise PinError("公网 UDP 端口必须为 1–65535")
    return str(int(value))


def port_range(value):
    if not value:
        return ""
    match = re.fullmatch(r"([0-9]{1,5})-([0-9]{1,5})", value)
    if not match or not 1 <= int(match.group(1)) <= int(match.group(2)) <= 65535:
        raise PinError("mport 必须是有效 UDP 端口范围，例如 60000-60100")
    return "%s-%s" % (int(match.group(1)), int(match.group(2)))


def run_openssl(*args):
    try:
        return subprocess.run(
            ["openssl", *map(str, args)], check=True, capture_output=True,
            text=True, timeout=40,
        ).stdout.strip()
    except FileNotFoundError as exc:
        raise PinError("未安装 OpenSSL；Debian 可执行 apt-get install -y openssl") from exc
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        detail = (getattr(exc, "stderr", "") or str(exc)).strip()
        raise PinError("OpenSSL 检查/生成失败：" + detail[-600:]) from exc


def verify_pair(cert, key):
    if not cert.is_file() or not key.is_file() or cert.is_symlink() or key.is_symlink():
        raise PinError("证书文件或私钥不存在，或是符号链接；不会自动覆盖")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        ctx.load_cert_chain(str(cert), str(key))
    except (OSError, ssl.SSLError) as exc:
        raise PinError("证书与私钥无法配对：" + str(exc)) from exc
    # Refuse a stale cert: silently reissuing would change every client's pin.
    run_openssl("x509", "-in", cert, "-noout", "-checkend", "0")


def fingerprint(cert):
    raw = run_openssl("x509", "-in", cert, "-noout", "-fingerprint", "-sha256")
    if "=" not in raw:
        raise PinError("无法读取证书的 SHA-256 指纹")
    sha = raw.split("=", 1)[1].replace(":", "").strip().lower()
    if not HEX64.fullmatch(sha):
        raise PinError("证书指纹格式错误")
    return sha


def ensure_certificate(cert_name, root=DEFAULT_ROOT):
    cert_name = domain(cert_name)
    root = Path(root)
    if root.is_symlink():
        raise PinError("证书根目录不允许使用符号链接")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    cert_dir = root / cert_name
    if cert_dir.is_symlink():
        raise PinError("证书目录不允许使用符号链接")
    cert_dir.mkdir(mode=0o700, exist_ok=True)
    os.chmod(cert_dir, 0o700)
    cert = cert_dir / "cert.pem"
    key = cert_dir / "key.pem"

    if cert.exists() or key.exists() or cert.is_symlink() or key.is_symlink():
        if not (cert.exists() and key.exists()):
            raise PinError("仅存在部分证书文件。拒绝覆盖，请先检查/手动恢复。")
        verify_pair(cert, key)
        return cert, key, False

    # Stage the pair in a private folder; existing certs are never overwritten.
    old_umask = os.umask(0o077)
    try:
        with tempfile.TemporaryDirectory(prefix=".new-", dir=cert_dir) as tmp:
            staged_cert = Path(tmp) / "cert.pem"
            staged_key = Path(tmp) / "key.pem"
            run_openssl(
                "req", "-x509", "-newkey", "rsa:2048", "-sha256",
                "-nodes", "-days", "365",
                "-subj", "/CN=" + cert_name,
                "-addext", "subjectAltName=DNS:" + cert_name,
                "-keyout", staged_key, "-out", staged_cert,
            )
            verify_pair(staged_cert, staged_key)
            os.chmod(staged_key, 0o600)
            os.chmod(staged_cert, 0o644)
            # If interrupted during this step, next invocation detects partial
            # files instead of silently rotating the pinned certificate.
            os.replace(staged_key, key)
            os.replace(staged_cert, cert)
    finally:
        os.umask(old_umask)
    return cert, key, True


def uri_template(server, port, sni, pin, mport=""):
    server = server_host(server) if server else "YOUR_NODE_HOST"
    if ":" in server:
        server = "[" + server + "]"
    port = port_number(port) if port else "YOUR_UDP_PORT"
    mport = port_range(mport)
    if not HEX64.fullmatch(pin):
        raise PinError("无效的固定证书指纹")
    query = {"sni": domain(sni), "insecure": "1", "pinSHA256": pin}
    if mport:
        query["mport"] = mport
    return "hysteria2://YOUR_XBOARD_USER_PASSWORD@%s:%s?%s#HY2-PINNED" % (
        server, port, urlencode(query)
    )


def issue(args):
    cert_name = domain(args.domain)
    sni = domain(args.sni or cert_name)
    host = server_host(args.host)
    port = port_number(args.port)
    mport = port_range(args.mport)
    if mport and not port:
        raise PinError("填写 mport 时也需要指定 UDP 初始端口")
    if mport and port and not int(mport.split("-")[0]) <= int(port) <= int(mport.split("-")[1]):
        raise PinError("mport 范围应包含初始端口")
    cert, key, created = ensure_certificate(cert_name, args.root)
    pin = fingerprint(cert)
    print("Hysteria2 固定证书：%s" % ("新生成（365 天）" if created else "已存在，原样复用"))
    print("证书路径：%s" % cert)
    print("私钥路径：%s（请勿分享）" % key)
    print("SHA256（64位小写，不含冒号）：%s" % pin)
    print("客户端 SNI（须在 Xboard 节点设置中同步配置）：%s" % sni)
    if sni != cert_name:
        print("注意：SNI 与自签名证书名称不同；客户端需设置 insecure=1 且启用 pinSHA256。")
    print("客户端手动连接模板（替换密码和地址；不含真实用户密码）：")
    print(uri_template(host, port, sni, pin, mport))
    print("请核对从可信服务器获得的指纹。Xboard 默认订阅格式未保证下发 pinSHA256。")
    print("不要仅下发 insecure=1 而没有 pin 的订阅！")
    print("私钥和证书不会自动轮换；证书到期需计划重新签发并同步更新所有客户端指纹。")
    if mport:
        print("mport 仅生成客户端模板，未修改防火墙/NAT；必须已将全部 UDP 端口映射到服务端。")


def show(args):
    if args.cert is not None:
        cert = Path(args.cert)
        if not cert.is_file():
            raise PinError("证书不存在：" + str(cert))
        print("证书：%s" % cert)
        print("pinSHA256=%s" % fingerprint(cert))
        return
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PinError("无法读取 V2bX config.json；可使用 --cert 指定证书：" + str(exc)) from exc
    nodes = [node for node in config.get("Nodes", [])
             if isinstance(node, dict) and node.get("NodeType") == "hysteria2"]
    if not nodes:
        print("配置中未找到 Hysteria2 节点。")
        return
    for node in nodes:
        cc = node.get("CertConfig") or {}
        cert_name = cc.get("CertFile")
        print("Node ID: %s  核心: %s  证书模式: %s" %
              (node.get("NodeID"), node.get("Core"), cc.get("CertMode")))
        if not cert_name or not Path(cert_name).is_file():
            print("  暂无证书文件（可能尚未签发）。")
            continue
        print("  证书：%s" % cert_name)
        try:
            print("  pinSHA256: %s" % fingerprint(Path(cert_name)))
            if cc.get("CertMode") != "file":
                print("  警告：ACME 证书可能自动轮换；不建议固定此模式的指纹。")
        except PinError as exc:
            print("  证书读取失败：%s" % exc)


def main():
    parser = argparse.ArgumentParser(description="V2bX Hysteria2 SHA256 pinned certificate helper")
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("issue", help="Generate or reuse a private self-signed server certificate")
    p.add_argument("--domain", required=True, help="Self-signed certificate DNS name")
    p.add_argument("--sni", default="", help="Client SNI for example link; set in Xboard separately")
    p.add_argument("--host", default="", help="Optional external NAT/domain connection host")
    p.add_argument("--port", default="", help="Optional external UDP port")
    p.add_argument("--mport", default="", help="Optional already-forwarded UDP hop range")
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    q = sub.add_parser("show", help="Show current certificate fingerprint (read-only)")
    q.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    q.add_argument("--cert", default=None, help="Alternative: inspect an individual cert file")
    args = parser.parse_args()
    if args.action == "issue":
        if hasattr(os, "geteuid") and os.geteuid() != 0 and Path(args.root) == DEFAULT_ROOT:
            raise PinError("写入系统证书目录需要 root 权限")
        issue(args)
    else:
        show(args)


if __name__ == "__main__":
    try:
        main()
    except (PinError, OSError, KeyboardInterrupt) as exc:
        print("HY2 证书管理失败：%s" % exc, file=sys.stderr)
        sys.exit(1)
