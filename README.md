# V2bX
A V2board node server based on Xray-Core.

一个基于Xray的V2board节点服务端，支持V2ay,Trojan,Shadowsocks协议

Find the source code here: [InazumaV/V2bX](https://github.com/InazumaV/V2bX)

如对脚本不放心，可使用此沙箱先测一遍再使用：https://killercoda.com/playgrounds/scenario/ubuntu

# 详细使用教程

[教程](https://v2bx.v-50.me/)

# 一键安装

```
wget -N https://raw.githubusercontent.com/Jasper4860/Xborad-V2bX-script/master/install.sh && bash install.sh
```


## AnyTLS (native V2bX + sing-box)

This fork adds safe native AnyTLS node management. **AnyTLS is already implemented
by the V2bX sing-box core**, including per-user authentication and traffic
accounting; do **not** run standalone AnyTLS-Go to integrate with Xboard.

Requirements: a compatible V2bX binary, an Xboard panel version with an AnyTLS
node and subscription support, a correctly configured TLS domain and certificate,
and Python 3 for the optional safe quick-add helper.

```bash
# Add an AnyTLS node alongside existing nodes; backs up config first.
V2bX anytls-add
# Validate local AnyTLS configuration without touching services.
V2bX anytls-check
# Inspect config and backup; only on a test node, when ready:
systemctl restart V2bX
journalctl -u V2bX -n 100 --no-pager
```

The interactive `V2bX` menu offers choices **18** (add) and **19** (check).
The existing config wizard (choice 15) also exposes **sing-box -> AnyTLS**.

This helper appends to `/etc/V2bX/config.json`, preserves existing Xray,
Trojan, VLESS and other nodes, and adds a sing-box core only if absent.
Before writing it keeps `config.json.bak-anytls-*`. If a new sing-box core
is needed and `sing_origin.json` is absent, it creates a minimal default
that blocks private destination IPs. If that file exists, it is preserved.

TLS modes: **file** (valid existing certificate/key pair, recommended) or
**http** (V2bX attempts ACME; requires working DNS and externally reachable
TCP/80). The helper does not restart V2bX, change iptables, or alter Docker.
It intentionally refuses to rewrite JSON5-with-comments configuration.
An old V2bX executable or an outdated Xboard panel may not support this
protocol even if the menu shows it; check actual versions first.

**Important:** This is a script integration, not a new proxy engine. The
upstream binary is still downloaded from `wyx2685/V2bX`. The management
script and helper download from this fork; do not replace a production
installation without testing. Keep configs and backups private because
they contain UniProxy API secrets.
