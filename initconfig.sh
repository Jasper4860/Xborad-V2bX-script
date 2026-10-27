#!/bin/bash
# 一键配置

# 检查系统是否有 IPv6 地址
check_ipv6_support() {
    if ip -6 addr | grep -q "inet6"; then
        echo "1"  # 支持 IPv6
    else
        echo "0"  # 不支持 IPv6
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
    while true; do
        read -r -p "请选择 [1-4]（默认 1）：" selection || return 1
        selection="${selection:-1}"
        case "$selection" in
            1) certmode="http"; break ;;
            2) certmode="dns"; break ;;
            3) certmode="self"; break ;;
            4) certmode="file"; break ;;
            *) echo "选择无效，请输入 1-4。" ;;
        esac
    done

    while true; do
        read -r -p "请输入 TLS 证书域名（例如 node.example.com）：" certdomain || return 1
        certdomain="${certdomain,,}"
        if valid_tls_domain "$certdomain"; then break; fi
        echo -e "${red}域名无效：请填写有 DNS 解析的完整域名，不含 https:// 和端口。${plain}"
    done

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
                read -rp "请输入机场网址(https://example.com)：" ApiHost
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
    
    # 备份旧的配置文件
    mv config.json config.json.bak
    nodes_config_str="${nodes_config[*]}"
    formatted_nodes_config="${nodes_config_str%,}"

    # 创建 config.json 文件
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
            "outboundTag": "block",
            "ip": [
                "geoip:private"
            ]
        },
        {
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
            "outboundTag": "block",
            "protocol": [
                "bittorrent"
            ]
        },
        {
            "outboundTag": "IPv4_out",
            "network": "udp,tcp"
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
    echo -e "${green}V2bX 配置文件生成完成,正在重新启动服务${plain}"
    v2bx restart
}
