#!/usr/bin/env python3
"""
Web Dashboard for MAP-E & PPPoE ISP Emulation Environment

Entry point - imports modules and starts the HTTP server.

Modules:
  constants.py   : グローバル定数・環境変数
  config.py      : AppConfig / ConfigManager (Section 1)
  utils.py       : ユーティリティ / ネットワーク (Section 2)
  collectors.py  : PPPoECollector / MAPECollector (Section 3)
  handlers.py    : DashboardHandler (Section 4)
"""

from http.server import ThreadingHTTPServer

from constants import PORT
from handlers import DashboardHandler
from collectors import start_background_ping_worker


def main():
    print(f"=== Starting Web Dashboard on port {PORT} ===")
    start_background_ping_worker()
    server_address = ("0.0.0.0", PORT)
    httpd = ThreadingHTTPServer(server_address, DashboardHandler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n=== Shutting down Web Dashboard ===")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
