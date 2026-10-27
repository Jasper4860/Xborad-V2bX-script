# V2bX
A V2board node server based on Xray-Core.

一个基于Xray的V2board节点服务端，支持V2ay,Trojan,Shadowsocks协议

Find the source code here: [InazumaV/V2bX](https://github.com/InazumaV/V2bX)

如对脚本不放心，可使用此沙箱先测一遍再使用：https://killercoda.com/playgrounds/scenario/ubuntu

# 详细使用教程

[教程](https://v2bx.v-50.me/)

# 一键安装

```
wget -N https://raw.githubusercontent.com/Jasper4860/Xborad-V2bX-script/feature/native-anytls-management/install.sh && bash install.sh
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

## Integrated interactive AnyTLS / Let's Encrypt wizard

Run `V2bX generate`, or use **15. 生成 V2bX 配置文件** in the menu.
In the **请选择节点核心类型** prompt, select **4. AnyTLS (sing-box core)**.
The wizard asks for the Xboard AnyTLS Node ID, TLS domain and preferred
certificate method:

1. **Let's Encrypt HTTP-01** (default): supply a valid FQDN and email.
   On **V2bX service start**, the built-in lego ACME provider requests the
   certificate. Renewal is checked once a day. Public TCP/80 must reach
   the local server, and no other process may hold port 80 during issuance.
   NAT/DDNS hostname resolution alone cannot solve a missing TCP/80 mapping.
2. **Let's Encrypt DNS-01 (Cloudflare)**: supply FQDN, contact email and
   a Cloudflare `CLOUDFLARE_DNS_API_TOKEN` with Zone:Read and DNS:Edit
   permissions. No public TCP/80 port is required. The credential is stored
   in `CertConfig.DNSEnv` and the generated config is chmod 600.
3. **Self-signed**: test use only (not publicly trusted).
4. **Existing cert files**: does not request or renew certificates.

Automatic certificate acquisition occurs **on the V2bX daemon**, not
during the wizard. Certificate paths are per-domain:
`/etc/V2bX/certs/<domain>/fullchain.cer` and
`/etc/V2bX/certs/<domain>/privkey.pem`. The containing directory is
chmod 700; do not share private keys through screenshots or issue comments.

**Caution**: the full config generator (option 15) still rebuilds the
config file, though it now creates a timestamped backup and validates JSON.
For an existing production node, use **V2bX anytls-add** to append without
replacing its other node definitions or restarting the service. The two
flows serve different purposes. Test on staging first, and make sure
Xboard has an AnyTLS node configured before enabling it.
