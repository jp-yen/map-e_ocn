#!/bin/bash
set -e

# /dev/log が無い場合のみ syslog ソケットへリンク (chronyd は /dev/log 経由で直接 syslog 送信)
if [ -e /run/syslog/log ] && [ ! -e /dev/log ]; then
    ln -sf /run/syslog/log /dev/log
fi

exec /usr/sbin/chronyd -n -u root -f /etc/chrony/chrony.conf
