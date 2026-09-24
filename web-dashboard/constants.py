# web-dashboard/constants.py
"""
グローバル定数・環境変数の解決。
他のモジュールはここから定数を import する。
"""

import os

PORT = int(os.environ.get("PORT", 1600))
LOG_DIR = os.environ.get("LOG_DIR", "/var/log")
WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR")
if not WORKSPACE_DIR:
    if os.path.exists("/.dockerenv") and os.path.exists("/app/workspace"):
        WORKSPACE_DIR = "/app/workspace"
    else:
        WORKSPACE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def get_host_workspace_dir() -> str:
    """
    ホスト上のプロジェクトディレクトリを動的に解決する。
    コンテナ内からホストの Docker daemon を操作する際（DooD）、
    docker inspect から自身の /app/workspace のマウント元（ホスト側パス）を動的に取得する。
    ホスト側パスが取得できない場合は WORKSPACE_DIR を返す。
    """
    host_dir = os.environ.get("HOST_WORKSPACE")
    if host_dir:
        return host_dir
    try:
        import subprocess
        res = subprocess.run(
            ["docker", "inspect", "web-dashboard", "--format",
             '{{ range .Mounts }}{{ if eq .Destination "/app/workspace" }}{{ .Source }}{{ end }}{{ end }}'],
            capture_output=True, text=True, timeout=5
        )
        val = res.stdout.strip()
        if val:
            return val
    except Exception:
        pass
    return WORKSPACE_DIR

KEA_LEASE_CSV = os.environ.get("KEA_LEASE_CSV", "/var/lib/kea/kea-leases6.csv")
KEA_CTRL_SOCK = os.environ.get("KEA_CTRL_SOCK", "/run/kea/kea-dhcp6-ctrl.sock")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
TUNNEL_EVENT_LOG = os.path.join(LOG_DIR, "mape-tunnel-events.log")
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
PPPOE_SESSION_CACHE = os.path.join(DATA_DIR, "pppoe_sessions.json")
