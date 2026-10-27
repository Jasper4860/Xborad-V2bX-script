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


## Hysteria2 self-signed certificate + pinSHA256 (native installer)

Run `V2bX generate` and select **3. hysteria2** (native Hysteria2 core) or
**2. singbox -> 5. Hysteria2**. When asked for the TLS certificate mode,
choose **5. 自签名 + SHA256 固定证书** (available only for Hysteria2).
Enter a locally managed certificate name (for example `jp.example.com`),
then the client SNI (defaults to that name), optional public NAT/DDNS host,
public UDP port, and optional *already forwarded* mport range.

The wizard installs/reuses a 365-day self-signed leaf certificate at
`/etc/V2bX/certs/hy2/<certificate-name>/{cert.pem,key.pem}`, prints the
full leaf certificate SHA-256 fingerprint, and uses V2bX `CertMode=file`.
It will **never silently rotate** an existing cert or key: this avoids
breaking all clients that pin the fingerprint. For renewal, explicitly
replace the cert/key and update pinned fingerprints in every client.

```bash
# View local node certificate fingerprints (no service restart):
V2bX hy2-pin-show

# Inspect a particular local certificate:
python3 /usr/local/V2bX/hy2_pin_manager.py show \\
  --cert /etc/V2bX/certs/hy2/jp.example.com/cert.pem

# To activate a generated config on a TEST server after review:
systemctl restart V2bX
journalctl -u V2bX -n 100 --no-pager
```

The printed connection URI is a **template** with `YOUR_XBOARD_USER_PASSWORD`,
not a working subscription link. Set the same client SNI in the Xboard
node's TLS settings. Xboard's default General Hysteria2 subscription
exporter does not currently emit `pinSHA256` automatically; do not
publish `insecure=1` subscriptions without a matching pin or otherwise
trusted CA certificate. Integrating pin distribution into Xboard's
subscription exporters is a separate change, NOT included here.

The **mport** parameter is an optional *example only*: no UDP port range
is opened or forwarded by this script. Public NAT mappings and firewall
forwarding must exist and all chosen UDP ports must land on the same
Hysteria2 server. If only one UDP port is mapped, leave mport empty.

This workflow uses the existing native V2bX Hysteria2 server and **does
not require installing AnyTLS-Go, another Hysteria2 service, or Let's Encrypt**.
The previous Let's Encrypt HTTP-01 and Cloudflare DNS-01 options remain.

**Safety**: The original full config wizard regenerates the config and may
restart V2bX. Back up live configuration and test changes on staging.
The new certificate helper itself never restarts V2bX or changes iptables.


## 中文 AnyTLS 添加向导（`V2bX anytls-add`）

现已将附加 AnyTLS 节点工具的交互文案改为中文。UniProxy API Key 使用**可见输入**，并要求显式确认。请避免在共享终端、录屏、截图、Shell 日志中暴露密钥；配置文件及其备份将设置为 root-only (`chmod 600`)。

证书选择：
- **1 Let’s Encrypt HTTP-01**：提供域名、邮箱，配置由 V2bX 首次启动时签发；公网 TCP 80 必须到达本机。
- **2 Let’s Encrypt DNS-01（Cloudflare）**：提供域名、邮箱和 Cloudflare DNS API Token；不要求公网 TCP 80，适合 NAT，但必须拥有域名的 DNS 控制权。
- **3 已有证书**：需要在服务器上填写真实的 `fullchain.pem`（证书链）和 `privkey.pem`（私钥）绝对路径，工具会核对是否存在且密钥配对。

示例路径：

```text
/etc/letsencrypt/live/jp.example.com/fullchain.pem
/etc/letsencrypt/live/jp.example.com/privkey.pem
```

路径只是示例，不可直接用于其他域名。面板内的 SNI 必须与受信任的证书名称匹配（未启用不安全跳过校验时）。**AnyTLS 的 ECH 配置是独立功能，服务端证书申请不自动配置 ECH。** `anytls-add` 仅追加本地节点配置，不会立即申请证书或自动重启。请先确认 Xboard 节点 ID、端口和服务端 TLS 参数，再在测试机手动重启。


## ACME 同步证书签发：成功才提交节点

通过 V2bX generate 或 V2bX anytls-add 选择 HTTP-01 / Cloudflare DNS-01 后：
- 检查 Certbot / OpenSSL / DNS 插件；缺少时需要确认后才安装 Debian 软件包。
- DNS-01 检查 Cloudflare Token 有权访问 DNS Zone。服务商的 NAT 子域名不能使用其他 Zone 的 Token。
- 立即运行 Certbot，显示 ACME 日志；仅在证书 SAN、有效期及证书/私钥匹配校验成功后，才生成节点配置。
- 成功后 CertMode=file，从 /etc/letsencrypt/live/v2bx-<域名>/fullchain.pem 与 privkey.pem 读取证书；不再让 V2bX 重复申请。
- 失败时停止添加节点，保留原来的 config.json，并恢复原 Cloudflare 凭据文件。Certbot 本身可能留下 ACME 日志和账户数据。

续期交给系统 certbot.timer。安装器/管理脚本更新会添加部署钩子 /etc/letsencrypt/renewal-hooks/deploy/95-v2bx-native-restart；它仅对 live config 正在使用的证书执行续期成功后的 V2bX 重启，已有同名钩子不会覆盖。
检查续期：systemctl status certbot.timer --no-pager；certbot certificates。
注意：全量生成向导仍会重新创建 config.json 并重启服务；生产环境请先备份，优先使用只追加的 V2bX anytls-add。ECH 和端口映射也需要分别正确配置。

## 自动检测和复用已经申请的 TLS 证书

现在运行 `V2bX generate` 或 `V2bX anytls-add` 时，选择 HTTP-01 / DNS-01 并输入域名后，会先执行只读 `acme_cert_manager.py reuse --domain <域名>`。
若 `/etc/letsencrypt/live/v2bx-<域名>/{fullchain.pem,privkey.pem}` 已存在、证书与私钥匹配、SAN 含当前域名且剩余有效期超过 30 天，直接设为 CertMode=file，不会再次调用 Certbot、Cloudflare API，也不会再要求输入联系邮箱或 DNS Token。
若证书缺失、校验失败、到期或剩余有效期不足 30 天，则保留原来的签发/续期流程，仍须提供具有 DNS 区域权限的 Cloudflare Token（DNS-01）或公网 80 端口（HTTP-01）。
这个行为不会自动复用无关域名、其他 Certbot lineage 或用户自行放置的证书；对这类证书请显式选择已有文件模式。
直接查看是否能复用：`python3 /usr/local/V2bX/acme_cert_manager.py reuse --domain wuya-162141-hk.5708888.xyz`。返回码 0 表示可复用；返回 1 表示需要重新配置/续期。
