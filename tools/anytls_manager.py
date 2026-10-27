#!/usr/bin/env python3
"""Safely append Xboard AnyTLS nodes to a native V2bX sing-box configuration."""
import argparse
import copy
import getpass
import json
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
        raise ConfigError("Cannot read strict JSON config: %s (JSON5 comments require manual editing)" % exc)
    if not isinstance(value, dict) or any(not isinstance(value.get(k), list) for k in ("Cores", "Nodes")):
        raise ConfigError("Configuration must contain Cores and Nodes arrays")
    return value


def validate_host(value):
    value = value.strip().rstrip("/")
    url = urlparse(value)
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
        raise ConfigError("ApiHost must be a valid http(s) panel URL without embedded credentials")
    if url.path not in ("", "/") or url.query or url.fragment:
        raise ConfigError("ApiHost must be the base URL without path, query, or fragment")
    return value


def validate_domain(value):
    value = value.strip().lower()
    if len(value) > 253 or "." not in value or not all(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", x) for x in value.split(".")
    ):
        raise ConfigError("Invalid ASCII certificate domain")
    return value


def validate_pair(cert, key):
    for value in (cert, key):
        if not Path(value).is_absolute() or not Path(value).is_file():
            raise ConfigError("Existing TLS certificates require absolute paths to real files")
    try:
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(cert, key)
    except (OSError, ssl.SSLError) as exc:
        raise ConfigError("Certificate/key cannot be loaded as a matching pair: %s" % exc)


def build_node(host, key, node_id, domain, cert_mode, cert_file, key_file):
    host = validate_host(host)
    domain = validate_domain(domain)
    if not isinstance(node_id, int) or not 1 <= node_id <= 2147483647:
        raise ConfigError("Invalid positive node ID")
    if not key.strip():
        raise ConfigError("UniProxy API key cannot be blank")
    if cert_mode not in ("file", "http"):
        raise ConfigError("Certificate mode must be file or http")
    if not Path(cert_file).is_absolute() or not Path(key_file).is_absolute():
        raise ConfigError("Certificate paths must be absolute")
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
            "Email": "", "Provider": "", "DNSEnv": {}
        }
    }


def build_updated(config, node, directory):
    out = copy.deepcopy(config)
    if any(isinstance(n, dict) and n.get("NodeID") == node["NodeID"]
           and str(n.get("ApiHost", "")).rstrip("/") == node["ApiHost"] for n in out["Nodes"]):
        raise ConfigError("This panel URL and node ID are already configured")
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
        raise ConfigError("V2bX config file does not exist")
    origin = path.parent / "sing_origin.json"
    backup = path.with_name(path.name + ".bak-anytls-" +
                            datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") +
                            "-" + str(os.getpid()))
    shutil.copy2(path, backup)
    if needs_core and not origin.exists():
        # Do not overwrite an existing sing-box origin or routing policy.
        write_json_atomic(origin, {
            "outbounds": [
                {"type": "direct", "tag": "direct"},
                {"type": "block", "tag": "block"}
            ],
            "route": {"rules": [{"ip_is_private": True, "outbound": "block"}], "final": "direct"}
        }, 0o644)
    write_json_atomic(path, updated, stat.S_IMODE(path.stat().st_mode))
    return backup


def check(config):
    nodes = [n for n in config["Nodes"] if isinstance(n, dict) and n.get("NodeType") == "anytls"]
    sings = [c for c in config["Cores"] if isinstance(c, dict) and c.get("Type") == "sing"]
    if not nodes:
        print("No AnyTLS nodes configured.")
        return 0
    failed = False
    for node in nodes:
        issues = []
        cert = node.get("CertConfig") or {}
        if node.get("Core") != "sing" or not sings:
            issues.append("AnyTLS needs a sing core")
        if cert.get("CertMode") not in ("file", "http", "dns", "self"):
            issues.append("Invalid TLS certificate mode")
        if not cert.get("CertDomain"):
            issues.append("TLS certificate domain is missing")
        if cert.get("CertMode") == "file":
            try:
                validate_pair(cert.get("CertFile", ""), cert.get("KeyFile", ""))
            except (ConfigError, TypeError) as exc:
                issues.append(str(exc))
        failed = failed or bool(issues)
        print("AnyTLS node %s: %s" % (node.get("NodeID"), "; ".join(issues) if issues else "local config OK"))
    print("Read-only local checks. Panel API, ACME issuance and live connections are not verified.")
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description="Native AnyTLS manager for V2bX")
    parser.add_argument("action", choices=["add", "check"])
    parser.add_argument("--config", type=Path, default=Path("/etc/V2bX/config.json"))
    args = parser.parse_args()
    existing = load_config(args.config)
    if args.action == "check":
        return check(existing)
    if os.geteuid() != 0:
        raise ConfigError("Root privileges required to modify V2bX configuration")
    print("Append an AnyTLS node via existing sing-box core. No services restarted.")
    host = validate_host(input("Xboard panel API base URL: "))
    api_key = getpass.getpass("UniProxy API key (hidden): ")
    node_id = input("Existing Xboard AnyTLS Node ID: ").strip()
    if not node_id.isdecimal():
        raise ConfigError("Node ID must be a positive integer")
    domain = validate_domain(input("TLS certificate domain: "))
    print("TLS certificate: 1) existing files [recommended]  2) HTTP ACME (requires reachable port 80)")
    choice = input("Choose [1]: ").strip() or "1"
    if choice == "1":
        mode = "file"
        cert_file = input("Existing fullchain absolute path: ").strip()
        key_file = input("Existing private key absolute path: ").strip()
    elif choice == "2":
        mode = "http"
        cert_file = str(args.config.parent / ("anytls-%s-fullchain.cer" % node_id))
        key_file = str(args.config.parent / ("anytls-%s-key.pem" % node_id))
    else:
        raise ConfigError("Unsupported certificate mode")
    node = build_node(host, api_key, int(node_id), domain, mode, cert_file, key_file)
    updated, needs_core = build_updated(existing, node, args.config.parent)
    print("Will add AnyTLS node %s. Existing nodes: %d. Add sing core: %s" %
          (node_id, len(existing["Nodes"]), needs_core))
    if input("Write backup and updated configuration (no restart)? [y/N]: ").strip().lower() != "y":
        print("Cancelled without changes.")
        return 0
    backup = save(args.config, updated, needs_core)
    print("Saved config. Backup: %s" % backup)
    print("Check with: V2bX anytls-check; manually activate with: systemctl restart V2bX")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ConfigError, KeyboardInterrupt, EOFError) as exc:
        print("AnyTLS: %s" % exc, file=sys.stderr)
        sys.exit(1)
