#!/bin/bash
# macOS 透明代理设置脚本

PROXY_PORT=8899
REDIRECT_PORT=8899

# 启动透明代理
start_transparent_proxy() {
    echo "正在配置透明代理..."

    # 1. 启用 IP 转发
    sudo sysctl -w net.inet.ip.forwarding=1

    # 2. 创建 pf 规则文件
    cat > /tmp/pf_mitmproxy.conf <<EOF
# 重定向美团相关域名的流量到 mitmproxy
rdr pass on lo0 inet proto tcp from any to any port 80 -> 127.0.0.1 port $REDIRECT_PORT
rdr pass on lo0 inet proto tcp from any to any port 443 -> 127.0.0.1 port $REDIRECT_PORT
EOF

    # 3. 加载 pf 规则
    sudo pfctl -f /tmp/pf_mitmproxy.conf
    sudo pfctl -e 2>/dev/null || true

    echo "透明代理已启动"
}

# 停止透明代理
stop_transparent_proxy() {
    echo "正在停止透明代理..."

    # 禁用 pf
    sudo pfctl -d 2>/dev/null || true

    # 恢复 IP 转发设置
    sudo sysctl -w net.inet.ip.forwarding=0

    # 清理临时文件
    rm -f /tmp/pf_mitmproxy.conf

    echo "透明代理已停止"
}

# 检查状态
check_status() {
    echo "=== pf 状态 ==="
    sudo pfctl -s info 2>/dev/null || echo "pf 未运行"
    echo ""
    echo "=== IP 转发状态 ==="
    sysctl net.inet.ip.forwarding
}

case "$1" in
    start)
        start_transparent_proxy
        ;;
    stop)
        stop_transparent_proxy
        ;;
    status)
        check_status
        ;;
    *)
        echo "用法: $0 {start|stop|status}"
        exit 1
        ;;
esac
