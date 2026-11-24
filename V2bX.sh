#!/bin/bash

red='\033[0;31m'
green='\033[0;32m'
yellow='\033[0;33m'
plain='\033[0m'

# check root
[[ $EUID -ne 0 ]] && echo -e "${red}错误: ${plain} 必须使用root用户运行此脚本！\n" && exit 1

# check os
if [[ -f /etc/redhat-release ]]; then
    release="centos"
elif cat /etc/issue | grep -Eqi "alpine"; then
    release="alpine"
elif cat /etc/issue | grep -Eqi "debian"; then
    release="debian"
elif cat /etc/issue | grep -Eqi "ubuntu"; then
    release="ubuntu"
elif cat /etc/issue | grep -Eqi "centos|red hat|redhat|rocky|alma|oracle linux"; then
    release="centos"
elif cat /proc/version | grep -Eqi "debian"; then
    release="debian"
elif cat /proc/version | grep -Eqi "ubuntu"; then
    release="ubuntu"
elif cat /proc/version | grep -Eqi "centos|red hat|redhat|rocky|alma|oracle linux"; then
    release="centos"
elif cat /proc/version | grep -Eqi "arch"; then
    release="arch"
else
    echo -e "${red}未检测到系统版本，请联系脚本作者！${plain}\n" && exit 1
fi

# os version
if [[ -f /etc/os-release ]]; then
    os_version=$(awk -F'[= ."]' '/VERSION_ID/{print $3}' /etc/os-release)
fi
if [[ -z "$os_version" && -f /etc/lsb-release ]]; then
    os_version=$(awk -F'[= ."]+' '/DISTRIB_RELEASE/{print $2}' /etc/lsb-release)
fi

if [[ x"${release}" == x"centos" ]]; then
    if [[ ${os_version} -le 6 ]]; then
        echo -e "${red}请使用 CentOS 7 或更高版本的系统！${plain}\n" && exit 1
    fi
    if [[ ${os_version} -eq 7 ]]; then
        echo -e "${red}注意： CentOS 7 无法使用hysteria1/2协议！${plain}\n"
    fi
elif [[ x"${release}" == x"ubuntu" ]]; then
    if [[ ${os_version} -lt 16 ]]; then
        echo -e "${red}请使用 Ubuntu 16 或更高版本的系统！${plain}\n" && exit 1
    fi
elif [[ x"${release}" == x"debian" ]]; then
    if [[ ${os_version} -lt 8 ]]; then
        echo -e "${red}请使用 Debian 8 或更高版本的系统！${plain}\n" && exit 1
    fi
fi

# 检查系统是否有 IPv6 地址
check_ipv6_support() {
    if ip -6 addr | grep -q "inet6"; then
        echo "1"  # 支持 IPv6
    else
        echo "0"  # 不支持 IPv6
    fi
}

confirm() {
    if [[ $# > 1 ]]; then
        echo && read -rp "$1 [默认$2]: " temp
        if [[ x"${temp}" == x"" ]]; then
            temp=$2
        fi
    else
        read -rp "$1 [y/n]: " temp
    fi
    if [[ x"${temp}" == x"y" || x"${temp}" == x"Y" ]]; then
        return 0
    else
        return 1
    fi
}

confirm_restart() {
    confirm "是否重启V2bX" "y"
    if [[ $? == 0 ]]; then
        restart
    else
        show_menu
    fi
}

before_show_menu() {
    echo && echo -n -e "${yellow}按回车返回主菜单: ${plain}" && read temp
    show_menu
}

install() {
    echo "请先检出可信脚本提交，然后从该目录运行："
    echo "  sudo bash ./install.sh v0.4.0 --scripts-rev <40位提交SHA>"
    echo "安装后检查 /etc/V2bX/config.json，再用 V2bX start 启动；需自动启动可显式使用 --start。"
}

run_installer() {
    local helper=/usr/local/V2bX/install_manager.py
    if [[ ! -f "$helper" ]] || ! command -v python3 >/dev/null; then
        echo -e "${red}缺少安全安装管理器或 Python 3；请从可信 Git 提交运行 install.sh。${plain}" >&2
        return 1
    fi
    (export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; umask 077; python3 -I "$helper" "$@")
}

update() {
    local version
    if [[ $# == 0 ]]; then
        read -r -p "输入明确版本（例如 v0.4.0；留空取消）: " version || return 1
        [[ -n "$version" ]] || return 1
        run_installer --core-only "$version"
    else
        run_installer --core-only "$@"
    fi
}

config() {
    echo "V2bX在修改配置后会自动尝试重启"
    vi /etc/V2bX/config.json
    sleep 2
    restart
    check_status
    case $? in
        0)
            echo -e "V2bX状态: ${green}已运行${plain}"
            ;;
        1)
            echo -e "检测到您未启动V2bX或V2bX自动重启失败，是否查看日志？[Y/n]" && echo
            read -e -rp "(默认: y):" yn
            [[ -z ${yn} ]] && yn="y"
            if [[ ${yn} == [Yy] ]]; then
               show_log
            fi
            ;;
        2)
            echo -e "V2bX状态: ${red}未安装${plain}"
    esac
}

uninstall() {
    confirm "确定要卸载 V2bX 吗?" "n"
    if [[ $? != 0 ]]; then
        if [[ $# == 0 ]]; then
            show_menu
        fi
        return 0
    fi
    if [[ x"${release}" == x"alpine" ]]; then
        service V2bX stop
        rc-update del V2bX
        rm /etc/init.d/V2bX -f
    else
        systemctl stop V2bX
        systemctl disable V2bX
        rm /etc/systemd/system/V2bX.service -f
        systemctl daemon-reload
        systemctl reset-failed
    fi
    rm /etc/V2bX/ -rf
    rm /usr/local/V2bX/ -rf

    echo ""
    echo -e "卸载成功，如果你想删除此脚本，则退出脚本后运行 ${green}rm /usr/bin/V2bX -f${plain} 进行删除"
    echo ""

    if [[ $# == 0 ]]; then
        before_show_menu
    fi
}

start() {
    check_status
    if [[ $? == 0 ]]; then
        echo ""
        echo -e "${green}V2bX已运行，无需再次启动，如需重启请选择重启${plain}"
    else
        if [[ x"${release}" == x"alpine" ]]; then
            service V2bX start
        else
            systemctl start V2bX
        fi
        sleep 2
        check_status
        if [[ $? == 0 ]]; then
            echo -e "${green}V2bX 启动成功，请使用 V2bX log 查看运行日志${plain}"
        else
            echo -e "${red}V2bX可能启动失败，请稍后使用 V2bX log 查看日志信息${plain}"
        fi
    fi

    if [[ $# == 0 ]]; then
        before_show_menu
    fi
}

stop() {
    if [[ x"${release}" == x"alpine" ]]; then
        service V2bX stop
    else
        systemctl stop V2bX
    fi
    sleep 2
    check_status
    if [[ $? == 1 ]]; then
        echo -e "${green}V2bX 停止成功${plain}"
    else
        echo -e "${red}V2bX停止失败，可能是因为停止时间超过了两秒，请稍后查看日志信息${plain}"
    fi

    if [[ $# == 0 ]]; then
        before_show_menu
    fi
}

restart() {
    if [[ x"${release}" == x"alpine" ]]; then
        service V2bX restart
    else
        systemctl restart V2bX
    fi
    sleep 2
    check_status
    if [[ $? == 0 ]]; then
        echo -e "${green}V2bX 重启成功，请使用 V2bX log 查看运行日志${plain}"
    else
        echo -e "${red}V2bX可能启动失败，请稍后使用 V2bX log 查看日志信息${plain}"
    fi
    if [[ $# == 0 ]]; then
        before_show_menu
    fi
}

status() {
    if [[ x"${release}" == x"alpine" ]]; then
        service V2bX status
    else
        systemctl status V2bX --no-pager -l
    fi
    if [[ $# == 0 ]]; then
        before_show_menu
    fi
}

enable() {
    if [[ x"${release}" == x"alpine" ]]; then
        rc-update add V2bX
    else
        systemctl enable V2bX
    fi
    if [[ $? == 0 ]]; then
        echo -e "${green}V2bX 设置开机自启成功${plain}"
    else
        echo -e "${red}V2bX 设置开机自启失败${plain}"
    fi

    if [[ $# == 0 ]]; then
        before_show_menu
    fi
}

disable() {
    if [[ x"${release}" == x"alpine" ]]; then
        rc-update del V2bX
    else
        systemctl disable V2bX
    fi
    if [[ $? == 0 ]]; then
        echo -e "${green}V2bX 取消开机自启成功${plain}"
    else
        echo -e "${red}V2bX 取消开机自启失败${plain}"
    fi

    if [[ $# == 0 ]]; then
        before_show_menu
    fi
}

show_log() {
    if [[ x"${release}" == x"alpine" ]]; then
        echo -e "${red}alpine系统暂不支持日志查看${plain}\n" && exit 1
    else
        journalctl -u V2bX.service -e --no-pager -f
    fi
    if [[ $# == 0 ]]; then
        before_show_menu
    fi
}

install_bbr() {
    bash <(curl -L -s https://github.com/ylx2016/Linux-NetSpeed/raw/master/tcpx.sh)
}

update_shell() {
    local checkout revision
    if [[ $# == 0 ]]; then
        read -r -p "可信本地 Git 工作副本的路径: " checkout || return 1
        read -r -p "脚本提交的完整 40 位 SHA: " revision || return 1
    elif [[ $# == 2 ]]; then
        checkout="$1"
        revision="$2"
    else
        echo "用法：V2bX update_shell <可信本地 Git 工作副本路径> <40位提交SHA>" >&2
        return 1
    fi
    run_installer --scripts-only --source "$checkout" --scripts-rev "$revision"
}

# 0: running, 1: not running, 2: not installed
check_status() {
    if [[ ! -f /usr/local/V2bX/V2bX ]]; then
        return 2
    fi
    if [[ x"${release}" == x"alpine" ]]; then
        temp=$(service V2bX status | awk '{print $3}')
        if [[ x"${temp}" == x"started" ]]; then
            return 0
        else
            return 1
        fi
    else
        temp=$(systemctl status V2bX | grep Active | awk '{print $3}' | cut -d "(" -f2 | cut -d ")" -f1)
        if [[ x"${temp}" == x"running" ]]; then
            return 0
        else
            return 1
        fi
    fi
}

check_enabled() {
    if [[ x"${release}" == x"alpine" ]]; then
        temp=$(rc-update show | grep V2bX)
        if [[ x"${temp}" == x"" ]]; then
            return 1
        else
            return 0
        fi
    else
        temp=$(systemctl is-enabled V2bX)
        if [[ x"${temp}" == x"enabled" ]]; then
            return 0
        else
            return 1;
        fi
    fi
}

check_uninstall() {
    check_status
    if [[ $? != 2 ]]; then
        echo ""
        echo -e "${red}V2bX已安装，请不要重复安装${plain}"
        if [[ $# == 0 ]]; then
            before_show_menu
        fi
        return 1
    else
        return 0
    fi
}

check_install() {
    check_status
    if [[ $? == 2 ]]; then
        echo ""
        echo -e "${red}请先安装V2bX${plain}"
        if [[ $# == 0 ]]; then
            before_show_menu
        fi
        return 1
    else
        return 0
    fi
}

show_status() {
    check_status
    case $? in
        0)
            echo -e "V2bX状态: ${green}已运行${plain}"
            show_enable_status
            ;;
        1)
            echo -e "V2bX状态: ${yellow}未运行${plain}"
            show_enable_status
            ;;
        2)
            echo -e "V2bX状态: ${red}未安装${plain}"
    esac
}

show_enable_status() {
    check_enabled
    if [[ $? == 0 ]]; then
        echo -e "是否开机自启: ${green}是${plain}"
    else
        echo -e "是否开机自启: ${red}否${plain}"
    fi
}

generate_x25519_key() {
    echo -n "正在生成 x25519 密钥："
    /usr/local/V2bX/V2bX x25519
    echo ""
    if [[ $# == 0 ]]; then
        before_show_menu
    fi
}

show_V2bX_version() {
    echo -n "V2bX 版本："
    /usr/local/V2bX/V2bX version
    echo ""
    if [[ $# == 0 ]]; then
        before_show_menu
    fi
}


# Check an ASCII TLS hostname before using it in certificate paths and JSON.
valid_tls_domain() {
    local domain="$1" label
    local -a parts=()
    [[ "${#domain}" -le 253 && "$domain" == *.* && "$domain" != *..* ]] || return 1
    IFS='.' read -r -a parts <<< "$domain"
    for label in "${parts[@]}"; do
        [[ "$label" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$ ]] || return 1
    done
}

valid_tls_email() {
    [[ "$1" =~ ^[A-Za-z0-9._%+~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]]
}

# Called by add_node_config. Variables are dynamically scoped to that function.
use_existing_node_certificate() {
    local helper="$1"
    while true; do
        read -r -p "请输入现有 fullchain.pem/cert 文件绝对路径：" certfile || return 1
        read -r -p "请输入现有私钥文件绝对路径：" keyfile || return 1
        # These shell wizards interpolate paths into a JSON template.
        if [[ "$certfile" =~ ^/[A-Za-z0-9._/-]+$ && "$keyfile" =~ ^/[A-Za-z0-9._/-]+$ ]] &&
           python3 "$helper" validate --domain "$certdomain" --cert "$certfile" --key "$keyfile"; then
            certmode="file"
            certemail=""
            certprovider=""
            certdnsenv="{}"
            echo "使用已有证书；本向导不会接管其续期。"
            return 0
        fi
        echo -e "${red}请提供与域名匹配的有效证书和私钥；路径须为绝对路径且不含空格。回车可取消。${plain}"
        [[ -n "$certfile" && -n "$keyfile" ]] || return 1
    done
}

configure_node_tls() {
    local selection cf_token="" helper paths webroot="" fallback=""
    local tracked=false issued=false
    local -a extra_args=()
    echo -e "${yellow}请选择 TLS 证书方式：${plain}"
    echo -e "${green}1. Let's Encrypt HTTP-01（默认，无需 Cloudflare Token）${plain}"
    echo -e "${green}2. Let's Encrypt DNS-01（可选，Cloudflare API Token）${plain}"
    echo -e "${green}3. self 自签名证书（仅测试，客户端需单独信任）${plain}"
    echo -e "${green}4. 使用已有证书文件（由原方式维护）${plain}"
    if [[ "$NodeType" == "hysteria2" ]]; then
        echo -e "${green}5. 自签名 + SHA256 固定证书（自动生成或复用）${plain}"
    fi
    while true; do
        read -r -p "请选择证书方式（默认 1）：" selection || return 1
        selection="${selection:-1}"
        case "$selection" in
            1) certmode="http"; break ;;
            2) certmode="dns"; break ;;
            3) certmode="self"; break ;;
            4) certmode="file"; break ;;
            5) if [[ "$NodeType" == "hysteria2" ]]; then
                   certmode="file"; hy2_pin_enabled=true; break
               fi
               echo "只有 hysteria2 可选择选项 5。" ;;
            *) echo "请选择以上列出的证书方式。" ;;
        esac
    done

    while true; do
        read -r -p "请输入 TLS 证书域名（例如 node.example.com）：" certdomain || return 1
        certdomain="${certdomain,,}"
        if valid_tls_domain "$certdomain"; then break; fi
        echo -e "${red}域名无效：请填写有 DNS 解析的完整域名，不含 https:// 和端口。${plain}"
    done

    if [[ "${hy2_pin_enabled:-false}" == "true" ]]; then
        helper="/usr/local/V2bX/hy2_pin_manager.py"
        if [[ ! -f "$helper" ]]; then
            helper="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/tools/hy2_pin_manager.py"
        fi
        if [[ ! -f "$helper" ]]; then echo -e "${red}找不到 HY2 证书管理器，请更新安装脚本。${plain}"; return 1; fi
        if ! command -v openssl >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
            echo -e "${red}需要 openssl 和 python3${plain}"; return 1
        fi
        local hy2_sni hy2_host hy2_port hy2_mport
        read -r -p "客户端 SNI（回车默认证书域名）：" hy2_sni || return 1
        hy2_sni="${hy2_sni:-$certdomain}"
        read -r -p "公网 NAT/DDNS 连接域名（可留空）：" hy2_host || return 1
        read -r -p "公网 UDP 连接端口（可留空）：" hy2_port || return 1
        read -r -p "已映射的 mport UDP 端口范围（可留空）：" hy2_mport || return 1
        if ! python3 "$helper" issue --domain "$certdomain" --sni "$hy2_sni" --host "$hy2_host" --port "$hy2_port" --mport "$hy2_mport"; then
            echo -e "${red}证书生成/校验失败，取消添加节点。${plain}"; return 1
        fi
        certmode="file"
        certfile="/etc/V2bX/certs/hy2/$certdomain/cert.pem"
        keyfile="/etc/V2bX/certs/hy2/$certdomain/key.pem"
        echo -e "${yellow}请将 Xboard SNI 同步设为 $hy2_sni，并确保订阅下发 pinSHA256。${plain}"
        return 0
    fi
    if [[ "$certmode" == "self" ]]; then
        echo -e "${yellow}自签名证书仅用于测试，不属于 Let's Encrypt。${plain}"
        certfile="/etc/V2bX/certs/$certdomain/fullchain.cer"
        keyfile="/etc/V2bX/certs/$certdomain/privkey.pem"
        mkdir -p "/etc/V2bX/certs/$certdomain" && chmod 0700 "/etc/V2bX/certs/$certdomain"
        return $?
    fi

    helper="/usr/local/V2bX/acme_cert_manager.py"
    if [[ ! -f "$helper" ]]; then
        helper="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/tools/acme_cert_manager.py"
    fi
    if [[ ! -f "$helper" ]] || ! command -v python3 >/dev/null 2>&1; then
        echo -e "${red}缺少证书校验工具或 python3；未添加节点。${plain}"
        return 1
    fi
    if [[ "$certmode" == "file" ]]; then
        use_existing_node_certificate "$helper"
        return $?
    fi

    if python3 "$helper" reuse --domain "$certdomain"; then
        issued=true
    elif python3 "$helper" tracked --domain "$certdomain"; then
        tracked=true
        echo "发现 Certbot 续期配置，将保留已有验证方式和凭据。"
    else
        if [[ "$certmode" == "http" ]]; then
            echo "HTTP-01 需要公网 TCP/80；已有站点时请选择实际服务挑战目录的 Webroot。"
            read -r -p "已有站点 Webroot 绝对路径（回车仅在 TCP/80 空闲时用 standalone）：" webroot || return 1
            if [[ -n "$webroot" ]]; then extra_args+=(--webroot "$webroot"); fi
        fi
        while true; do
            read -r -p "请输入 Let's Encrypt 联系邮箱（可回车跳过）：" certemail || return 1
            if [[ -z "$certemail" ]] || valid_tls_email "$certemail"; then break; fi
            echo -e "${red}邮箱格式无效，请重输。${plain}"
        done
        if [[ "$certmode" == "dns" ]]; then
            echo "Cloudflare DNS-01 需要对应 Zone 的 Zone:Read 与 DNS:Edit，Token 不写入节点配置。"
            read -r -s -p "Cloudflare DNS API Token（隐藏输入）：" cf_token || return 1
            echo
            [[ "$cf_token" =~ ^[A-Za-z0-9_-]+$ ]] || return 1
        fi
    fi
    if [[ "$issued" != true ]]; then
        if [[ "$tracked" == true ]] || python3 "$helper" deps --mode "$certmode"; then
            if [[ "$certmode" == "dns" && "$tracked" != true ]]; then
                if printf '%s\n' "$cf_token" | python3 "$helper" issue --mode dns --domain "$certdomain" --email "$certemail"; then
                    issued=true
                fi
            elif python3 "$helper" issue --mode "$certmode" --domain "$certdomain" --email "$certemail" "${extra_args[@]}"; then
                issued=true
            fi
        fi
        unset cf_token
    fi
    if [[ "$issued" != true ]]; then
        read -r -p "自动证书不可用，改用已有证书和私钥？[y/N]：" fallback || return 1
        [[ "${fallback,,}" == y ]] || return 1
        use_existing_node_certificate "$helper"
        return $?
    fi

    paths="$(python3 "$helper" paths --domain "$certdomain")" || return 1
    IFS='|' read -r certfile keyfile <<< "$paths"
    [[ -n "$certfile" && -n "$keyfile" ]] || return 1
    certmode="file"
    certemail=""
    certprovider=""
    certdnsenv="{}"
    echo -e "${green}已校验证书；使用文件模式，由 Certbot 按原验证方式续期。${plain}"
    return 0
}

add_node_config() {
    local core_type core NodeID NodeType protocol_choice
    local direct_anytls=false isreality="" istls="" fastopen=true
    local certmode="none" certdomain="example.com" certemail=""
    local certfile="/etc/V2bX/fullchain.cer" keyfile="/etc/V2bX/cert.key"
    local certprovider="" certdnsenv="{}" hy2_line=""
    local hy2_pin_enabled=false

    while true; do
        echo -e "${green}请选择节点核心类型：${plain}"
        echo -e "${green}1. xray${plain}"
        echo -e "${green}2. singbox${plain}"
        echo -e "${green}3. hysteria2${plain}"
        echo -e "${green}4. AnyTLS (sing-box 核心，一键配置)${plain}"
        read -r -p "请输入 [1-4]：" core_type || return 1
        case "$core_type" in
            1) core="xray"; core_xray=true; break ;;
            2) core="sing"; core_sing=true; break ;;
            3) core="hysteria2"; core_hysteria2=true; break ;;
            4) core="sing"; core_type="2"; core_sing=true; direct_anytls=true; break ;;
            *) echo "无效选择，请输入 1-4。" ;;
        esac
    done

    while true; do
        read -r -p "请输入 Xboard 节点 Node ID：" NodeID || return 1
        if [[ "$NodeID" =~ ^[1-9][0-9]*$ && "${#NodeID}" -le 10 ]] && (( NodeID <= 2147483647 )); then
            break
        fi
        echo "请输入有效的正整数 Node ID。"
    done

    if [[ "$direct_anytls" == "true" ]]; then
        NodeType="anytls"
    elif [[ "$core_type" == "3" ]]; then
        NodeType="hysteria2"
    else
        while true; do
            echo -e "${yellow}请选择节点传输协议：${plain}"
            echo -e "${green}1. Shadowsocks  2. Vless  3. Vmess  6. Trojan${plain}"
            if [[ "$core" == "sing" ]]; then
                echo -e "${green}4. Hysteria  5. Hysteria2  7. Tuic  8. AnyTLS${plain}"
            fi
            read -r -p "请输入：" protocol_choice || return 1
            case "$protocol_choice" in
                1) NodeType="shadowsocks" ;;
                2) NodeType="vless" ;;
                3) NodeType="vmess" ;;
                6) NodeType="trojan" ;;
                4) [[ "$core" == "sing" ]] && NodeType="hysteria" ;;
                5) [[ "$core" == "sing" ]] && NodeType="hysteria2" ;;
                7) [[ "$core" == "sing" ]] && NodeType="tuic" ;;
                8) [[ "$core" == "sing" ]] && NodeType="anytls" ;;
            esac
            if [[ -n "$NodeType" ]]; then break; fi
            echo "当前核心不支持该协议，请重新选择。"
        done
    fi

    if [[ "$NodeType" == "vless" ]]; then
        read -r -p "是否使用 Reality？(y/N)：" isreality || return 1
    elif [[ "$NodeType" == "hysteria" || "$NodeType" == "hysteria2" || "$NodeType" == "tuic" || "$NodeType" == "anytls" ]]; then
        fastopen=false
        istls="y"
    fi
    if [[ ! "$isreality" =~ ^[Yy]$ && "$istls" != "y" ]]; then
        read -r -p "是否启用 TLS？(y/N)：" istls || return 1
    fi
    if [[ ! "$isreality" =~ ^[Yy]$ && "$istls" =~ ^[Yy]$ ]]; then
        configure_node_tls || return 1
    fi

    local listen_ip="0.0.0.0"
    if [[ "$NodeType" != "anytls" ]] && [[ "$(check_ipv6_support)" == "1" ]]; then
        listen_ip="::"
    fi
    local node_config=""
    if [[ "$core" == "xray" ]]; then
        node_config=$(cat <<EOF
{
  "Core": "$core",
  "ApiHost": "$ApiHost",
  "ApiKey": "$ApiKey",
  "NodeID": $NodeID,
  "NodeType": "$NodeType",
  "Timeout": 30,
  "ListenIP": "0.0.0.0",
  "SendIP": "0.0.0.0",
  "DeviceOnlineMinTraffic": 200,
  "MinReportTraffic": 0,
  "EnableProxyProtocol": false,
  "EnableUot": true,
  "EnableTFO": true,
  "DNSType": "UseIPv4",
  "CertConfig": {
    "CertMode": "$certmode", "RejectUnknownSni": false,
    "CertDomain": "$certdomain", "CertFile": "$certfile",
    "KeyFile": "$keyfile", "Email": "$certemail",
    "Provider": "$certprovider", "DNSEnv": $certdnsenv
  }
},
EOF
)
    elif [[ "$core" == "sing" ]]; then
        node_config=$(cat <<EOF
{
  "Core": "$core",
  "ApiHost": "$ApiHost",
  "ApiKey": "$ApiKey",
  "NodeID": $NodeID,
  "NodeType": "$NodeType",
  "Timeout": 30,
  "ListenIP": "$listen_ip",
  "SendIP": "0.0.0.0",
  "DeviceOnlineMinTraffic": 200,
  "MinReportTraffic": 0,
  "TCPFastOpen": $fastopen,
  "SniffEnabled": true,
  "CertConfig": {
    "CertMode": "$certmode", "RejectUnknownSni": false,
    "CertDomain": "$certdomain", "CertFile": "$certfile",
    "KeyFile": "$keyfile", "Email": "$certemail",
    "Provider": "$certprovider", "DNSEnv": $certdnsenv
  }
},
EOF
)
    elif [[ "$core" == "hysteria2" ]]; then
        node_config=$(cat <<EOF
{
  "Core": "$core",
  "ApiHost": "$ApiHost",
  "ApiKey": "$ApiKey",
  "NodeID": $NodeID,
  "NodeType": "$NodeType",
  "Hysteria2ConfigPath": "/etc/V2bX/hy2config.yaml",
  "Timeout": 30,
  "ListenIP": "",
  "SendIP": "0.0.0.0",
  "DeviceOnlineMinTraffic": 200,
  "MinReportTraffic": 0,
  "CertConfig": {
    "CertMode": "$certmode", "RejectUnknownSni": false,
    "CertDomain": "$certdomain", "CertFile": "$certfile",
    "KeyFile": "$keyfile", "Email": "$certemail",
    "Provider": "$certprovider", "DNSEnv": $certdnsenv
  }
},
EOF
)
    fi

    nodes_config+=("$node_config")
    echo -e "${green}已添加 Node $NodeID：$NodeType（核心 $core，证书方式 $certmode）${plain}"
    if [[ "$certmode" == "file" ]]; then
        echo -e "${green}证书模式 file；证书已由向导校验或由 Certbot 申请。${plain}"
    fi
    return 0
}

generate_config_file() {
    echo -e "${yellow}V2bX 配置文件生成向导${plain}"
    echo -e "${red}请阅读以下注意事项：${plain}"
    echo -e "${red}1. 目前该功能正处测试阶段${plain}"
    echo -e "${red}2. 生成的配置文件会保存到 /etc/V2bX/config.json${plain}"
    echo -e "${red}3. 原来的配置文件会保存到 /etc/V2bX/config.json.bak${plain}"
    echo -e "${red}4. 目前仅部分支持TLS${plain}"
    echo -e "${red}5. 使用此功能生成的配置文件会自带审计，确定继续？(y/n)${plain}"
    read -rp "请输入：" continue_prompt
    if [[ "$continue_prompt" =~ ^[Nn][Oo]? ]]; then
        exit 0
    fi
    
    nodes_config=()
    first_node=true
    core_xray=false
    core_sing=false
    core_hysteria2=false
    fixed_api_info=false
    check_api=false
    
    while true; do
        if [ "$first_node" = true ]; then
            read -rp "请输入机场网址(https://example.com)：" ApiHost
            read -rp "请输入面板对接API Key：" ApiKey
            read -rp "是否设置固定的机场网址和API Key？(y/n)" fixed_api
            if [ "$fixed_api" = "y" ] || [ "$fixed_api" = "Y" ]; then
                fixed_api_info=true
                echo -e "${red}成功固定地址${plain}"
            fi
            first_node=false
            if ! add_node_config; then
                echo -e "${red}添加节点失败，停止向导，原 config.json 未被修改。${plain}"
                return 1
            fi
        else
            read -rp "是否继续添加节点配置？(回车继续，输入n或no退出)" continue_adding_node
            if [[ "$continue_adding_node" =~ ^[Nn][Oo]? ]]; then
                break
            elif [ "$fixed_api_info" = false ]; then
                read -rp "请输入机场网址：" ApiHost
                read -rp "请输入面板对接API Key：" ApiKey
            fi
            if ! add_node_config; then
                echo -e "${red}添加节点失败，停止向导，原 config.json 未被修改。${plain}"
                return 1
            fi
        fi
    done

    # 初始化核心配置数组
    cores_config="["

    # 检查并添加xray核心配置
    if [ "$core_xray" = true ]; then
        cores_config+="
    {
        \"Type\": \"xray\",
        \"Log\": {
            \"Level\": \"error\",
            \"ErrorPath\": \"/etc/V2bX/error.log\"
        },
        \"OutboundConfigPath\": \"/etc/V2bX/custom_outbound.json\",
        \"RouteConfigPath\": \"/etc/V2bX/route.json\"
    },"
    fi

    # 检查并添加sing核心配置
    if [ "$core_sing" = true ]; then
        cores_config+="
    {
        \"Type\": \"sing\",
        \"Log\": {
            \"Level\": \"error\",
            \"Timestamp\": true
        },
        \"NTP\": {
            \"Enable\": false,
            \"Server\": \"time.apple.com\",
            \"ServerPort\": 0
        },
        \"OriginalPath\": \"/etc/V2bX/sing_origin.json\"
    },"
    fi

    # 检查并添加hysteria2核心配置
    if [ "$core_hysteria2" = true ]; then
        cores_config+="
    {
        \"Type\": \"hysteria2\",
        \"Log\": {
            \"Level\": \"error\"
        }
    },"
    fi

    # 移除最后一个逗号并关闭数组
    cores_config+="]"
    cores_config=$(echo "$cores_config" | sed 's/},]$/}]/')

    # 切换到配置文件目录
    cd /etc/V2bX
    
    # 备份旧的配置文件，并保护其中的 Xboard API Key / DNS Token。
    # 不复用旧备份文件名，避免覆盖之前的救援点。
    local backup_path="/etc/V2bX/config.json.bak.$(date -u +%Y%m%d%H%M%S).$"
    if [[ -f /etc/V2bX/config.json ]]; then
        cp -p /etc/V2bX/config.json "$backup_path" || return 1
        chmod 600 "$backup_path" || return 1
        echo -e "${yellow}原配置已经备份到：$backup_path${plain}"
    fi
    nodes_config_str="${nodes_config[*]}"
    formatted_nodes_config="${nodes_config_str%,}"

    # 创建 config.json 文件
    # Restrict secrets and avoid exposing them through a newly created config.
    umask 077
    local staged_conf
    staged_conf=$(mktemp /etc/V2bX/.config.json.XXXXXXXX) || return 1
    cat <<EOF > "$staged_conf"
{
    "Log": {
        "Level": "error",
        "Output": ""
    },
    "Cores": $cores_config,
    "Nodes": [$formatted_nodes_config]
}
EOF
    chmod 600 "$staged_conf" || { rm -f "$staged_conf"; return 1; }
    # Never replace the live config with malformed JSON.
    if ! command -v python3 >/dev/null 2>&1 || ! python3 -m json.tool "$staged_conf" >/dev/null 2>&1; then
        rm -f "$staged_conf"
        echo -e "${red}配置 JSON 校验失败：已删除临时文件，原 config.json 未修改。${plain}"
        return 1
    fi
    mv -f "$staged_conf" /etc/V2bX/config.json || { rm -f "$staged_conf"; return 1; }
    
    # 创建 custom_outbound.json 文件
    cat <<EOF > /etc/V2bX/custom_outbound.json
    [
        {
            "tag": "IPv4_out",
            "protocol": "freedom",
            "settings": {
                "domainStrategy": "UseIPv4v6"
            }
        },
        {
            "tag": "IPv6_out",
            "protocol": "freedom",
            "settings": {
                "domainStrategy": "UseIPv6"
            }
        },
        {
            "protocol": "blackhole",
            "tag": "block"
        }
    ]
EOF
    
    # 创建 route.json 文件
    cat <<EOF > /etc/V2bX/route.json
    {
        "domainStrategy": "AsIs",
        "rules": [
            {
                "type": "field",
                "outboundTag": "block",
                "ip": [
                    "geoip:private"
                ]
            },
            {
                "type": "field",
                "outboundTag": "block",
                "domain": [
                    "regexp:(api|ps|sv|offnavi|newvector|ulog.imap|newloc)(.map|).(baidu|n.shifen).com",
                    "regexp:(.+.|^)(360|so).(cn|com)",
                    "regexp:(Subject|HELO|SMTP)",
                    "regexp:(torrent|.torrent|peer_id=|info_hash|get_peers|find_node|BitTorrent|announce_peer|announce.php?passkey=)",
                    "regexp:(^.@)(guerrillamail|guerrillamailblock|sharklasers|grr|pokemail|spam4|bccto|chacuo|027168).(info|biz|com|de|net|org|me|la)",
                    "regexp:(.?)(xunlei|sandai|Thunder|XLLiveUD)(.)",
                    "regexp:(..||)(dafahao|mingjinglive|botanwang|minghui|dongtaiwang|falunaz|epochtimes|ntdtv|falundafa|falungong|wujieliulan|zhengjian).(org|com|net)",
                    "regexp:(ed2k|.torrent|peer_id=|announce|info_hash|get_peers|find_node|BitTorrent|announce_peer|announce.php?passkey=|magnet:|xunlei|sandai|Thunder|XLLiveUD|bt_key)",
                    "regexp:(.+.|^)(360).(cn|com|net)",
                    "regexp:(.*.||)(guanjia.qq.com|qqpcmgr|QQPCMGR)",
                    "regexp:(.*.||)(rising|kingsoft|duba|xindubawukong|jinshanduba).(com|net|org)",
                    "regexp:(.*.||)(netvigator|torproject).(com|cn|net|org)",
                    "regexp:(..||)(visa|mycard|gash|beanfun|bank).",
                    "regexp:(.*.||)(gov|12377|12315|talk.news.pts.org|creaders|zhuichaguoji|efcc.org|cyberpolice|aboluowang|tuidang|epochtimes|zhengjian|110.qq|mingjingnews|inmediahk|xinsheng|breakgfw|chengmingmag|jinpianwang|qi-gong|mhradio|edoors|renminbao|soundofhope|xizang-zhiye|bannedbook|ntdtv|12321|secretchina|dajiyuan|boxun|chinadigitaltimes|dwnews|huaglad|oneplusnews|epochweekly|cn.rfi).(cn|com|org|net|club|net|fr|tw|hk|eu|info|me)",
                    "regexp:(.*.||)(miaozhen|cnzz|talkingdata|umeng).(cn|com)",
                    "regexp:(.*.||)(mycard).(com|tw)",
                    "regexp:(.*.||)(gash).(com|tw)",
                    "regexp:(.bank.)",
                    "regexp:(.*.||)(pincong).(rocks)",
                    "regexp:(.*.||)(taobao).(com)",
                    "regexp:(.*.||)(laomoe|jiyou|ssss|lolicp|vv1234|0z|4321q|868123|ksweb|mm126).(com|cloud|fun|cn|gs|xyz|cc)",
                    "regexp:(flows|miaoko).(pages).(dev)"
                ]
            },
            {
                "type": "field",
                "outboundTag": "block",
                "ip": [
                    "127.0.0.1/32",
                    "10.0.0.0/8",
                    "fc00::/7",
                    "fe80::/10",
                    "172.16.0.0/12"
                ]
            },
            {
                "type": "field",
                "outboundTag": "block",
                "protocol": [
                    "bittorrent"
                ]
            }
        ]
    }
EOF

    ipv6_support=$(check_ipv6_support)
    dnsstrategy="ipv4_only"
    if [ "$ipv6_support" -eq 1 ]; then
        dnsstrategy="prefer_ipv4"
    fi
    # 创建 sing_origin.json 文件
    cat <<EOF > /etc/V2bX/sing_origin.json
{
  "dns": {
    "servers": [
      {
        "tag": "cf",
        "address": "1.1.1.1"
      }
    ],
    "strategy": "$dnsstrategy"
  },
  "outbounds": [
    {
      "tag": "direct",
      "type": "direct",
      "domain_resolver": {
        "server": "cf",
        "strategy": "$dnsstrategy"
      }
    },
    {
      "type": "block",
      "tag": "block"
    }
  ],
  "route": {
    "rules": [
      {
        "ip_is_private": true,
        "outbound": "block"
      },
      {
        "domain_regex": [
            "(api|ps|sv|offnavi|newvector|ulog.imap|newloc)(.map|).(baidu|n.shifen).com",
            "(.+.|^)(360|so).(cn|com)",
            "(Subject|HELO|SMTP)",
            "(torrent|.torrent|peer_id=|info_hash|get_peers|find_node|BitTorrent|announce_peer|announce.php?passkey=)",
            "(^.@)(guerrillamail|guerrillamailblock|sharklasers|grr|pokemail|spam4|bccto|chacuo|027168).(info|biz|com|de|net|org|me|la)",
            "(.?)(xunlei|sandai|Thunder|XLLiveUD)(.)",
            "(..||)(dafahao|mingjinglive|botanwang|minghui|dongtaiwang|falunaz|epochtimes|ntdtv|falundafa|falungong|wujieliulan|zhengjian).(org|com|net)",
            "(ed2k|.torrent|peer_id=|announce|info_hash|get_peers|find_node|BitTorrent|announce_peer|announce.php?passkey=|magnet:|xunlei|sandai|Thunder|XLLiveUD|bt_key)",
            "(.+.|^)(360).(cn|com|net)",
            "(.*.||)(guanjia.qq.com|qqpcmgr|QQPCMGR)",
            "(.*.||)(rising|kingsoft|duba|xindubawukong|jinshanduba).(com|net|org)",
            "(.*.||)(netvigator|torproject).(com|cn|net|org)",
            "(..||)(visa|mycard|gash|beanfun|bank).",
            "(.*.||)(gov|12377|12315|talk.news.pts.org|creaders|zhuichaguoji|efcc.org|cyberpolice|aboluowang|tuidang|epochtimes|zhengjian|110.qq|mingjingnews|inmediahk|xinsheng|breakgfw|chengmingmag|jinpianwang|qi-gong|mhradio|edoors|renminbao|soundofhope|xizang-zhiye|bannedbook|ntdtv|12321|secretchina|dajiyuan|boxun|chinadigitaltimes|dwnews|huaglad|oneplusnews|epochweekly|cn.rfi).(cn|com|org|net|club|net|fr|tw|hk|eu|info|me)",
            "(.*.||)(miaozhen|cnzz|talkingdata|umeng).(cn|com)",
            "(.*.||)(mycard).(com|tw)",
            "(.*.||)(gash).(com|tw)",
            "(.bank.)",
            "(.*.||)(pincong).(rocks)",
            "(.*.||)(taobao).(com)",
            "(.*.||)(laomoe|jiyou|ssss|lolicp|vv1234|0z|4321q|868123|ksweb|mm126).(com|cloud|fun|cn|gs|xyz|cc)",
            "(flows|miaoko).(pages).(dev)"
        ],
        "outbound": "block"
      },
      {
        "outbound": "direct",
        "network": [
          "udp","tcp"
        ]
      }
    ]
  },
  "experimental": {
    "cache_file": {
      "enabled": true
    }
  }
}
EOF

    # 创建 hy2config.yaml 文件           
    cat <<EOF > /etc/V2bX/hy2config.yaml
quic:
  initStreamReceiveWindow: 8388608
  maxStreamReceiveWindow: 8388608
  initConnReceiveWindow: 20971520
  maxConnReceiveWindow: 20971520
  maxIdleTimeout: 30s
  maxIncomingStreams: 1024
  disablePathMTUDiscovery: false
ignoreClientBandwidth: false
disableUDP: false
udpIdleTimeout: 60s
resolver:
  type: system
acl:
  inline:
    - direct(geosite:google)
    - reject(geosite:cn)
    - reject(geoip:cn)
masquerade:
  type: 404
EOF
    echo -e "${green}V2bX 配置文件生成完成，正在重新启动 V2bX 服务${plain}"
    restart 0
    before_show_menu
}

# 放开防火墙端口
open_ports() {
    systemctl stop firewalld.service 2>/dev/null
    systemctl disable firewalld.service 2>/dev/null
    setenforce 0 2>/dev/null
    ufw disable 2>/dev/null
    iptables -P INPUT ACCEPT 2>/dev/null
    iptables -P FORWARD ACCEPT 2>/dev/null
    iptables -P OUTPUT ACCEPT 2>/dev/null
    iptables -t nat -F 2>/dev/null
    iptables -t mangle -F 2>/dev/null
    iptables -F 2>/dev/null
    iptables -X 2>/dev/null
    netfilter-persistent save 2>/dev/null
    echo -e "${green}放开防火墙端口成功！${plain}"
}


# AnyTLS uses the existing V2bX sing-box core, not a separate anytls-go service.
anytls_manage() {
    local action="$1"
    local interactive="${2:-false}"
    local helper="/usr/local/V2bX/anytls_manager.py"
    if [[ ! -f "$helper" ]]; then
        helper="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/tools/anytls_manager.py"
    fi
    local result=0
    if ! command -v python3 >/dev/null 2>&1; then
        echo -e "${red}AnyTLS 管理需要 python3，请先安装 python3${plain}"
        result=1
    elif [[ ! -f "$helper" ]]; then
        echo -e "${red}未找到 AnyTLS 管理工具。请更新安装脚本后重试。${plain}"
        result=1
    else
        python3 "$helper" "$action" || result=$?
    fi
    if [[ "$interactive" == "true" ]]; then
        before_show_menu
    fi
    return "$result"
}

hy2_pin_show() {
    local helper="/usr/local/V2bX/hy2_pin_manager.py"
    if [[ ! -f "$helper" ]]; then helper="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/tools/hy2_pin_manager.py"; fi
    if [[ ! -f "$helper" ]]; then echo -e "${red}请先更新 HY2 证书管理器。${plain}"; return 1; fi
    python3 "$helper" show
    local result=$?
    if [[ "${1:-}" != "noninteractive" ]]; then before_show_menu; fi
    return "$result"
}
show_usage() {
    echo "V2bX 管理脚本使用方法: "
    echo "------------------------------------------"
    echo "V2bX              - 显示管理菜单 (功能更多)"
    echo "V2bX start        - 启动 V2bX"
    echo "V2bX stop         - 停止 V2bX"
    echo "V2bX restart      - 重启 V2bX"
    echo "V2bX status       - 查看 V2bX 状态"
    echo "V2bX enable       - 设置 V2bX 开机自启"
    echo "V2bX disable      - 取消 V2bX 开机自启"
    echo "V2bX log          - 查看 V2bX 日志"
    echo "V2bX x25519       - 生成 x25519 密钥"
    echo "V2bX generate     - 生成 V2bX 配置文件"
    echo "V2bX update <tag> [--sha256 <digest>] [--start] - 校验并更新指定核心版本"
    echo "V2bX update_shell <本地Git目录> <commit> - 从固定提交更新维护脚本，不重启"
    echo "V2bX install      - 显示从可信 Git 工作副本安装的方法"
    echo "V2bX uninstall    - 卸载 V2bX"
    echo "V2bX version      - 查看 V2bX 版本"
    echo "V2bX anytls-add   - 添加 Xboard AnyTLS 节点（不自动重启）"
    echo "V2bX anytls-check - 检查 AnyTLS 本地配置"
    echo "V2bX hy2-pin-show - 查看 Hysteria2 证书 SHA256 指纹"
    echo "------------------------------------------"
}

show_menu() {
    echo -e "
  ${green}V2bX 后端管理脚本，${plain}${red}不适用于docker${plain}
--- https://github.com/wyx2685/V2bX ---
  ${green}0.${plain} 修改配置
————————————————
  ${green}1.${plain} 查看安全安装方法
  ${green}2.${plain} 校验并更新指定版本
  ${green}3.${plain} 卸载 V2bX
————————————————
  ${green}4.${plain} 启动 V2bX
  ${green}5.${plain} 停止 V2bX
  ${green}6.${plain} 重启 V2bX
  ${green}7.${plain} 查看 V2bX 状态
  ${green}8.${plain} 查看 V2bX 日志
————————————————
  ${green}9.${plain} 设置 V2bX 开机自启
  ${green}10.${plain} 取消 V2bX 开机自启
————————————————
  ${green}11.${plain} 一键安装 bbr (最新内核)
  ${green}12.${plain} 查看 V2bX 版本
  ${green}13.${plain} 生成 X25519 密钥
  ${green}14.${plain} 从固定 Git 提交更新维护脚本（不重启）
  ${green}15.${plain} 生成 V2bX 配置文件
  ${green}16.${plain} 放行 VPS 的所有网络端口
  ${green}17.${plain} 退出脚本
  ${green}18.${plain} 添加 Xboard AnyTLS 节点
  ${green}19.${plain} 检查 AnyTLS 本地配置
  ${green}20.${plain} 查看 Hysteria2 固定证书指纹
 "
 #后续更新可加入上方字符串中
    show_status
    echo && read -rp "请输入选择 [0-20]: " num

    case "${num}" in
        0) config ;;
        1) check_uninstall && install ;;
        2) check_install && update ;;
        3) check_install && uninstall ;;
        4) check_install && start ;;
        5) check_install && stop ;;
        6) check_install && restart ;;
        7) check_install && status ;;
        8) check_install && show_log ;;
        9) check_install && enable ;;
        10) check_install && disable ;;
        11) install_bbr ;;
        12) check_install && show_V2bX_version ;;
        13) check_install && generate_x25519_key ;;
        14) update_shell ;;
        15) generate_config_file ;;
        16) open_ports ;;
        17) exit ;;
        18) anytls_manage add true ;;
        19) anytls_manage check true ;;
        20) hy2_pin_show ;;
        *) echo -e "${red}请输入正确的数字 [0-20]${plain}" ;;
    esac
}


if [[ $# > 0 ]]; then
    case $1 in
        "start") check_install 0 && start 0 ;;
        "stop") check_install 0 && stop 0 ;;
        "restart") check_install 0 && restart 0 ;;
        "status") check_install 0 && status 0 ;;
        "enable") check_install 0 && enable 0 ;;
        "disable") check_install 0 && disable 0 ;;
        "log") check_install 0 && show_log 0 ;;
        "update") shift; check_install 0 && { if [[ $# == 0 ]]; then echo "请指定核心版本，例如 V2bX update v0.4.0" >&2; exit 1; fi; update "$@"; } ;;
        "config") config $* ;;
        "generate") generate_config_file ;;
        "install") check_uninstall 0 && install 0 ;;
        "uninstall") check_install 0 && uninstall 0 ;;
        "x25519") check_install 0 && generate_x25519_key 0 ;;
        "version") check_install 0 && show_V2bX_version 0 ;;
        "update_shell") shift; if [[ $# != 2 ]]; then echo "用法：V2bX update_shell <可信本地 Git 工作副本路径> <40位提交SHA>" >&2; exit 1; fi; update_shell "$@" ;;
        "anytls-add") anytls_manage add ;;
        "anytls-check") anytls_manage check ;;
        "hy2-pin-show") hy2_pin_show noninteractive ;;
        *) show_usage
    esac
else
    show_menu
fi
