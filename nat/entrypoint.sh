#!/bin/sh
set -e

MGT_IF="${MGT_IF:-ens3}"
NAT_ENABLED="${NAT_ENABLED:-0}"

echo "=== Starting Central NAT Container ==="
echo "Management Interface: $MGT_IF"
echo "NAT Enabled: $NAT_ENABLED"

cleanup() {
    echo "Cleaning up outbound NAT rules on $MGT_IF..."
    iptables -t nat -D POSTROUTING -o "$MGT_IF" -m comment --comment "CENTRAL_NAT" -j MASQUERADE 2>/dev/null || true
    iptables -D FORWARD -o "$MGT_IF" -m comment --comment "CENTRAL_NAT" -j ACCEPT 2>/dev/null || true
    iptables -D FORWARD -i "$MGT_IF" -m conntrack --ctstate RELATED,ESTABLISHED -m comment --comment "CENTRAL_NAT" -j ACCEPT 2>/dev/null || true
    iptables -t mangle -D FORWARD -p tcp --tcp-flags SYN,RST SYN -o "$MGT_IF" -m comment --comment "CENTRAL_NAT" -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || true

    # Clean legacy comment if present
    iptables -t nat -D POSTROUTING -o "$MGT_IF" -m comment --comment "MAP-E_PPPOE_NAT" -j MASQUERADE 2>/dev/null || true
    iptables -D FORWARD -o "$MGT_IF" -m comment --comment "MAP-E_PPPOE_NAT" -j ACCEPT 2>/dev/null || true
    iptables -D FORWARD -i "$MGT_IF" -m conntrack --ctstate RELATED,ESTABLISHED -m comment --comment "MAP-E_PPPOE_NAT" -j ACCEPT 2>/dev/null || true
    iptables -t mangle -D FORWARD -p tcp --tcp-flags SYN,RST SYN -o "$MGT_IF" -m comment --comment "MAP-E_PPPOE_NAT" -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || true
}

trap 'cleanup; exit 0' TERM INT

# 初期化時に既存の残余ルールをクリーンアップ
cleanup

if [ "$NAT_ENABLED" = "1" ]; then
    echo "Enabling IPv4 Outbound Central NAT (MASQUERADE) on $MGT_IF..."
    echo 1 > /proc/sys/net/ipv4/ip_forward 2>/dev/null || sysctl -w net.ipv4.ip_forward=1 2>/dev/null || true

    iptables -t nat -A POSTROUTING -o "$MGT_IF" -m comment --comment "CENTRAL_NAT" -j MASQUERADE
    iptables -I FORWARD 1 -o "$MGT_IF" -m comment --comment "CENTRAL_NAT" -j ACCEPT
    iptables -I FORWARD 2 -i "$MGT_IF" -m conntrack --ctstate RELATED,ESTABLISHED -m comment --comment "CENTRAL_NAT" -j ACCEPT
    iptables -t mangle -I FORWARD 1 -p tcp --tcp-flags SYN,RST SYN -o "$MGT_IF" -m comment --comment "CENTRAL_NAT" -j TCPMSS --clamp-mss-to-pmtu
    echo "Central NAT rules applied successfully."
else
    echo "Central NAT is disabled."
fi

# シグナルを捕捉できるよう待機ループ
while true; do
    sleep 86400 &
    wait $!
done
