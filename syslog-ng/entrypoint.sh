#!/bin/sh
set -e

# crontabs の設定 (毎日 0:00 JST に logrotate を実行)
mkdir -p /etc/crontabs /var/lib /var/log/kea
chmod 755 /var/log /var/log/kea || true
# logrotate は設定ファイルの所有者が root (uid 0) でないと実行を拒否するため、
# ホストからマウントされたファイルを root 所有のローカル設定として配置する
if [ -f /etc/logrotate.d/syslog-ng ]; then
    cp /etc/logrotate.d/syslog-ng /etc/logrotate.syslog-ng
    chown root:root /etc/logrotate.syslog-ng
    chmod 0644 /etc/logrotate.syslog-ng
fi

cat << 'EOF' > /etc/crontabs/root
0 0 * * * /usr/sbin/logrotate -s /var/lib/logrotate.status /etc/logrotate.syslog-ng
EOF

# バックグラウンドで crond (Alpine cron デーモン) を起動
echo "[entrypoint] Starting crond for daily log rotation (rotate 20)..."
crond -b -l 8

# フォアグラウンドで syslog-ng を起動
echo "[entrypoint] Starting syslog-ng..."
exec /usr/sbin/syslog-ng -F -f /etc/syslog-ng/syslog-ng.conf
