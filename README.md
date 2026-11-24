# V2bX
A V2board node server based on Xray-Core.

一个基于Xray的V2board节点服务端，支持V2ay,Trojan,Shadowsocks协议

Find the source code here: [InazumaV/V2bX](https://github.com/InazumaV/V2bX)

如对脚本不放心，可使用此沙箱先测一遍再使用：https://killercoda.com/playgrounds/scenario/ubuntu

# 详细使用教程

[教程](https://v2bx.v-50.me/)

## 安装与升级（测试分支）

在 **Debian 测试机**先验证。不要在生产机上直接执行来自可变分支的下载脚本。审核源码后，从受信任的仓库检出并固定提交。需要 Bash、Python 3、Git、curl 和系统 CA 证书；安装器不会自动修改系统软件包。

```bash
# 将占位值换成审核过的完整 Git 提交 SHA，再从已克隆的仓库执行。
REV='<完整 Git 提交 SHA>'
git checkout --detach "$REV"
sudo bash ./install.sh v0.4.0 --scripts-rev "$REV"
```

- 不默认追踪 `latest`。二进制使用指定 GitHub Release 的 SHA-256 校验；若没有可信的发布摘要，须以 `--sha256` 提供已核实的摘要，否则停止。摘要验证的是包内容与预期是否一致，不代表上游二进制经过安全审计。
- 首次安装不默认启动服务；审核配置后再启动。升级保留现有配置和证书文件；活动服务会进行切换检查，失败尝试回滚。未运行的服务不因升级自动启动，除非显式指定 `--start`。
- 已安装安全版后，用 `sudo V2bX update v0.4.0` 升级核心；只更新管理脚本和辅助工具时，从审核过的 checkout 运行 `sudo bash ./install.sh --scripts-only --scripts-rev "$REV"`，不重启服务。
- 生产升级前，请备份 `/etc/V2bX` 并安排维护窗口。服务进程检查不等于实际节点连通性或面板连接检查。

## 免费证书：默认不需要 Cloudflare Token

默认使用 Let's Encrypt **HTTP-01**。公网 TCP 80 必须能到达该域名的验证路径 `/.well-known/acme-challenge/`；同时检查已有的 A 和 AAAA 记录。如果 Nginx 已经监听 80，填能够实际提供验证文件的绝对 Webroot 路径。留空只在 80 能绑定时使用 standalone，不会停止 Nginx、调整防火墙或修改业务端口。

如果 80 不可达，选「使用已有证书」，填写证书和私钥路径。工具不会自动改用 DNS-01、下发 Cloudflare Token、自签名或关闭校验。既有 DNS-01 配置和续签机制保留；选用旧方式须主动选择，请为旧凭据做最小权限和轮换管理。

首次自动签发前，请在 systemd 主机上确认 Certbot 的续期 timer 已启用且正在运行，以及项目自带续期钩子已经安装。签发成功、域名及证书/私钥校验通过后才写入节点配置；续签时只处理引用该证书的 V2bX 服务。首次签发失败不覆盖节点。用 `certbot renew --cert-name <证书名> --dry-run` 测试续签，不把 ACME 测试证书发给客户端。上线前在独立节点验证一次签发、续签和服务切换。

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

Choose existing trusted certificate files, or use Certbot HTTP-01 when
TCP/80 is available. The helper does not restart V2bX, change iptables,
or alter Docker.
It intentionally refuses to rewrite JSON5-with-comments configuration.
An old V2bX executable or an outdated Xboard panel may not support this
protocol even if the menu shows it; check actual versions first.

**Important:** This is a script integration, not a new proxy engine. The
upstream binary is still downloaded from `wyx2685/V2bX`. The management
scripts and helpers are staged from the reviewed Git commit; do not replace a production
installation without testing. Keep configs and backups private because
they contain UniProxy API secrets.

## Integrated interactive AnyTLS / Let's Encrypt wizard

Run `V2bX generate`, or use **15. 生成 V2bX 配置文件** in the menu.
In the **请选择节点核心类型** prompt, select **4. AnyTLS (sing-box core)**.
The wizard asks for the Xboard AnyTLS Node ID, TLS domain and preferred
certificate method:

1. **Let's Encrypt HTTP-01** (default): supply a valid FQDN and email.
   Certbot validates the domain on public TCP/80 before the node is written.
   Use an existing webroot when a web server already serves that path, or
   standalone only when the port is free. Certbot manages renewal.
   NAT/DDNS hostname resolution alone cannot solve a missing TCP/80 mapping.
2. **Let's Encrypt DNS-01 (Cloudflare)**: supply FQDN, contact email and
   a Cloudflare `CLOUDFLARE_DNS_API_TOKEN` with Zone:Read and DNS:Edit
   permissions. No public TCP/80 port is required. Fresh Certbot DNS-01
   requests store a credential for future renewals in a root-only file,
   not in the generated V2bX node entry.
3. **Self-signed**: test use only (not publicly trusted).
4. **Existing cert files**: does not request or renew certificates.

For automatic issuance, Certbot obtains and validates the certificate during
the wizard; V2bX loads the resulting files. Do not share private keys through
screenshots or issue comments.

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

现已将附加 AnyTLS 节点工具的交互文案改为中文。UniProxy API Key 请在受信任的终端输入，不要录屏、截图或开启 Shell 跟踪。配置文件及其备份仅允许 root 读取（`chmod 600`）。

证书选择：
- **1 Let’s Encrypt HTTP-01**：提供域名、邮箱，以及现有 HTTP Webroot（可选，留空使用 standalone）；公网 TCP 80 必须可达。由 Certbot 签发和续签，不需要 Cloudflare Token。
- **2 Let’s Encrypt DNS-01（Cloudflare）**：提供域名、邮箱和 Cloudflare DNS API Token；不要求公网 TCP 80，适合 NAT，但必须拥有域名的 DNS 控制权。
- **3 已有证书**：需要在服务器上填写真实的 `fullchain.pem`（证书链）和 `privkey.pem`（私钥）绝对路径，工具会核对是否存在且密钥配对。

示例路径：

```text
/etc/letsencrypt/live/jp.example.com/fullchain.pem
/etc/letsencrypt/live/jp.example.com/privkey.pem
```

路径只是示例，不可直接用于其他域名。面板内的 SNI 必须与受信任的证书名称匹配（未启用不安全跳过校验时）。**AnyTLS 的 ECH 配置是独立功能，服务端证书申请不自动配置 ECH。** `anytls-add` 仅在证书校验通过后追加本地节点配置，不会自动重启服务。请先确认 Xboard 节点 ID、端口和服务端 TLS 参数，再在测试机手动重启。


## ACME 同步证书签发：成功才提交节点

通过 V2bX generate 或 V2bX anytls-add 选择 HTTP-01 / Cloudflare DNS-01 后：
- 检查 Certbot / OpenSSL / DNS 插件；缺少时需要确认后才安装 Debian 软件包。
- DNS-01 检查 Cloudflare Token 有权访问 DNS Zone。服务商的 NAT 子域名不能使用其他 Zone 的 Token。
- 立即运行 Certbot，显示 ACME 日志；仅在证书 SAN、有效期及证书/私钥匹配校验成功后，才生成节点配置。
- 成功后使用 CertMode=file，由 V2bX 读取工具返回的证书与私钥路径，不重复申请。新受管证书使用可回退的副本；已有直接引用 Certbot live 路径的配置不会自动迁移。
- 失败时停止添加节点，保留原来的 config.json，并恢复原 Cloudflare 凭据文件。Certbot 本身可能留下 ACME 日志和账户数据。

续期交给系统 certbot.timer。安装器/管理脚本更新会添加部署钩子 /etc/letsencrypt/renewal-hooks/deploy/95-v2bx-native-restart；它仅对 live config 正在使用的证书执行续期成功后的 V2bX 重启，仅迁移可确认来自本项目的旧版钩子，不覆盖自定义钩子。
检查续期：systemctl status certbot.timer --no-pager；certbot certificates。
注意：全量生成向导仍会重新创建 config.json 并重启服务；生产环境请先备份，优先使用只追加的 V2bX anytls-add。ECH 和端口映射也需要分别正确配置。

## 检查已经申请的 TLS 证书

同域名的 Certbot 证书只在名称匹配、证书与私钥配对且未过期时复用。向导不会把已登记的 DNS-01 证书切换成 HTTP-01，也不会从旧配置读出或显示 Token。临近到期时请检查该证书所属的续期任务；无效或过期时，先修复已有续期配置，不要盲目重复签发。

```bash
sudo certbot certificates
systemctl status certbot.timer --no-pager
sudo certbot renew --cert-name v2bx-node.example.com --dry-run
```

命令中的证书名只是示例，以 `certbot certificates` 实际显示为准。
