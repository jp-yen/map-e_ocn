#!/bin/bash
set -e

# Required environment variables:
# VLAN_IFS: Interface names to listen on (e.g. "ens8.801 ens8.802", space-separated)
#   (or legacy VLAN_IF)
# SERVER_IP: Server local IP (e.g. 100.111.111.0)
# SERVICE_NAME: (Optional) Service Name

IFS_TO_USE="${VLAN_IFS:-$VLAN_IF}"
if [ -z "$IFS_TO_USE" ]; then
    echo "Error: VLAN_IFS (or VLAN_IF) environment variable is required (e.g. 'ens8.801 ens8.802')" >&2
    exit 1
fi

# SERVER_IP は統合型 (SERVICE_NAMES 指定時) では省略可能
if [ -z "$SERVER_IP" ] && [ -z "$SERVICE_NAMES" ]; then
    echo "Error: SERVER_IP environment variable is required (e.g. 100.111.111.0)" >&2
    exit 1
fi

echo "=== Starting PPPoE Server Container ==="
echo "Interfaces: $IFS_TO_USE"
echo "Server IP: ${SERVER_IP:-${PPPOE_SERVER_BASE_IP:-100.126.241.1}} (increment: ${INCR_LOCAL_IP:-1})"
echo "IP Pool (PPPOE_IP_POOL): ${PPPOE_IP_POOL:-(not set, fixed IP only)}"
[ -n "$SERVICE_NAMES" ] && echo "Service Names: $SERVICE_NAMES"

# Ensure /dev/ppp exists
if [ ! -c /dev/ppp ]; then
    mknod /dev/ppp c 108 0 || true
    chmod 600 /dev/ppp || true
fi

# Ensure /dev/log points to dedicated syslog socket if available, otherwise fallback to shared socket
SOCKET_PATH="/run/syslog/log"
# 統合型: 固定ソケット名 pppoe-unified を使用
TARGET_SOCK="/run/syslog/pppoe-unified.sock"
for i in $(seq 1 25); do
    if [ -S "$TARGET_SOCK" ] || [ -e "$TARGET_SOCK" ]; then
        SOCKET_PATH="$TARGET_SOCK"
        break
    fi
    sleep 0.2
done
if [ -e "$SOCKET_PATH" ]; then
    ln -sf "$SOCKET_PATH" /dev/log
    echo "Syslog socket connected to: $SOCKET_PATH"
else
    echo "Warning: No syslog socket found at $SOCKET_PATH"
fi

# Neutralize Debian default /etc/ppp/options which has 'auth', 'login', 'lock'
# which conflict with per-session PPPoE options and non-system users
cat << 'EOF' > /etc/ppp/options
# Global options disabled for pppoe-server
EOF

# Ensure pppd plugin symlink exists
mkdir -p /etc/ppp/plugins
if [ ! -e /etc/ppp/plugins/rp-pppoe.so ]; then
    PLUGIN_PATH=$(ls /usr/lib/pppd/*/rp-pppoe.so 2>/dev/null | head -n 1)
    if [ -n "$PLUGIN_PATH" ]; then
        ln -sf "$PLUGIN_PATH" /etc/ppp/plugins/rp-pppoe.so
    fi
fi

# Ensure options file exists
OPTIONS_FILE="/etc/ppp/pppoe-server-options"
if [ ! -f "$OPTIONS_FILE" ]; then
    cat << 'EOF' > "$OPTIONS_FILE"
# Default PPPoE server pppd options
require-chap
require-pap
lcp-echo-interval 10
lcp-echo-failure 3
mtu 1452
mru 1452
nodefaultroute
proxyarp
debug
EOF
fi

# Build pppoe-server arguments
# -F : foreground (do not fork, required for container)
# -k : use kernel-mode PPPoE
# -I : interface
# -L : local IP (server IP) - 統合型では省略可能(各サービスで異なるため)
# -R : remote starting IP (IP pool start address for dynamic allocation)
# -N : max sessions
# -O : options file
# -S : Service-Name (複数指定可 / 空文字 = 全 Service-Name を受け付け)
ARGS=("-F" "-k" "-N" "128" "-O" "$OPTIONS_FILE")

# IP プール設定 (-R <start_ip>): PPPOE_IP_POOL が設定されている場合のみ追加
# 固定IPユーザーは chap-secrets で個別指定済み、プールは chap-secrets が * のユーザーに使用される
if [ -n "$PPPOE_IP_POOL" ]; then
    # CIDR から最初のホスト IP を計算 (例: 10.9.0.0/24 -> 10.9.0.1)
    POOL_NET="${PPPOE_IP_POOL%/*}"
    POOL_PREFIX="${PPPOE_IP_POOL#*/}"
    IFS='.' read -r o1 o2 o3 o4 <<< "$POOL_NET"
    # ネットワークアドレス -> 最初のホスト IP (+1)
    o4_host=$(( o4 + 1 ))
    if [ "$o4_host" -gt 254 ]; then
        o3=$(( o3 + 1 ))
        o4_host=1
    fi
    POOL_START_IP="${o1}.${o2}.${o3}.${o4_host}"
    # プールサイズ (ホスト数) を計算: 2^(32-prefix) - 2
    POOL_HOST_COUNT=$(( (1 << (32 - POOL_PREFIX)) - 2 ))
    if [ "$POOL_HOST_COUNT" -lt 1 ]; then POOL_HOST_COUNT=1; fi
    if [ "$POOL_HOST_COUNT" -gt 128 ]; then POOL_HOST_COUNT=128; fi
    ARGS+=("-R" "$POOL_START_IP" "-N" "$POOL_HOST_COUNT")
    echo "IP Pool: $PPPOE_IP_POOL -> start=$POOL_START_IP count=$POOL_HOST_COUNT"
else
    # プールなし: 固定IPのみ (chap-secrets に * がないことを想定)
    ARGS+=("-R" "0.0.0.0")
fi

# サーバーローカルIP設定とセッションごとのインクリメント (-l)
# デフォルトでベースIPを設定し、-l でセッションごとに異なるIPを払い出す
TARGET_SERVER_IP="${SERVER_IP:-${PPPOE_SERVER_BASE_IP:-100.126.241.1}}"
if [ -n "$TARGET_SERVER_IP" ]; then
    ARGS+=("-L" "$TARGET_SERVER_IP")
fi

if [ "${INCR_LOCAL_IP:-1}" = "1" ]; then
    ARGS+=("-l")
    echo "Local IP increment: enabled (-l from $TARGET_SERVER_IP)"
fi

for iface in $IFS_TO_USE; do
    ARGS+=("-I" "$iface")
done

# 複数サービス名 (SERVICE_NAMES) を各 -S オプションとして展開
# 空文字列を含めることで「Service-Name 未指定クライアント」も受け付ける
if [ -n "$SERVICE_NAMES" ]; then
    for svc in $SERVICE_NAMES; do
        ARGS+=("-S" "$svc")
    done
    ARGS+=("-S" "")   # Service-Name 無指定クライアントも受け付ける
fi

echo "Executing: pppoe-server ${ARGS[*]}"
exec pppoe-server "${ARGS[@]}"
