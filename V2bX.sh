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
    bash <(curl -Ls https://raw.githubusercontent.com/Jasper4860/Xborad-V2bX-script/feature/native-anytls-management/install.sh)
    if [[ $? == 0 ]]; then
        if [[ $# == 0 ]]; then
            start
        else
            start 0
        fi
    fi
}

update() {
    if [[ $# == 0 ]]; then
        echo && echo -n -e "输入指定版本(默认最新版): " && read version
    else
        version=$2
    fi
    bash <(curl -Ls https://raw.githubusercontent.com/Jasper4860/Xborad-V2bX-script/feature/native-anytls-management/install.sh) $version
    if [[ $? == 0 ]]; then
        echo -e "${green}更新完成，已自动重启 V2bX，请使用 V2bX log 查看运行日志${plain}"
        exit
    fi

    if [[ $# == 0 ]]; then
        before_show_menu
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
    wget -O /usr/bin/V2bX -N --no-check-certificate https://raw.githubusercontent.com/Jasper4860/Xborad-V2bX-script/feature/native-anytls-management/V2bX.sh
    if [[ $? != 0 ]]; then
        echo ""
        echo -e "${red}下载脚本失败，请检查本机能否连接 Github${plain}"
        before_show_menu
    else
        chmod +x /usr/bin/V2bX
        if [[ -d /usr/local/V2bX ]]; then
            curl -fLsS --retry 3 -o /usr/local/V2bX/anytls_manager.py https://raw.githubusercontent.com/Jasper4860/Xborad-V2bX-script/feature/native-anytls-management/tools/anytls_manager.py || echo -e "${yellow}AnyTLS 管理工具下载失败，请稍后重试${plain}"
        fi
        echo -e "${green}升级脚本成功，请重新运行脚本${plain}" && exit 0
    fi
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
configure_node_tls() {
    local selection cf_token
    echo -e "${yellow}请选择 TLS 证书方式：${plain}"
    echo -e "${green}1. Let's Encrypt HTTP-01（默认，启动后自动申请和续期）${plain}"
    echo -e "${green}2. Let's Encrypt DNS-01（Cloudflare API Token，支持 NAT）${plain}"
    echo -e "${green}3. self 自签名证书（仅测试，客户端需单独信任）${plain}"
    echo -e "${green}4. 使用已有证书文件（不自动续期）${plain}"
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
            *) echo "选择无效，请输入 1-4。" ;;
        esac
    done

    while true; do
        read -r -p "请输入 TLS 证书域名（例如 node.example.com）：" certdomain || return 1
        certdomain="${certdomain,,}"
        if valid_tls_domain "$certdomain"; then break; fi
        echo -e "${red}域名无效：请填写有 DNS 解析的完整域名，不含 https:// 和端口。${plain}"
    done

    if [[ "${hy2_pin_enabled:-false}" == "true" ]]; then
        local helper="/usr/local/V2bX/hy2_pin_manager.py"
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
    if [[ "$certmode" == "file" ]]; then
        while true; do
            read -r -p "请输入现有 fullchain.pem/cert 文件绝对路径：" certfile || return 1
            read -r -p "请输入现有私钥文件绝对路径：" keyfile || return 1
            if [[ "$certfile" =~ ^/[A-Za-z0-9._/-]+$ && "$keyfile" =~ ^/[A-Za-z0-9._/-]+$ && -r "$certfile" && -r "$keyfile" ]]; then
                break
            fi
            echo -e "${red}证书路径必须是可读取的绝对路径，且不含空格或特殊字符。${plain}"
        done
        echo "将使用已有证书，不会调用 Let's Encrypt 申请或续期。"
        return 0
    fi

    certfile="/etc/V2bX/certs/$certdomain/fullchain.cer"
    keyfile="/etc/V2bX/certs/$certdomain/privkey.pem"
    # lego upstream saves the ACME account and certificate key with permissive
    # file modes: protect their parent directory before the service starts.
    if [[ "$certmode" == "http" || "$certmode" == "dns" ]]; then
        while true; do
            read -r -p "请输入 Let's Encrypt 联系邮箱：" certemail || return 1
            if valid_tls_email "$certemail"; then break; fi
            echo -e "${red}邮箱格式无效，请重新输入。${plain}"
        done
    fi

    if [[ "$certmode" == "http" ]]; then
        echo -e "${yellow}注意：HTTP-01 要求域名指向本节点，公网 TCP/80 可直接访问本机 80 端口。${plain}"
        echo -e "${yellow}NAT/DDNS 仅映射高位端口时不能使用 HTTP-01，请改选 DNS-01。${plain}"
        if command -v ss >/dev/null 2>&1 && [[ -n "$(ss -H -ltn 'sport = :80' 2>/dev/null)" ]]; then
            echo -e "${red}警告：检测到本机 TCP/80 正在监听，V2bX 的 HTTP-01 申请可能因端口冲突失败！${plain}"
        fi
        if command -v getent >/dev/null 2>&1 && ! getent ahostsv4 "$certdomain" >/dev/null 2>&1; then
            echo -e "${yellow}提示：未检测到域名的 IPv4 解析。请确认 DNS 后再启动 V2bX。${plain}"
        fi
    elif [[ "$certmode" == "dns" ]]; then
        certprovider="cloudflare"
        echo -e "${yellow}需要 Cloudflare DNS 区域权限：Zone:Read、DNS:Edit。Token 将以明文写入仅 root 可读的 config.json。${plain}"
        while true; do
            read -r -s -p "请输入 CLOUDFLARE_DNS_API_TOKEN（隐藏输入）：" cf_token || return 1
            echo
            if [[ "$cf_token" =~ ^[A-Za-z0-9_-]+$ ]]; then break; fi
            echo -e "${red}Token 格式无效，不能留空或包含空格/引号。${plain}"
        done
        certdnsenv="{\"CLOUDFLARE_DNS_API_TOKEN\":\"$cf_token\"}"
        unset cf_token
    elif [[ "$certmode" == "self" ]]; then
        echo -e "${yellow}自签名证书仅用于测试环境，不属于 Let's Encrypt。${plain}"
    fi
    # Do not create the cert folder if user selected existing certificate.
    if ! mkdir -p "/etc/V2bX/certs/$certdomain"; then
        echo -e "${red}证书目录无法创建。${plain}"
        return 1
    fi
    chmod 0700 "/etc/V2bX/certs/$certdomain" || return 1
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
    if [[ "$certmode" == "http" || "$certmode" == "dns" ]]; then
        echo -e "${yellow}Let's Encrypt 将在 V2bX 启动时申请；成功后每日检查续期。${plain}"
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
            add_node_config
        else
            read -rp "是否继续添加节点配置？(回车继续，输入n或no退出)" continue_adding_node
            if [[ "$continue_adding_node" =~ ^[Nn][Oo]? ]]; then
                break
            elif [ "$fixed_api_info" = false ]; then
                read -rp "请输入机场网址：" ApiHost
                read -rp "请输入面板对接API Key：" ApiKey
            fi
            add_node_config
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
    cat <<EOF > /etc/V2bX/config.json
{
    "Log": {
        "Level": "error",
        "Output": ""
    },
    "Cores": $cores_config,
    "Nodes": [$formatted_nodes_config]
}
EOF
    chmod 600 /etc/V2bX/config.json || return 1
    # A malformed configuration must never be used to restart the live service.
    if command -v python3 >/dev/null 2>&1; then
        if ! python3 -m json.tool /etc/V2bX/config.json >/dev/null 2>&1; then
            echo -e "${red}生成的 JSON 不合法！当前 V2bX 不会重启，请检查配置并用备份恢复。${plain}"
            return 1
        fi
    fi
    
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
    echo "V2bX update       - 更新 V2bX"
    echo "V2bX update x.x.x - 安装 V2bX 指定版本"
    echo "V2bX install      - 安装 V2bX"
    echo "V2bX uninstall    - 卸载 V2bX"
    echo "V2bX version      - 查看 V2bX 版本"
    echo "V2bX anytls-add   - 添加 Xboard AnyTLS 节点（不自动重启）"
    echo "V2bX anytls-check - 检查 AnyTLS 本地配置"
    echo "------------------------------------------"
}

show_menu() {
    echo -e "
  ${green}V2bX 后端管理脚本，${plain}${red}不适用于docker${plain}
--- https://github.com/wyx2685/V2bX ---
  ${green}0.${plain} 修改配置
————————————————
  ${green}1.${plain} 安装 V2bX
  ${green}2.${plain} 更新 V2bX
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
  ${green}14.${plain} 升级 V2bX 维护脚本
  ${green}15.${plain} 生成 V2bX 配置文件
  ${green}16.${plain} 放行 VPS 的所有网络端口
  ${green}17.${plain} 退出脚本
  ${green}18.${plain} 添加 Xboard AnyTLS 节点
  ${green}19.${plain} 检查 AnyTLS 本地配置
 "
 #后续更新可加入上方字符串中
    show_status
    echo && read -rp "请输入选择 [0-19]: " num

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
        *) echo -e "${red}请输入正确的数字 [0-19]${plain}" ;;
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
        "update") check_install 0 && update 0 $2 ;;
        "config") config $* ;;
        "generate") generate_config_file ;;
        "install") check_uninstall 0 && install 0 ;;
        "uninstall") check_install 0 && uninstall 0 ;;
        "x25519") check_install 0 && generate_x25519_key 0 ;;
        "version") check_install 0 && show_V2bX_version 0 ;;
        "update_shell") update_shell ;;
        "anytls-add") anytls_manage add ;;
        "anytls-check") anytls_manage check ;;
        *) show_usage
    esac
else
    show_menu
fi
