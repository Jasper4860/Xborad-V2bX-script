#!/usr/bin/env python3
"""Safely append Xboard AnyTLS nodes to a native V2bX sing-box configuration."""
import argparse
import copy
import json
import socket
import subprocess
import os
from pathlib import Path
import re
import shutil
import ssl
import stat
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlparse


class ConfigError(Exception):
    pass


def load_config(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ConfigError("配置文件读取失败（含注释的 JSON5 配置请手动处理）：%s" % exc)
    if not isinstance(value, dict) or any(not isinstance(value.get(k), list) for k in ("Cores", "Nodes")):
        raise ConfigError("配置文件缺少 Cores 或 Nodes 数组")
    return value


def validate_host(value):
    value = value.strip().rstrip("/")
    url = urlparse(value)
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
        raise ConfigError("面板地址必须是 http(s)://域名，不能包含账号或密码")
    if url.path not in ("", "/") or url.query or url.fragment:
        raise ConfigError("面板地址仅填写网站根地址，不含路径、参数")
    return value


def validate_domain(value):
    value = value.strip().lower()
    if len(value) > 253 or "." not in value or not all(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", x) for x in value.split(".")
    ):
        raise ConfigError("TLS 证书域名格式不正确（例如 jp.example.com）")
    return value


def validate_pair(cert, key):
    for value in (cert, key):
        if not Path(value).is_absolute() or not Path(value).is_file():
            raise ConfigError("已有证书必须填写真实存在的绝对路径，以 / 开头")
    try:
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(cert, key)
    except (OSError, ssl.SSLError) as exc:
        raise ConfigError("证书和私钥不匹配或无法读取：%s" % exc)


def build_node(host, key, node_id, domain, cert_mode, cert_file, key_file, email="", provider="", dns_env=None):
    host = validate_host(host)
    domain = validate_domain(domain)
    if not isinstance(node_id, int) or not 1 <= node_id <= 2147483647:
        raise ConfigError("Node ID 必须是有效正整数")
    if not key.strip():
        raise ConfigError("UniProxy API Key 不能为空")
    if cert_mode not in ("file", "http", "dns"):
        raise ConfigError("证书模式仅支持已有证书、HTTP-01、Cloudflare DNS-01")
    if cert_mode in ("http", "dns") and email and ("@" not in email or "." not in email.split("@")[-1]):
        raise ConfigError("Let's Encrypt 联系邮箱格式不正确")
    if cert_mode == "dns" and (provider != "cloudflare" or not (dns_env or {}).get("CLOUDFLARE_DNS_API_TOKEN")):
        raise ConfigError("DNS-01 需要 Cloudflare DNS API Token")
    if not Path(cert_file).is_absolute() or not Path(key_file).is_absolute():
        raise ConfigError("证书路径必须以 / 开头")
    if cert_mode == "file":
        validate_pair(cert_file, key_file)
    return {
        "Core": "sing", "ApiHost": host, "ApiKey": key.strip(),
        "NodeID": node_id, "NodeType": "anytls", "Timeout": 30,
        "ListenIP": "0.0.0.0", "SendIP": "0.0.0.0",
        "DeviceOnlineMinTraffic": 200, "MinReportTraffic": 0,
        "TCPFastOpen": False, "SniffEnabled": True,
        "CertConfig": {
            "CertMode": cert_mode, "RejectUnknownSni": False,
            "CertDomain": domain, "CertFile": cert_file, "KeyFile": key_file,
            "Email": email, "Provider": provider, "DNSEnv": dns_env or {}
        }
    }


def build_updated(config, node, directory):
    out = copy.deepcopy(config)
    if any(isinstance(n, dict) and n.get("NodeID") == node["NodeID"]
           and str(n.get("ApiHost", "")).rstrip("/") == node["ApiHost"] for n in out["Nodes"]):
        raise ConfigError("此 Xboard 地址下已经存在同一 Node ID，拒绝重复添加")
    needs_core = not any(isinstance(c, dict) and c.get("Type") == "sing" for c in out["Cores"])
    if needs_core:
        out["Cores"].append({
            "Type": "sing", "Log": {"Level": "error", "Timestamp": True},
            "NTP": {"Enable": False, "Server": "time.apple.com", "ServerPort": 0},
            "OriginalPath": str(directory / "sing_origin.json")
        })
    out["Nodes"].append(node)
    return out, needs_core


def write_json_atomic(path, obj, mode):
    fd, staged = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), mode)
            json.dump(obj, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, path)
    finally:
        if os.path.exists(staged):
            os.unlink(staged)


def save(path, updated, needs_core):
    if not path.is_file():
        raise ConfigError("找不到 V2bX config.json 配置文件")
    origin = path.parent / "sing_origin.json"
    backup = path.with_name(path.name + ".bak-anytls-" +
                            datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") +
                            "-" + str(os.getpid()))
    shutil.copy2(path, backup)
    os.chmod(backup, 0o600)
    if needs_core and not origin.exists():
        # Do not overwrite an existing sing-box origin or routing policy.
        write_json_atomic(origin, {
            "outbounds": [
                {"type": "direct", "tag": "direct"},
                {"type": "block", "tag": "block"}
            ],
            "route": {"rules": [{"ip_is_private": True, "outbound": "block"}], "final": "direct"}
        }, 0o644)
    write_json_atomic(path, updated, 0o600)
    return backup


def check(config):
    nodes = [n for n in config["Nodes"] if isinstance(n, dict) and n.get("NodeType") == "anytls"]
    sings = [c for c in config["Cores"] if isinstance(c, dict) and c.get("Type") == "sing"]
    if not nodes:
        print("当前配置没有 AnyTLS 节点。")
        return 0
    failed = False
    for node in nodes:
        issues = []
        cert = node.get("CertConfig") or {}
        if node.get("Core") != "sing" or not sings:
            issues.append("AnyTLS 必须使用 sing 核心")
        if cert.get("CertMode") not in ("file", "http", "dns", "self"):
            issues.append("TLS 证书模式无效")
        if not cert.get("CertDomain"):
            issues.append("未配置 TLS 证书域名")
        if cert.get("CertMode") == "file":
            try:
                validate_pair(cert.get("CertFile", ""), cert.get("KeyFile", ""))
            except (ConfigError, TypeError) as exc:
                issues.append(str(exc))
        failed = failed or bool(issues)
        print("AnyTLS 节点 %s：%s" % (node.get("NodeID"), "；".join(issues) if issues else "本地配置检查正常"))
    print("仅检查本地配置；未实际验证面板 API、证书签发或节点连通性。")
    return 1 if failed else 0


def acme_file_paths(directory, node_id, cert_domain):
    """Unique private paths; no user-supplied path input for automatic issuance."""
    target = directory / "certs" / "anytls" / ("%s-%d" % (cert_domain, node_id))
    return str(target / "fullchain.cer"), str(target / "privkey.pem")


def ask_choice():
    print("\n请选择 TLS 证书方式：")
    print("  1. Let's Encrypt HTTP-01 自动申请（公网 TCP/80 必须能访问本机）")
    print("  2. Let's Encrypt DNS-01（Cloudflare，适合 NAT，无需公网 80）")
    print("  3. 使用服务器上已有的证书和私钥")
    while True:
        value = input("请输入 [1/2/3]（默认 3）：").strip() or "3"
        if value in ("1", "2", "3"):
            return value
        print("输入无效，请选择 1、2 或 3。")


def main():
    parser = argparse.ArgumentParser(description="V2bX AnyTLS 中文节点管理器")
    parser.add_argument("action", choices=["add", "check"])
    parser.add_argument("--config", type=Path, default=Path("/etc/V2bX/config.json"))
    args = parser.parse_args()
    existing = load_config(args.config)
    if args.action == "check":
        return check(existing)
    if os.geteuid() != 0:
        raise ConfigError("需要 root 权限修改 V2bX 配置文件")

    print("=== Xboard / V2bX：AnyTLS 一键添加节点 ===")
    print("通过 V2bX 内置 sing-box 核心运行。只修改配置，不自动重启服务。")
    host = validate_host(input("请输入 Xboard 面板地址（如 https://panel.example.com）："))
    print("API Key 将以明文显示在当前终端，请不要截图或共享终端记录。")
    api_key = input("请输入 UniProxy API Key（可见）：").strip()
    if not api_key:
        raise ConfigError("API Key 不能为空")
    if input("确认 API Key 输入无误？输入 y 继续，其它取消 [y/N]：").strip().lower() != "y":
        print("操作已取消，没有修改配置。")
        return 0
    raw_id = input("请输入 Xboard 已创建的 AnyTLS 节点 Node ID：").strip()
    if not raw_id.isdecimal() or not (1 <= int(raw_id) <= 2147483647):
        raise ConfigError("Node ID 必须是有效正整数")
    node_id = int(raw_id)
    domain = validate_domain(input("请输入 TLS 证书域名（例如 jp.example.com）："))
    # Read-only preflight: a valid previous Certbot lineage needs no new ACME call.
    acme_tool = Path("/usr/local/V2bX/acme_cert_manager.py")
    if not acme_tool.is_file():
        acme_tool = Path(__file__).with_name("acme_cert_manager.py")
    reused = False
    if acme_tool.is_file():
        print("正在检查服务器是否已有可复用的 Let\u0027s Encrypt 证书...")
        reusable = subprocess.run([sys.executable, str(acme_tool), "reuse", "--domain", domain], check=False)
        reused = reusable.returncode == 0
    if reused:
        mode, email, provider, env = "file", "", "", {}
        cert_file = "/etc/letsencrypt/live/v2bx-%s/fullchain.pem" % domain
        key_file = "/etc/letsencrypt/live/v2bx-%s/privkey.pem" % domain
        print("已自动复用证书，无需输入 Cloudflare Token。")
    else:
        choice = ask_choice()
        mode, email, provider, env = "file", "", "", {}
        if choice == "3":
            print("已有证书需要填写服务器上的真实绝对路径（以 / 开头）。")
            print("例如：/etc/letsencrypt/live/jp.example.com/fullchain.pem")
            print("      /etc/letsencrypt/live/jp.example.com/privkey.pem")
            cert_file = input("请输入 fullchain 证书绝对路径：").strip()
            key_file = input("请输入 private key 私钥绝对路径：").strip()
        else:
            acme_tool = Path("/usr/local/V2bX/acme_cert_manager.py")
            if not acme_tool.is_file():
                acme_tool = Path(__file__).with_name("acme_cert_manager.py")
            if not acme_tool.is_file():
                raise ConfigError("找不到同步证书申请工具，请更新 V2bX 脚本")
            mode = "http" if choice == "1" else "dns"
            email = input("请输入 Let\u0027s Encrypt 联系邮箱（可回车跳过）：").strip()
            token = ""
            if mode == "dns":
                print("Cloudflare Token 需要 Zone:Read 与 DNS:Edit 权限，且域名必须由你管理。")
                token = input("请输入 Cloudflare DNS API Token（可见，请勿截图）：").strip()
                if not re.fullmatch(r"[A-Za-z0-9_-]+", token):
                    raise ConfigError("Cloudflare Token 格式不正确")
            if input("是否现在实时申请证书？失败则不添加节点。[y/N]：").strip().lower() != "y":
                print("已取消：未修改配置")
                return 0
            dep = subprocess.run([sys.executable, str(acme_tool), "deps", "--mode", mode], check=False)
            if dep.returncode:
                raise ConfigError("证书组件检查失败；未添加节点")
            cmd = [sys.executable, str(acme_tool), "issue", "--mode", mode,
                   "--domain", domain, "--email", email]
            actual = subprocess.run(cmd, input=(token + "\n" if mode == "dns" else None),
                                    text=True, check=False)
            if actual.returncode:
                raise ConfigError("Let\u0027s Encrypt 证书验证/申请失败；未添加节点")
            cert_file = "/etc/letsencrypt/live/v2bx-%s/fullchain.pem" % domain
            key_file = "/etc/letsencrypt/live/v2bx-%s/privkey.pem" % domain
            mode = "file"  # certbot manages renewal; V2bX consumes existing files
            email = ""
            provider = ""
            env = {}
    node = build_node(host, api_key, node_id, domain, mode, cert_file, key_file,
                      email=email, provider=provider, dns_env=env)
    updated, needs_core = build_updated(existing, node, args.config.parent)
    print("\n准备追加 AnyTLS 节点：ID %d，核心 sing，证书方式 %s" % (node_id, mode))
    print("现有节点数量：%d；是否新增 sing 核心：%s" %
          (len(existing["Nodes"]), "是" if needs_core else "否"))
    print("证书文件：%s" % cert_file)
    print("私钥文件：%s（不会输出私钥内容）" % key_file)
    print("不会自动重启 V2bX；原有节点保持不变。")
    if input("是否备份并写入新的配置？[y/N]：").strip().lower() != "y":
        print("操作已取消，没有修改配置。")
        return 0
    backup = save(args.config, updated, needs_core)
    print("配置写入完成。备份文件：%s" % backup)
    print("执行 V2bX anytls-check 查看本地配置。")
    print("在测试环境确认无误后，手动执行 systemctl restart V2bX。")
    if mode in ("http", "dns"):
        print("Let's Encrypt 将由 V2bX 启动时申请；此时尚未签发证书。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ConfigError, KeyboardInterrupt, EOFError) as exc:
        print("AnyTLS 配置失败：%s" % exc, file=sys.stderr)
        sys.exit(1)
