#!/bin/sh
set -e

# /run/radvd ディレクトリの作成 (pidfile 用)
mkdir -p /run/radvd

# /dev/log が無い場合のみ syslog ソケットへリンク (既存 /dev/log があれば優先)
if [ -e /run/syslog/log ] && [ ! -e /dev/log ]; then
    ln -sf /run/syslog/log /dev/log
fi

exec /usr/sbin/radvd -n -u root -m syslog -C /etc/radvd.conf

