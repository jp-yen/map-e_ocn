# web-dashboard/handlers.py
"""
Section 4: HTTP Request Handling & Dispatch
  - DashboardHandler : 全 API エンドポイントのルーティングと処理
"""

import os
import re
import json
import time
import datetime
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from constants import WORKSPACE_DIR, STATIC_DIR
from config import ConfigManager
from utils import (
    run_cmd, KeaClient,
    parse_line_datetime, detect_log_level, fetch_logs_by_type,
    get_container_statuses,
)
from collectors import PPPoECollector, MAPECollector, sync_active_ping_targets


def _get_app_config():
    from config import APP_CONFIG  # noqa: PLC0415
    return APP_CONFIG


def get_system_vrfs():
    """ホスト上に存在する VRF デバイス名一覧を返す。"""
    ret, out, _ = run_cmd(["ip", "-o", "link", "show", "type", "vrf"])
    vrfs = []
    if ret == 0:
        for line in out.splitlines():
            parts = line.split(":")
            if len(parts) >= 2:
                dev = parts[1].strip()
                if "@" in dev:
                    dev = dev.split("@")[0]
                if dev:
                    vrfs.append(dev)
    try:
        app_cfg = _get_app_config()
        for srv in getattr(app_cfg, "pppoe_servers", []):
            if srv.get("vrf_enabled"):
                name = srv.get("service_name") or srv.get("ac_name")
                if name:
                    v_name = f"vrf-{name.replace(' ', '_')[:11]}"
                    if v_name not in vrfs:
                        vrfs.append(v_name)
    except Exception:
        pass
    return sorted(list(set(vrfs)))


# Section 4: HTTP Request Handling & Dispatch
# =============================================================================

class DashboardHandler(BaseHTTPRequestHandler):
    """Web ダッシュボードの HTTP リクエスト処理およびルーティングディスパッチャ"""

    def log_message(self, format, *args):
        # アクセスログは stderr（WARN/ERR扱い）ではなく stdout（INFO扱い）に出力
        import sys
        sys.stdout.write("%s - - [%s] %s\n" %
                         (self.address_string(),
                          self.log_date_time_string(),
                          format % args))
        sys.stdout.flush()

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, filepath, content_type="text/html; charset=utf-8"):
        if not os.path.exists(filepath):
            self.send_error(404, "File Not Found")
            return
        with open(filepath, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # --- GET Handlers ---

    def handle_get_clients(self, query):
        _get_app_config().reload()
        pppoe_list = PPPoECollector.collect()
        mape_list = MAPECollector.collect()
        sync_active_ping_targets(pppoe_list, mape_list)
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        self.send_json({
            "timestamp": now_str,
            "summary": {
                "pppoe_count": len(pppoe_list),
                "mape_count": len(mape_list),
                "total_count": len(pppoe_list) + len(mape_list)
            },
            "pppoe_servers": _get_app_config().pppoe_servers,
            "br_info": _get_app_config().br_info,
            "ipoe_vlans": _get_app_config().ipoe_vlans,
            "pppoe_clients": pppoe_list,
            "mape_clients": mape_list,
            "containers": get_container_statuses()
        })

    def handle_get_vrfs(self, query):
        self.send_json({"vrfs": get_system_vrfs()})

    def handle_get_ping(self, query):
        target = query.get("host", [""])[0].strip()
        count = int(query.get("count", ["4"])[0])
        count = min(max(count, 1), 10)
        vrf = query.get("vrf", [""])[0].strip()
        if vrf and not re.match(r"^[a-zA-Z0-9_\-]+$", vrf):
            vrf = ""

        if not target:
            self.send_json({"error": "Host parameter is required"}, 400)
            return

        is_v6 = ":" in target
        cmd = ["ping"]
        if vrf:
            cmd.extend(["-I", vrf])
        cmd.extend(["-6" if is_v6 else "-4", "-c", str(count), "-W", "1", target])
        ret, out, err = run_cmd(cmd, timeout=count * 2 + 3)
        self.send_json({
            "host": target,
            "vrf": vrf or None,
            "returncode": ret,
            "success": ret == 0,
            "output": out if ret == 0 else (out + "\n" + err)
        })

    def handle_get_ping_stream(self, query):
        """Ping の出力を Server-Sent Events (SSE) でリアルタイムに返す。

        ping コマンドを subprocess.Popen で起動し、1行出力されるごとに
        'data: <line>' として即時フラッシュ送信する。最後に returncode を
        'event: done' として送信してストリームを閉じる。
        """
        import subprocess as _sp

        target = query.get("host", [""])[0].strip()
        count = int(query.get("count", ["4"])[0])
        count = min(max(count, 1), 10)
        vrf = query.get("vrf", [""])[0].strip()
        if vrf and not re.match(r"^[a-zA-Z0-9_\-]+$", vrf):
            vrf = ""

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        if not target:
            self.wfile.write(f"event: error\ndata: Host parameter is required\n\n".encode("utf-8"))
            self.wfile.flush()
            return

        is_v6 = ":" in target
        cmd = ["ping"]
        if vrf:
            cmd.extend(["-I", vrf])
        cmd.extend(["-6" if is_v6 else "-4", "-c", str(count), "-W", "1", target])

        try:
            proc = _sp.Popen(
                cmd,
                stdout=_sp.PIPE,
                stderr=_sp.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as e:
            self.wfile.write(f"event: error\ndata: {str(e)}\n\n".encode("utf-8"))
            self.wfile.flush()
            return

        assert proc.stdout is not None
        try:
            for line in iter(proc.stdout.readline, ""):
                if not line:
                    break
                self.wfile.write(f"data: {line.rstrip()}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            # クライアント切断時は読み取りを止める
            proc.kill()
            return
        finally:
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()

        rc = proc.returncode
        self.wfile.write(f"event: done\ndata: {rc}\n\n".encode("utf-8"))
        self.wfile.flush()

    def handle_get_rustscan_stream(self, query):
        """RustScan による全ポートスキャン出力を Server-Sent Events (SSE) でリアルタイムにストリーミング配信する。

        rustscan コマンドを subprocess.Popen で起動し、1行出力されるごとに
        'data: <line>' として即時フラッシュ送信する。最後に returncode を
        'event: done' として送信してストリームを閉じる。
        """
        import subprocess as _sp
        import ipaddress

        target = query.get("host", [""])[0].strip()
        vrf = query.get("vrf", [""])[0].strip()
        if vrf and not re.match(r"^[a-zA-Z0-9_\-]+$", vrf):
            vrf = ""

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        if not target:
            self.wfile.write(b"event: error\ndata: Host parameter is required\n\n")
            self.wfile.flush()
            return

        # セキュリティ検証: 有効な IPv4 アドレスのみ許可 (コマンドインジェクション防止)
        try:
            ipaddress.IPv4Address(target)
        except ValueError:
            self.wfile.write(b"event: error\ndata: Invalid IPv4 address format\n\n")
            self.wfile.flush()
            return

        cmd = ["sudo"]
        if vrf:
            cmd.extend(["ip", "vrf", "exec", vrf])
        cmd.extend([
            "rustscan",
            "--accessible",
            "--no-banner",
            "--ulimit", "5000",
            "-b", "2000",
            "-t", "1000",
            "-a", target,
            "-r", "1-65535",
            "--",
            "-sV",
            "-Pn"
        ])

        try:
            proc = _sp.Popen(
                cmd,
                stdout=_sp.PIPE,
                stderr=_sp.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as e:
            self.wfile.write(f"event: error\ndata: {str(e)}\n\n".encode("utf-8"))
            self.wfile.flush()
            return

        ansi_re = re.compile(r'(?:\x1B[@-Z\\-_]|[\x80-\x9A\x9C-\x9F]|(?:\x1B\[|\x9B)[0-?]*[ -/]*[@-~])')
        assert proc.stdout is not None
        try:
            for line in iter(proc.stdout.readline, ""):
                if not line:
                    break
                clean_line = ansi_re.sub('', line).rstrip()
                self.wfile.write(f"data: {clean_line}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            proc.kill()
            return
        finally:
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()

        rc = proc.returncode
        self.wfile.write(f"event: done\ndata: {rc}\n\n".encode("utf-8"))
        self.wfile.flush()

    def handle_get_routes(self, query):
        vrf = query.get("vrf", [""])[0].strip()
        if vrf and not re.match(r"^[a-zA-Z0-9_\-]+$", vrf):
            vrf = ""

        vrfs = get_system_vrfs()

        if vrf:
            cmd4 = ["ip", "-4", "route", "show", "vrf", vrf]
            cmd6 = ["ip", "-6", "route", "show", "vrf", vrf]
        else:
            cmd4 = ["ip", "-4", "route", "show"]
            cmd6 = ["ip", "-6", "route", "show"]

        ret4, out4, _ = run_cmd(cmd4)
        ret6, out6, _ = run_cmd(cmd6)
        v6_lines = out6.strip().splitlines() if ret6 == 0 else []

        collapsed_v6 = []
        fe80_collapsed = False
        for line in v6_lines:
            if line.startswith("fe80::/64"):
                if not fe80_collapsed:
                    collapsed_v6.append("fe80::/64 (省略)")
                    fe80_collapsed = True
            else:
                collapsed_v6.append(line)

        self.send_json({
            "vrf": vrf or "default",
            "vrfs": vrfs,
            "ipv4_routes": out4.strip().splitlines() if ret4 == 0 else [],
            "ipv6_routes": collapsed_v6
        })

    def handle_get_interfaces(self, query):
        ret, out, _ = run_cmd(["ip", "-j", "-d", "addr", "show"])
        interfaces = []
        if ret == 0 and out.strip():
            try:
                interfaces = json.loads(out)
            except Exception:
                pass
        self.send_json({"interfaces": interfaces})

    def handle_get_ntp_status(self, query):
        ret_s, out_s, err_s = run_cmd(["docker", "exec", "chrony", "chronyc", "-n", "sources", "-v"], timeout=5)
        ret_t, out_t, err_t = run_cmd(["docker", "exec", "chrony", "chronyc", "-n", "tracking"], timeout=5)
        ret_c, out_c, err_c = run_cmd(["docker", "exec", "chrony", "chronyc", "-n", "clients"], timeout=5)

        tracking_data = {}
        if ret_t == 0 and out_t:
            for line in out_t.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    tracking_data[k.strip()] = v.strip()

        sources_data = []
        if ret_s == 0 and out_s:
            in_table = False
            for line in out_s.splitlines():
                if line.startswith("======="):
                    in_table = True
                    continue
                if not in_table:
                    continue
                line = line.strip()
                if not line:
                    continue
                parts = line.split(None, 6)
                if len(parts) >= 7:
                    ms = parts[0]
                    mode_char = ms[0] if len(ms) > 0 else "^"
                    state_char = ms[1] if len(ms) > 1 else "?"
                    sources_data.append({
                        "mode": mode_char,
                        "state": state_char,
                        "name": parts[1],
                        "stratum": parts[2],
                        "poll": parts[3],
                        "reach": parts[4],
                        "last_rx": parts[5],
                        "last_sample": parts[6],
                    })

        self.send_json({
            "success": (ret_s == 0 or ret_t == 0),
            "tracking": tracking_data,
            "sources": sources_data,
            "raw_sources": out_s.strip() if ret_s == 0 else (err_s.strip() or "取得失敗"),
            "raw_tracking": out_t.strip() if ret_t == 0 else (err_t.strip() or "取得失敗"),
            "raw_clients": out_c.strip() if ret_c == 0 else (err_c.strip() or "なし"),
        })

    def handle_get_cert_fortigate(self, query):
        cert_paths = [
            os.path.join(WORKSPACE_DIR, "mape-provisioning-server", "server.crt"),
            "/usr/local/bin/server.crt",
            "./mape-provisioning-server/server.crt"
        ]
        cert_content = ""
        for cp in cert_paths:
            if os.path.exists(cp):
                try:
                    with open(cp, "r", encoding="utf-8") as f:
                        cert_content = f.read().strip()
                        if cert_content:
                            break
                except Exception:
                    pass

        fg_cmd = f"""config vpn certificate ca
    edit "test_OCN_MAP_E_CA"
        set ca "{cert_content}"
    next
end"""

        fg_refresh_cmd = """config system vne-interface
    edit "vne.wan1"
        unset bmr-hostname
    next
end
diagnose test application vned 99"""

        self.send_json({
            "cert_found": bool(cert_content),
            "cert_content": cert_content,
            "import_command": fg_cmd,
            "refresh_command": fg_refresh_cmd
        })

    def handle_get_logs(self, query):
        # 複数指定 types (例: types=pppoe,kea,mape) または 単一指定 type を受け付け
        types_param = query.get("types", query.get("type", ["pppoe"]))[0]
        requested_types = [t.strip().lower() for t in types_param.split(",") if t.strip()]
        if not requested_types:
            requested_types = ["pppoe"]

        # 旧キーの統合互換
        normalized_types = []
        for t in requested_types:
            if t == "kea_hook":
                if "kea" not in normalized_types:
                    normalized_types.append("kea")
            elif t == "tunnel":
                if "mape" not in normalized_types:
                    normalized_types.append("mape")
            elif t in ["pppoe", "mape", "kea", "bind", "radvd", "chrony", "messages"]:
                if t not in normalized_types:
                    normalized_types.append(t)
            else:
                if t not in normalized_types:
                    normalized_types.append(t)

        lines_cnt = int(query.get("lines", ["200"])[0])
        lines_cnt = min(max(lines_cnt, 10), 1000)

        level_filter = query.get("level", ["INFO"])[0].upper()
        if level_filter not in ("ALL", "DEBUG", "INFO", "WARN", "ERROR"):
            level_filter = "INFO"

        level_order = {
            "DEBUG": 1,
            "INFO": 2,
            "WARN": 3,
            "ERROR": 4
        }
        min_level_val = level_order.get(level_filter, 2) if level_filter != "ALL" else 0

        type_labels = {
            "pppoe": "PPPoE",
            "mape": "MAP-E",
            "kea": "Kea",
            "bind": "DNS",
            "radvd": "RADVD",
            "chrony": "Chrony",
            "messages": "System"
        }

        all_entries = []
        sources = {}

        def fetch_single_type(t):
            raw_lines, sdesc = fetch_logs_by_type(t, lines_cnt, level_filter)
            return t, raw_lines, sdesc

        results = {}
        if normalized_types:
            with ThreadPoolExecutor(max_workers=min(len(normalized_types), 6)) as executor:
                futures = [executor.submit(fetch_single_type, t) for t in normalized_types]
                for f in futures:
                    t, raw_lines, sdesc = f.result()
                    results[t] = (raw_lines, sdesc)

        for t in normalized_types:
            raw_lines, sdesc = results.get(t, ([], "-"))
            sources[t] = sdesc
            last_ts = datetime.datetime.now().timestamp() - 86400

            for line in raw_lines:
                if not line.strip():
                    continue
                ts, time_str, cleaned_line = parse_line_datetime(line)
                if ts is not None:
                    last_ts = ts
                else:
                    last_ts += 0.0001
                    ts = last_ts
                    time_str = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

                detected_level = detect_log_level(cleaned_line)
                entry_level_val = level_order.get(detected_level, 2)
                if level_filter != "ALL" and entry_level_val < min_level_val:
                    continue

                all_entries.append({
                    "type": t,
                    "label": type_labels.get(t, t.upper()),
                    "level": detected_level,
                    "ts": ts,
                    "time": time_str,
                    "text": cleaned_line
                })

        # 全ログを時系列（ts昇順）でソート
        all_entries.sort(key=lambda x: x["ts"])
        merged_entries = all_entries[-lines_cnt:]

        # 互換テキスト形式
        plain_lines = [
            f"[{e['label']}] {e['time']} | {e['text']}" for e in merged_entries
        ]

        source_desc_summary = " / ".join([f"{type_labels.get(k, k)}: {v}" for k, v in sources.items()])

        self.send_json({
            "types": normalized_types,
            "type": normalized_types[0] if normalized_types else "pppoe",
            "file": source_desc_summary,
            "sources": sources,
            "entries": merged_entries,
            "lines": plain_lines
        })

    def handle_get_config(self, query):
        live = ConfigManager.get_live_config()
        staging = ConfigManager.get_staging_config()
        has_pending = ConfigManager.has_pending_changes()
        self.send_json({
            "live": live,
            "staging": staging,
            "has_pending_changes": has_pending
        })

    def handle_get_config_template(self, query):
        """テンプレートから初期設定を返す (管理LAN設定は現在値を維持)"""
        tmpl = ConfigManager.get_template_config()
        self.send_json({"template": tmpl})

    def handle_get_mape_conf_vars(self, query):
        """map-e.conf から MAP-E 計算に必要な変数をJSONで返す"""
        from config import APP_CONFIG
        cfg = APP_CONFIG
        raw = cfg.raw_vars if hasattr(cfg, 'raw_vars') else {}
        # ConfigManager 経由でライブ設定を取得
        live = ConfigManager.get_live_config()
        ll = live.get("low_layer", {})
        ipoe = live.get("ipoe_common", {})
        slaac = live.get("slaac", {})
        dhcp_pd = live.get("dhcp_pd", {})

        def _g(key, default=""):
            for d in [ll, ipoe, slaac, dhcp_pd]:
                if key in d and str(d[key]).strip():
                    return str(d[key]).strip()
            return default

        base_subnet = _g("BASE_SUBNET", "2400:4150")
        br_prefix_raw = _g("BR_PREFIX", f"{base_subnet}:3620")
        # ${BASE_SUBNET} 参照を展開
        br_prefix = br_prefix_raw.replace("${BASE_SUBNET}", base_subnet)
        br_ipv4_addr = _g("BR_IPV4_ADDR", "100.127.255.255")
        br_ipv4_pool = _g("BR_IPV4_POOL", "10.248.0.0/16")
        slaac_br_base = _g("SLAAC_BR_BASE", "1000")
        slaac_fix_br_prefix = _g("SLAAC_FIX_BR_PREFIX", "3000")
        pd_pool = _g("PD_POOL", "2000")
        pd_fix_pool = _g("PD_FIX_POOL", "4000")
        slaac_dyn_vlans = _g("SLAAC_DYN_VLANS", "")
        pd_dyn_vlans = _g("PD_DYN_VLANS", "")
        slaac_fix_vlans = _g("SLAAC_FIX_VLANS", "")
        pd_fix_vlans = _g("PD_FIX_VLANS", "")

        # MapeCommon.pm 準拠の BR IPv6 計算 (BR_PREFIX::ipv4_to_hex_hextets)
        br_ipv6 = ""
        try:
            octets = [int(x) for x in br_ipv4_addr.strip().split(".")]
            if len(octets) == 4:
                hex_hextets = f"{octets[0]:02x}{octets[1]:02x}:{octets[2]:02x}{octets[3]:02x}"
                clean_pfx = br_prefix.strip().rstrip(":")
                br_ipv6 = f"{clean_pfx}::{hex_hextets}"
        except Exception:
            br_ipv6 = ""

        # map-e-static-ip.conf から登録済みエントリを読み込む
        static_entries = []
        for try_path in [
            os.path.join(WORKSPACE_DIR, "map-e-static-ip.conf"),
            "/etc/map-e/map-e-static-ip.conf"
        ]:
            if os.path.exists(try_path):
                try:
                    with open(try_path, "r", encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            line = line.strip()
                            if not line or line.startswith("#"):
                                continue
                            parts = [p.strip() for p in line.split(",")]
                            if len(parts) >= 2:
                                static_entries.append({
                                    "key": parts[0],
                                    "ipv4": parts[1],
                                    "note": parts[2] if len(parts) > 2 else ""
                                })
                    break
                except Exception:
                    pass

        self.send_json({
            "BASE_SUBNET": base_subnet,
            "BR_PREFIX": br_prefix,
            "BR_IPV4_ADDR": br_ipv4_addr,
            "BR_IPV4_POOL": br_ipv4_pool,
            "BR_IPV6": br_ipv6,
            "SLAAC_BR_BASE": slaac_br_base,
            "SLAAC_FIX_BR_PREFIX": slaac_fix_br_prefix,
            "PD_POOL": pd_pool,
            "PD_FIX_POOL": pd_fix_pool,
            "SLAAC_DYN_VLANS": slaac_dyn_vlans,
            "PD_DYN_VLANS": pd_dyn_vlans,
            "SLAAC_FIX_VLANS": slaac_fix_vlans,
            "PD_FIX_VLANS": pd_fix_vlans,
            "static_entries": static_entries,
        })

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # GET API ルーティングテーブル
        get_routes = {
            "/api/clients": self.handle_get_clients,
            "/api/vrfs": self.handle_get_vrfs,
            "/api/ping": self.handle_get_ping,
            "/api/ping/stream": self.handle_get_ping_stream,
            "/api/rustscan/stream": self.handle_get_rustscan_stream,
            "/api/routes": self.handle_get_routes,
            "/api/interfaces": self.handle_get_interfaces,
            "/api/cert/fortigate": self.handle_get_cert_fortigate,
            "/api/logs": self.handle_get_logs,
            "/api/ntp/status": self.handle_get_ntp_status,
            "/api/config": self.handle_get_config,
            "/api/config/template": self.handle_get_config_template,
            "/api/mape-conf-vars": self.handle_get_mape_conf_vars,
        }

        if path in get_routes:
            get_routes[path](query)
            return

        # 静的 HTML ページ
        page_routes = {
            "/": "index.html",
            "/index.html": "index.html",
            "/config": "config.html",
            "/tools/ping": "tool_ping.html",
            "/tools/routes": "tool_routes.html",
            "/tools/interfaces": "tool_interfaces.html",
            "/tools/logs": "tool_logs.html",
            "/tools/mape-calc": "tool_mape_calc.html",
        }
        if path in page_routes:
            self.send_file(os.path.join(STATIC_DIR, page_routes[path]))
            return

        # 静的アセット配信
        static_target = os.path.normpath(os.path.join(STATIC_DIR, path.lstrip("/")))
        if static_target.startswith(STATIC_DIR) and os.path.isfile(static_target):
            mime = "text/plain"
            if static_target.endswith(".css"):
                mime = "text/css"
            elif static_target.endswith(".js"):
                mime = "application/javascript"
            elif static_target.endswith(".html"):
                mime = "text/html"
            self.send_file(static_target, mime)
            return

        self.send_error(404, "Not Found")

    # --- POST Handlers ---

    def handle_post_disconnect_pppoe(self, body):
        pid = body.get("pid")
        ifname = body.get("interface", "")
        if not pid:
            self.send_json({"error": "PID is required for PPPoE disconnect"}, 400)
            return

        try:
            run_cmd(["kill", "-TERM", str(pid)], privileged=True)
            time.sleep(0.5)
            ret, _, _ = run_cmd(["kill", "-0", str(pid)], privileged=True)
            if ret == 0:
                run_cmd(["kill", "-KILL", str(pid)], privileged=True)

            self.send_json({
                "success": True,
                "message": f"PPPoE session {ifname} (PID {pid}) disconnected."
            })
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def handle_post_disconnect_mape(self, body):
        prefix = body.get("prefix", "").strip()
        ipv4 = body.get("ipv4", "").strip()
        ce_ipv6 = body.get("ce_ipv6", "").strip()

        results = []

        # 1. Kea DHCPv6 リース削除
        if prefix and prefix != "-":
            pd_res = KeaClient.delete_pd_lease(prefix)
            results.extend(pd_res)

        # 2. Linux カーネルトンネル経路 (mpe-common) 削除
        if ipv4 and ipv4 != "-":
            ret, _, err = run_cmd(["ip", "route", "del", f"{ipv4}/32", "dev", "mpe-common"], privileged=True)
            if ret != 0:
                ret, _, err = run_cmd(["ip", "route", "del", ipv4, "dev", "mpe-common"], privileged=True)
            results.append(f"Route del {ipv4}: code {ret}")
        else:
            ret, out, _ = run_cmd(["ip", "route", "show", "dev", "mpe-common"])
            if ret == 0:
                for line in out.splitlines():
                    match = False
                    if ce_ipv6 and ce_ipv6 != "-" and ce_ipv6 in line:
                        match = True
                    clean_pfx = prefix.split("/")[0].rstrip(":")
                    if clean_pfx and clean_pfx in line:
                        match = True
                    if match:
                        m_v4 = re.match(r"^([\d\.]+)(?:/32)?", line.strip())
                        if m_v4:
                            del_v4 = m_v4.group(1)
                            ret_d, _, _ = run_cmd(["ip", "route", "del", f"{del_v4}/32", "dev", "mpe-common"], privileged=True)
                            if ret_d != 0:
                                ret_d, _, _ = run_cmd(["ip", "route", "del", del_v4, "dev", "mpe-common"], privileged=True)
                            results.append(f"Route del {del_v4}: code {ret_d}")

        # 3. NDP キャッシュ削除
        if ce_ipv6 and ce_ipv6 != "-":
            ret, _, _ = run_cmd(["ip", "-6", "neigh", "flush", "to", ce_ipv6], privileged=True)
            results.append(f"NDP flush {ce_ipv6}: code {ret}")

        self.send_json({
            "success": True,
            "message": f"MAP-E client disconnected: {', '.join(results)}"
        })

    def handle_post_config_stage(self, body):
        success = ConfigManager.save_staging(body)
        errors = ConfigManager.validate_staging_config(body)
        self.send_json({
            "success": success,
            "has_pending_changes": ConfigManager.has_pending_changes(),
            "errors": errors
        })

    def handle_post_config_discard(self, body):
        success = ConfigManager.discard_staging()
        live = ConfigManager.get_live_config()
        self.send_json({
            "success": success,
            "has_pending_changes": False,
            "config": live
        })

    def handle_post_config_apply(self, body):
        if body and isinstance(body, dict) and any(k in body for k in ["low_layer", "pppoe", "ipoe_common", "slaac", "dhcp_pd"]):
            ConfigManager.save_staging(body)
        result = ConfigManager.apply_staging()
        self.send_json(result)

    def handle_post_containers_restart(self, body):
        self.send_json(ConfigManager.restart_containers())

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(length) if length > 0 else b"{}"

        try:
            body = json.loads(post_data.decode("utf-8"))
        except Exception:
            body = {}

        post_routes = {
            "/api/disconnect/pppoe": self.handle_post_disconnect_pppoe,
            "/api/disconnect/mape": self.handle_post_disconnect_mape,
            "/api/config/stage": self.handle_post_config_stage,
            "/api/config/discard": self.handle_post_config_discard,
            "/api/config/apply": self.handle_post_config_apply,
            "/api/containers/restart": self.handle_post_containers_restart,
        }

        if path in post_routes:
            post_routes[path](body)
            return

        self.send_error(404, "Action Not Found")


# =============================================================================
