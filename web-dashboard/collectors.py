# web-dashboard/collectors.py
"""
Section 3: Data Collectors
  - PPPoECollector : PPPoE セッション情報の収集・整形・死活監視
  - MAPECollector  : MAP-E トンネル情報の収集・整形・死活監視
"""

import os
import sys
import re
import json
import datetime
import time
import ipaddress
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

from constants import WORKSPACE_DIR, KEA_LEASE_CSV, TUNNEL_EVENT_LOG, LOG_DIR, DATA_DIR, PPPOE_SESSION_CACHE
from config import ConfigManager
from utils import (
    run_cmd, ping_target, format_duration, normalize_datetime,
    load_pppoe_conf_notes, load_static_ip_notes,
    load_kea_subnet_vlan_map, load_tunnel_events, classify_mape_mode,
)


def _get_app_config():
    from config import APP_CONFIG  # noqa: PLC0415
    return APP_CONFIG


# =============================================================================
# バックグラウンド死活監視（Ping）キャッシュ & ワーカー
# =============================================================================
# キャッシュ: key -> {"ok": bool, "rtt": str, "ping_target": str, "updated_at": float}
_PING_CACHE = {}
_PING_LOCK = threading.Lock()
_WATCHED_TARGETS = {}
_WORKER_STARTED = False


def get_cached_ping(key):
    """キャッシュから Ping 死活状態を取得（完全ノンブロッキング）"""
    if not key or key in ("-", "0.0.0.0", "::"):
        return False, None, key
    with _PING_LOCK:
        entry = _PING_CACHE.get(key)
        if entry:
            return entry.get("ok", False), entry.get("rtt"), entry.get("ping_target", key)
    return False, None, key


def get_mape_ping_ipv4(v4_str):
    """
    MAP-E トンネル経由で対向 CE ルーターへ死活監視 Ping を送信するためのターゲット IPv4 を算出する。
    - /32 (動的・固定1IP): そのままの IP アドレス
    - サブネット (/28, /29 等): ルーターの先頭ホストアドレス (network_address + 1)
    """
    if not v4_str or v4_str in ("-", "0.0.0.0"):
        return None
    try:
        clean = v4_str.strip()
        if "/" in clean:
            net = ipaddress.IPv4Network(clean, strict=False)
            if net.prefixlen < 31:
                return str(net.network_address + 1)
            else:
                return str(net.network_address)
        else:
            return clean
    except Exception:
        return v4_str.split("/")[0]


def sync_active_ping_targets(pppoe_clients, mape_clients):
    """現在接続中のクライアント一覧に基づき監視対象ターゲットを同期更新"""
    new_targets = {}
    for c in pppoe_clients:
        ip = c.get("remote_ip")
        if ip and ip not in ("-", "0.0.0.0"):
            new_targets[ip] = {
                "type": "pppoe",
                "ip": ip,
                "vrf_device": c.get("vrf_device"),
                "interface": c.get("interface")
            }

    for c in mape_clients:
        ce_ip6 = c.get("ce_ipv6")
        if ce_ip6 and ce_ip6 not in ("-", "::"):
            v4_raw = c.get("ipv4")
            v4_target = get_mape_ping_ipv4(v4_raw)
            new_targets[ce_ip6] = {
                "type": "mape",
                "ce_ipv6": ce_ip6,
                "gw_ipv6": c.get("gateway_ipv6"),
                "iface": c.get("interface"),
                "ipv4": v4_raw,
                "target_v4": v4_target,
                "status": c.get("status")
            }

    with _PING_LOCK:
        _WATCHED_TARGETS.clear()
        _WATCHED_TARGETS.update(new_targets)


def check_ndp_status(ce_ip6, gw_ip6=None, iface_val=None, neigh_cache=None, hwaddr=None):
    """
    IPv6近隣探索 (NDP) キャッシュから対向CEのL2/L3到達性を判定する。
    Firewall等でICMP Pingが遮断されていても、NDPが REACHABLE / STALE 等であれば接続中と判定。
    ただし、Gateway IPv6 が明示的に FAILED の場合や、PERMANENT 単体の場合は未達とみなす。
    """
    if neigh_cache is None:
        neigh_cache = MAPECollector._load_neigh_cache()

    valid_states = {"REACHABLE", "DELAY", "PROBE"}

    # 1. Gateway IPv6 の状態をチェック (リンクローカルの疎通性)
    if gw_ip6 and gw_ip6 not in ("-", "::"):
        for (nip, ndev), ninfo in neigh_cache.items():
            if nip == gw_ip6 and (not iface_val or iface_val == "-" or ndev == iface_val):
                st = ninfo.get("state", "").upper()
                mac = ninfo.get("mac", "")
                if st in valid_states and mac:
                    return True, st, mac
                if st == "FAILED":
                    return False, "FAILED", ""

    # 2. CE IPv6 直近エントリをチェック
    if ce_ip6 and ce_ip6 not in ("-", "::"):
        if iface_val and iface_val != "-":
            entry = neigh_cache.get((ce_ip6, iface_val))
            if entry:
                st = entry.get("state", "").upper()
                mac = entry.get("mac", "")
                if st in valid_states and mac:
                    return True, st, mac
                if st == "FAILED":
                    return False, "FAILED", ""

        for (nip, ndev), ninfo in neigh_cache.items():
            if nip == ce_ip6:
                st = ninfo.get("state", "").upper()
                mac = ninfo.get("mac", "")
                if st in valid_states and mac:
                    return True, st, mac
                if st == "FAILED":
                    return False, "FAILED", ""

    # 3. MAC アドレス指定によるチェック (CE物理インターフェースの健全性)
    if hwaddr and hwaddr != "-":
        mac_norm = hwaddr.lower().replace("-", ":")
        for (nip, ndev), ninfo in neigh_cache.items():
            if iface_val and iface_val != "-" and ndev != iface_val:
                continue
            if ninfo.get("mac", "").lower() == mac_norm:
                st = ninfo.get("state", "").upper()
                if st in valid_states:
                    return True, st, ninfo.get("mac", "")

    # 4. CE IPv6 と同一 /64 サブネット内の NDP エントリをチェック
    if ce_ip6 and ce_ip6 not in ("-", "::"):
        try:
            ce_net = ipaddress.IPv6Network(f"{ce_ip6}/64", strict=False)
            for (nip, ndev), ninfo in neigh_cache.items():
                if iface_val and iface_val != "-" and ndev != iface_val:
                    continue
                try:
                    n_addr = ipaddress.IPv6Address(nip)
                    if n_addr in ce_net:
                        st = ninfo.get("state", "").upper()
                        mac = ninfo.get("mac", "")
                        if st in valid_states and mac:
                            return True, st, mac
                except Exception:
                    pass
        except Exception:
            pass

    return False, "FAILED", ""


_TUNNEL_LAST_SEEN = {}  # ipv4 -> float timestamp


def _cleanup_stale_tunnels():
    """ネイバーが見えない (NDP 不達) 状態が 10 分 (600秒) 以上継続した MAP-E トンネル経路・ネイバーを自動削除"""
    now = time.time()
    try:
        active_tunnels = MAPECollector._get_active_tunnels()
        if not active_tunnels:
            return
        neigh_cache = MAPECollector._load_neigh_cache()
        events = load_tunnel_events()

        try:
            all_clients = MAPECollector.collect()
            client_map = {c.get("ipv4"): c for c in all_clients if c.get("ipv4")}
        except Exception:
            client_map = {}

        for t in active_tunnels:
            v4 = t.get("ipv4")
            ce_ip6 = t.get("ce_ipv6")
            if not v4 or not ce_ip6:
                continue

            c_info = client_map.get(v4, {})
            hw = c_info.get("hwaddr")
            gw = c_info.get("gateway_ipv6")
            iface = c_info.get("interface")
            prefix = c_info.get("prefix")

            # ネイバーが可視 (L2/L3 近隣生存) か判定
            ping_ok, _, _ = get_cached_ping(ce_ip6)
            ndp_ok, ndp_st, _ = check_ndp_status(ce_ip6, gw, iface, neigh_cache, hwaddr=hw)
            is_alive = bool(ping_ok or ndp_ok)

            if is_alive:
                _TUNNEL_LAST_SEEN[v4] = now
            else:
                if v4 not in _TUNNEL_LAST_SEEN:
                    ev = events.get(v4, {})
                    t_str = ev.get("last_provisioned") or ev.get("tunnel_created")
                    t_epoch = None
                    if t_str and t_str != "-":
                        try:
                            clean_t = t_str.split("+")[0].split(".")[0].replace(" ", "T")
                            dt = datetime.datetime.fromisoformat(clean_t)
                            t_epoch = dt.timestamp()
                        except Exception:
                            pass
                    _TUNNEL_LAST_SEEN[v4] = t_epoch if t_epoch else (now - 601)

                inactive_sec = now - _TUNNEL_LAST_SEEN[v4]
                if inactive_sec >= 600:  # 10分以上ネイバー不達
                    print(f"[TUNNEL-AUTOCLEAN] Purging stale tunnel route {v4} -> {ce_ip6} (neighbor unseen for {int(inactive_sec)}s)", file=sys.stderr)
                    # 1. mpe-common トンネルルート削除
                    run_cmd(["ip", "route", "del", f"{v4}/32", "dev", "mpe-common"], privileged=True)
                    run_cmd(["ip", "route", "del", v4, "dev", "mpe-common"], privileged=True)

                    # 2. 静的 (PERMANENT) ネイバーエントリの削除
                    if ce_ip6 and iface:
                        run_cmd(["ip", "-6", "neigh", "del", ce_ip6, "dev", iface], privileged=True)
                    if ce_ip6:
                        for (nip, ndev), ninfo in neigh_cache.items():
                            if nip == ce_ip6:
                                run_cmd(["ip", "-6", "neigh", "del", nip, "dev", ndev], privileged=True)

                    # 3. 委任プレフィックスのルーティングが残っていれば削除
                    if prefix and prefix not in ("-", ""):
                        run_cmd(["ip", "-6", "route", "del", prefix], privileged=True)

                    _TUNNEL_LAST_SEEN.pop(v4, None)
    except Exception as e:
        print(f"[TUNNEL-AUTOCLEAN] Error: {e}", file=sys.stderr)


def _background_ping_worker_loop():
    """定期的に登録ターゲットの死活確認および放置トンネル自動削除を行うバックグラウンドスレッド"""
    while True:
        try:
            _cleanup_stale_tunnels()
        except Exception as e:
            print(f"[PingWorker] Error in tunnel cleanup: {e}", file=sys.stderr)

        try:
            with _PING_LOCK:
                targets = dict(_WATCHED_TARGETS)

            if targets:
                def probe_target(item):
                    key, info = item
                    ttype = info.get("type")
                    if ttype == "pppoe":
                        ip = info.get("ip")
                        vrf_dev = info.get("vrf_device")
                        iface = info.get("interface")
                        ok, rtt = ping_target(ip, timeout=0.5, vrf=vrf_dev, iface=iface)
                        return key, ok, rtt, ip
                    elif ttype == "mape":
                        # MAP-E トンネルの IPv4 アドレスにのみ Ping を送信 (トンネル未確立時の誤判定を防止)
                        v4_target = info.get("target_v4") or get_mape_ping_ipv4(info.get("ipv4"))
                        v4_ok, rtt = (False, None)
                        if v4_target and v4_target not in ("-", "0.0.0.0"):
                            v4_ok, rtt = ping_target(v4_target, timeout=0.5)

                        # アンダーレイ IPv6 (Gateway または CE IPv6) の近隣探索をリフレッシュ
                        gw = info.get("gw_ipv6")
                        iface = info.get("iface")
                        ce_v6 = info.get("ce_ipv6")
                        if gw and gw not in ("-", "::") and iface:
                            ping_target(gw, timeout=0.2, iface=iface)
                        elif ce_v6 and ce_v6 not in ("-", "::") and iface:
                            ping_target(ce_v6, timeout=0.2, iface=iface)

                        return key, v4_ok, rtt, v4_target or key
                    return key, False, None, key

                with ThreadPoolExecutor(max_workers=10) as executor:
                    results = list(executor.map(probe_target, targets.items()))

                with _PING_LOCK:
                    now = time.time()
                    for key, ok, rtt, actual_target in results:
                        _PING_CACHE[key] = {
                            "ok": ok,
                            "rtt": rtt,
                            "ping_target": actual_target,
                            "updated_at": now
                        }
        except Exception as e:
            print(f"[PingWorker] Error in background ping: {e}", file=sys.stderr)

        time.sleep(5)


def start_background_ping_worker():
    """バックグラウンド死活監視スレッドを起動（冪等）"""
    global _WORKER_STARTED
    if _WORKER_STARTED:
        return
    _WORKER_STARTED = True

    # 起動直後の初期ターゲット登録
    try:
        tunnels = MAPECollector._get_active_tunnels()
        init_targets = {}
        for t in tunnels:
            cip = t.get("ce_ipv6")
            if cip:
                init_targets[cip] = {"type": "mape", "ce_ipv6": cip, "ipv4": t.get("ipv4")}
        with _PING_LOCK:
            _WATCHED_TARGETS.update(init_targets)
    except Exception:
        pass

    t = threading.Thread(target=_background_ping_worker_loop, daemon=True, name="BackgroundPingWorker")
    t.start()


# Section 3: Data Collectors
# =============================================================================

class PPPoECollector:
    """PPPoE セッション情報の収集・整形および並行死活監視を担当するコレクター"""

    @classmethod
    def collect(cls):
        notes = load_pppoe_conf_notes()

        # 1. 稼働中の ppp インターフェースを取得
        ppp_interfaces = cls._get_ppp_interfaces()

        # 2. 稼働中の pppd プロセス情報を取得
        pppd_procs = cls._get_pppd_procs()

        # 3. /var/log/pppoe-server.log から認証・IP マッピングを取得
        auth_by_remote_ip, auth_by_pid = cls._parse_auth_logs()

        # 4. セッション統合
        active_clients = cls._synthesize_sessions(ppp_interfaces, pppd_procs, auth_by_remote_ip, notes)

        # 5. 並行 Ping 実行
        return cls._ping_enrich(active_clients)

    @staticmethod
    def _get_ppp_interfaces():
        ret, out, _ = run_cmd(["ip", "-j", "addr", "show"])
        interfaces = []
        if ret == 0 and out.strip():
            try:
                interfaces = json.loads(out)
            except Exception:
                pass

        ppp_interfaces = {}
        for iface in interfaces:
            name = iface.get("ifname", "")
            if name.startswith("ppp"):
                local_ip, remote_ip = "", ""
                for addr in iface.get("addr_info", []):
                    if addr.get("family") == "inet":
                        local_ip = addr.get("local", "")
                        remote_ip = addr.get("address", "")
                ppp_interfaces[name] = {
                    "local_ip": local_ip,
                    "remote_ip": remote_ip,
                    "flags": iface.get("flags", [])
                }
        return ppp_interfaces

    @staticmethod
    def _get_pppd_procs():
        sys_uptime = 0.0
        try:
            with open("/proc/uptime", "r") as f:
                sys_uptime = float(f.read().split()[0])
        except Exception:
            pass
        clk_tck = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100

        # pppd の PID 一覧を取得 (pgrep を優先使用して全 /proc 走査をスキップ)
        candidate_pids = []
        ret, out, _ = run_cmd(["pgrep", "-x", "pppd"])
        if ret == 0 and out.strip():
            candidate_pids = [int(p) for p in out.split() if p.isdigit()]
        else:
            for pid_str in os.listdir("/proc"):
                if pid_str.isdigit():
                    candidate_pids.append(int(pid_str))

        pppd_procs = {}
        for pid in candidate_pids:
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as f:
                    raw = f.read().decode("latin1", errors="ignore").split("\x00")
                if any("pppd" in part for part in raw):
                    cmd_line = " ".join(raw)
                    sess_match = re.search(r"rp_pppoe_sess\s+(\d+):([0-9a-fA-F:]+)", cmd_line)
                    nic_match = re.search(r"nic-([^\s]+)", cmd_line)
                    remote_mac_match = re.search(r"remotenumber\s+([0-9a-fA-F:]+)", cmd_line)

                    proc_uptime = 0.0
                    try:
                        with open(f"/proc/{pid}/stat", "r") as f:
                            fields = f.read().split()
                            starttime_ticks = int(fields[21])
                            proc_uptime = max(0.0, sys_uptime - (starttime_ticks / clk_tck))
                    except Exception:
                        pass

                    sess_id = int(sess_match.group(1)) if sess_match else None
                    mac_val = (remote_mac_match.group(1) if remote_mac_match else (sess_match.group(2) if sess_match else "")).upper()
                    pppd_procs[pid] = {
                        "sess_id": sess_id,
                        "mac": mac_val,
                        "nic": nic_match.group(1) if nic_match else "",
                        "proc_uptime": proc_uptime,
                        "cmd": cmd_line
                    }
            except Exception:
                pass
        return pppd_procs

    @classmethod
    def _load_session_cache(cls):
        """
        セッション情報キャッシュ (pppoe_sessions.json) を読み込む。
        停止後の誤情報を防ぐため、最終更新タイムスタンプが 1時間 (3600秒) 以上前なら破棄してクリアする。
        """
        if not os.path.exists(PPPOE_SESSION_CACHE):
            return {}
        try:
            with open(PPPOE_SESSION_CACHE, "r", encoding="utf-8") as f:
                data = json.load(f)
            now = time.time()
            last_updated = data.get("last_updated", 0)
            if now - last_updated > 3600:
                # 1時間以上前なら一旦クリア
                return {}
            return data.get("sessions", {})
        except Exception as e:
            print(f"[PPPoE] Error loading session cache {PPPOE_SESSION_CACHE}: {e}", file=sys.stderr)
            return {}

    @classmethod
    def _save_session_cache(cls, sessions_dict):
        """
        現在アクティブなセッション情報をアトミックにキャッシュファイルへ保存・更新する。
        """
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            tmp_file = PPPOE_SESSION_CACHE + ".tmp"
            payload = {
                "last_updated": time.time(),
                "sessions": sessions_dict
            }
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp_file, PPPOE_SESSION_CACHE)
        except Exception as e:
            print(f"[PPPoE] Error saving session cache {PPPOE_SESSION_CACHE}: {e}", file=sys.stderr)

    @staticmethod
    def _parse_auth_logs():
        auth_by_remote_ip = {}
        auth_by_pid = {}
        log_paths = [
            os.path.join(LOG_DIR, "pppoe-server.log.1"),
            os.path.join(LOG_DIR, "pppoe-server.log")
        ]
        pending_by_pid = {}
        for log_path in log_paths:
            if not os.path.exists(log_path):
                continue
            try:
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        m_auth = re.search(r"^([A-Z][a-z]{2}\s+\d+\s+[\d:]+)\s+.*pppd\[(\d+)\]:\s+Peer\s+(.+?)\s+authenticated", line)
                        if m_auth:
                            tstr, p, u = m_auth.groups()
                            p = int(p)
                            pending = pending_by_pid.setdefault(p, {})
                            pending["username"] = u.strip()
                            pending["time_str"] = tstr

                        m_mac = re.search(r"pppd\[(\d+)\]:\s+peer from calling number\s+([0-9a-fA-F:]+)", line)
                        if m_mac:
                            p, mac = m_mac.groups()
                            p = int(p)
                            pending = pending_by_pid.setdefault(p, {})
                            pending["mac"] = mac.upper()

                        m_ip = re.search(r"^([A-Z][a-z]{2}\s+\d+\s+[\d:]+)\s+.*pppd\[(\d+)\]:\s+remote IP address\s+([\d\.]+)", line)
                        if m_ip:
                            tstr, p, rip = m_ip.groups()
                            p = int(p)
                            pending = pending_by_pid.setdefault(p, {})
                            pending["remote_ip"] = rip
                            pending["time_str"] = tstr
                            # Snapshot current pending authentication state for this remote IP
                            auth_by_remote_ip[rip] = dict(pending)
                            auth_by_pid[p] = dict(pending)
            except Exception as e:
                print(f"[PPPoE] Error parsing log {log_path}: {e}", file=sys.stderr)
        return auth_by_remote_ip, auth_by_pid

    @classmethod
    def _synthesize_sessions(cls, ppp_interfaces, pppd_procs, auth_by_remote_ip, notes):
        # 過去のセッションキャッシュを読み込み（1時間以上前なら自動クリア済み）
        session_cache = cls._load_session_cache()
        updated_cache = {}
        now = time.time()

        procs_by_mac = {}
        for pid, pdata in pppd_procs.items():
            procs_by_mac.setdefault(pdata["mac"], []).append((pid, pdata))

        used_pids = set()
        active_clients = []

        for ifname, ifinfo in sorted(ppp_interfaces.items()):
            remote_ip = ifinfo["remote_ip"]
            local_ip = ifinfo["local_ip"]
            matched_auth = auth_by_remote_ip.get(remote_ip, {})

            # 過去に接続したセッション情報（キャッシュ）
            cached = session_cache.get(remote_ip, {})

            username = matched_auth.get("username") or cached.get("username")
            user_conf = notes.get(username) if username else None
            if not user_conf:
                for uname, udata in notes.items():
                    if udata.get("configured_ip", "").split("/")[0] == remote_ip:
                        username = uname
                        user_conf = udata
                        break
            user_conf = user_conf or {}
            username = username or "-"

            note = user_conf.get("note", "")
            service_name = user_conf.get("service_name", user_conf.get("ac_name", "")) or cached.get("service_name", "")
            ac_name = service_name
            configured_ip = user_conf.get("configured_ip", "")
            mac = matched_auth.get("mac") or cached.get("mac") or "-"

            matched_pid = None
            # Match strictly by MAC address (and local_ip if multiple sessions from the same MAC)
            if mac and mac != "-" and mac in procs_by_mac:
                for pid, pdata in procs_by_mac[mac]:
                    if pid not in used_pids:
                        if local_ip and local_ip != "-" and f"{local_ip}:" not in pdata.get("cmd", ""):
                            continue
                        matched_pid = pid
                        used_pids.add(pid)
                        break

            # If MAC was not found in auth logs, get it from matched pppd process
            if (not mac or mac == "-") and matched_pid:
                mac = pppd_procs.get(matched_pid, {}).get("mac", "-")

            vlan = pppd_procs.get(matched_pid, {}).get("nic", "-") if matched_pid else cached.get("vlan", "-")
            
            # 接続開始時刻: ログの time_str を優先、なければキャッシュから取得
            raw_time_str = matched_auth.get("time_str") or cached.get("connect_time") or "-"
            connect_time_str = normalize_datetime(raw_time_str)

            uptime_str = "-"
            dur_sec = 0.0
            if matched_pid and matched_pid in pppd_procs:
                dur_sec = pppd_procs[matched_pid].get("proc_uptime", 0.0)
                uptime_str = format_duration(dur_sec)

            # 現在接続中の情報をキャッシュに登録・更新（初回接続時刻を保持）
            if remote_ip and remote_ip != "-":
                first_connect_time = cached.get("connect_time")
                if not first_connect_time or first_connect_time == "-":
                    first_connect_time = connect_time_str

                updated_cache[remote_ip] = {
                    "interface": ifname,
                    "remote_ip": remote_ip,
                    "local_ip": local_ip,
                    "username": username,
                    "mac": mac,
                    "vlan": vlan,
                    "service_name": service_name,
                    "connect_time": first_connect_time,
                    "updated_at": now
                }

            cidr_suffix = "/32"
            if configured_ip and "/" in configured_ip:
                try:
                    cidr_suffix = "/" + configured_ip.split("/")[1].strip()
                except Exception:
                    cidr_suffix = "/32"
            remote_ip_cidr = f"{remote_ip}{cidr_suffix}" if remote_ip and remote_ip != "-" else "-"
            ip_mode = "固定IP" if (configured_ip and configured_ip != "-") else "動的"

            # VRF 判定 (カーネル上の master または サービス設定)
            actual_master = None
            master_link = f"/sys/class/net/{ifname}/master"
            if os.path.islink(master_link):
                try:
                    actual_master = os.path.basename(os.readlink(master_link))
                except Exception:
                    pass

            srv_key = service_name or ac_name
            vrf_map = {}
            try:
                for s in getattr(_get_app_config(), "pppoe_servers", []):
                    sn = s.get("service_name") or s.get("ac_name")
                    if sn:
                        vrf_map[sn] = s.get("vrf_enabled", False)
            except Exception:
                pass

            cfg_vrf = vrf_map.get(srv_key, False)
            is_vrf = bool(actual_master or cfg_vrf)
            vrf_dev = actual_master or (f"vrf-{srv_key.replace(' ', '_')[:11]}" if cfg_vrf else None)

            active_clients.append({
                "interface": ifname,
                "remote_ip": remote_ip,
                "remote_ip_cidr": remote_ip_cidr,
                "local_ip": local_ip,
                "username": username,
                "service_name": service_name,
                "ac_name": ac_name,
                "note": note,
                "configured_ip": configured_ip,
                "ip_mode": ip_mode,
                "mac": mac,
                "vlan": vlan,
                "connect_time": connect_time_str,
                "uptime": uptime_str,
                "uptime_sec": dur_sec,
                "pid": matched_pid,
                "vrf_enabled": is_vrf,
                "vrf_device": vrf_dev,
                "status": "active"
            })

        # PPPoE は LCP keepalive で管理されているためパージせずそのまま全セッションを返す
        cls._save_session_cache(updated_cache)
        return active_clients

    @staticmethod
    def _ping_enrich(clients):
        for c in clients:
            ip = c.get("remote_ip")
            ok, rtt, _ = get_cached_ping(ip)
            c["ping_ok"] = ok
            c["ping_rtt"] = rtt
            c["is_online"] = bool(ok or c.get("status") == "active")
        return clients


class MAPECollector:
    """MAP-E クライアント情報の検出・集約および並行死活監視を担当するコレクター"""

    @classmethod
    def collect(cls):
        subnet_map = load_kea_subnet_vlan_map()
        tunnel_events = load_tunnel_events()
        static_notes_mac, static_notes_ip = load_static_ip_notes()

        # 1. 稼働中のトンネルルート (mpe-common) を取得
        active_tunnels = cls._get_active_tunnels()
        if not active_tunnels:
            return []

        # 2. Kea リース情報を読み込む
        kea_leases_by_pfx = cls._load_kea_leases()

        # 3. IPv6 近隣探索 (NDP) キャッシュを取得
        neigh_cache = cls._load_neigh_cache()

        # 4. IPv6 ルーティングテーブルを取得
        v6_routes, v6_routes_all = cls._load_v6_routes()

        # 5. 各トンネル経路ごとにクライアント情報を合成
        clients = []
        for tunnel in active_tunnels:
            client_dict = cls._enrich_tunnel(
                tunnel, subnet_map, tunnel_events, kea_leases_by_pfx, neigh_cache, v6_routes, v6_routes_all,
                static_notes_mac, static_notes_ip
            )
            clients.append(client_dict)

        # 6. 同じ MAC アドレスを持つ重複エントリを検出し、古いものをパージ（不要なカーネルルートも削除）
        clients = cls._purge_duplicate_macs(clients)

        # 7. 並行 Ping 実行
        return cls._ping_enrich(clients)

    @classmethod
    def _purge_duplicate_macs(cls, clients):
        """
        同じ MAC アドレスを持つエントリが複数存在する場合、
        最新のプロビジョニング・接続時刻を持つエントリを残し、古いエントリをリストからパージする。
        パージされた古いエントリの不要なトンネルルート (mpe-common) もカーネルから削除する。
        """
        if not clients:
            return []

        def get_sort_key(c):
            # 優先度: last_provisioned > tunnel_created > connect_start > raw_lease_start
            for key in ("last_provisioned", "tunnel_created", "connect_start", "raw_lease_start"):
                val = c.get(key)
                if val and val != "-":
                    try:
                        clean_v = str(val).split("+")[0].split(".")[0].replace("T", " ").strip()
                        dt = datetime.datetime.strptime(clean_v, "%Y-%m-%d %H:%M:%S")
                        return dt.timestamp()
                    except Exception:
                        pass
            return 0.0

        grouped = {}
        no_mac_clients = []

        for c in clients:
            hw = (c.get("hwaddr") or "").strip().lower()
            if not hw or hw == "-":
                no_mac_clients.append(c)
            else:
                grouped.setdefault(hw, []).append(c)

        deduped_clients = []
        for hw, c_list in grouped.items():
            if len(c_list) == 1:
                deduped_clients.append(c_list[0])
            else:
                # タイムスタンプが新しい順にソート (降順)
                c_list.sort(key=lambda x: (get_sort_key(x), 1 if x.get("ndp_ok") else 0), reverse=True)
                newest = c_list[0]
                deduped_clients.append(newest)

                # 古いエントリをパージし、カーネルの残留トンネルルートも削除
                for older in c_list[1:]:
                    old_v4 = older.get("ipv4")
                    if old_v4 and old_v4 != newest.get("ipv4"):
                        print(f"[MAPE-PURGE] Purging stale tunnel route for duplicate MAC {hw}: {old_v4} (keeping {newest.get('ipv4')})", file=sys.stderr)
                        try:
                            dst = old_v4 if "/" in old_v4 else f"{old_v4}/32"
                            run_cmd(["ip", "route", "del", dst, "dev", "mpe-common"], privileged=True)
                            if "/" not in old_v4:
                                run_cmd(["ip", "route", "del", old_v4, "dev", "mpe-common"], privileged=True)
                        except Exception as e:
                            print(f"[MAPE-PURGE] Failed to del route {old_v4}: {e}", file=sys.stderr)

                    old_iface = older.get("interface")
                    if old_iface and old_iface != "-" and old_iface != newest.get("interface"):
                        try:
                            res_n, out_n, _ = run_cmd(["ip", "-6", "neigh", "show", "dev", old_iface], privileged=True)
                            if res_n == 0:
                                for n_line in out_n.splitlines():
                                    if hw in n_line.lower():
                                        n_ip = n_line.split()[0]
                                        run_cmd(["ip", "-6", "neigh", "del", n_ip, "dev", old_iface], privileged=True)
                                        print(f"[MAPE-PURGE] Purged stale neigh {n_ip} on {old_iface} for duplicate MAC {hw}", file=sys.stderr)
                        except Exception as e:
                            print(f"[MAPE-PURGE] Failed to del stale neigh on {old_iface}: {e}", file=sys.stderr)

        return deduped_clients + no_mac_clients

    @staticmethod
    def _get_active_tunnels():
        ret, out, _ = run_cmd(["ip", "route", "show", "dev", "mpe-common"])
        if ret != 0 or not out.strip():
            ret, out, _ = run_cmd(["ip", "route", "show"])

        tunnels = []
        if ret == 0:
            for line in out.splitlines():
                m = re.search(r"^([\d\.]+)(?:/(\d+))?\s+encap ip6.*?dst\s+([0-9a-fA-F:]+)", line)
                if m:
                    v4 = m.group(1)
                    plen = m.group(2) or "32"
                    ce_ip6 = m.group(3)
                    m_dev = re.search(r"\bdev\s+([^\s]+)", line)
                    dev = m_dev.group(1) if m_dev else "mpe-common"
                    display_v4 = f"{v4}/{plen}" if plen != "32" else v4
                    tunnels.append({"ipv4": display_v4, "raw_ipv4": v4, "prefixlen": plen, "ce_ipv6": ce_ip6, "dev": dev})
        return tunnels

    @staticmethod
    def _load_kea_leases():
        leases = {}
        if os.path.exists(KEA_LEASE_CSV):
            try:
                with open(KEA_LEASE_CSV, "r", encoding="utf-8", errors="ignore") as f:
                    lines = [l.strip() for l in f.readlines() if l.strip()]
                    if len(lines) > 1:
                        headers = lines[0].split(",")
                        for line in lines[1:]:
                            cols = line.split(",")
                            if len(cols) == len(headers):
                                d = dict(zip(headers, cols))
                                addr = d.get("address", "").strip()
                                plen = d.get("prefix_len", "56").strip()
                                if addr:
                                    try:
                                        net = ipaddress.IPv6Network(f"{addr}/{plen}", strict=False)
                                        exp = int(d.get("expire", 0))
                                        vlft = int(d.get("valid_lifetime", 7200))
                                        st = exp - vlft if exp > 0 else 0
                                        if net not in leases:
                                            d["first_seen_st"] = st
                                            leases[net] = d
                                        else:
                                            # 最も古い開始時刻（最初に接続した日時）を維持
                                            cur_first = leases[net].get("first_seen_st", st)
                                            if st > 0 and (cur_first == 0 or st < cur_first):
                                                leases[net]["first_seen_st"] = st
                                            # 最新の expire を持つ行でデータを更新するが、first_seen_st は引き継ぐ
                                            if exp > int(leases[net].get("expire", 0)):
                                                saved_first = leases[net]["first_seen_st"]
                                                d["first_seen_st"] = saved_first
                                                leases[net] = d
                                    except Exception:
                                        pass
            except Exception as e:
                print(f"[MAPE] Error reading {KEA_LEASE_CSV}: {e}", file=sys.stderr)
        return leases

    @staticmethod
    def _load_neigh_cache():
        neigh_cache = {}
        ret_n, out_n, _ = run_cmd(["ip", "-6", "neigh", "show"])
        if ret_n == 0:
            for line in out_n.splitlines():
                parts = line.split()
                if len(parts) >= 4:
                    ip_addr = parts[0]
                    dev_val = parts[parts.index("dev") + 1] if "dev" in parts else ""
                    mac_val = parts[parts.index("lladdr") + 1] if "lladdr" in parts else ""
                    state = parts[-1]
                    is_router = "router" in parts
                    neigh_cache[(ip_addr, dev_val)] = {
                        "ip": ip_addr,
                        "dev": dev_val,
                        "mac": mac_val,
                        "state": state,
                        "is_router": is_router,
                    }
        return neigh_cache

    @staticmethod
    def _load_v6_routes():
        v6_routes = []
        v6_routes_all = []
        ret_v6, out_v6, _ = run_cmd(["ip", "-6", "route", "show"])
        if ret_v6 == 0:
            for line in out_v6.splitlines():
                m_via = re.search(r"^([0-9a-fA-F:]+/\d+)\s+via\s+([0-9a-fA-F:]+)\s+dev\s+([^\s]+)", line)
                if m_via:
                    pfx_str, gw_str, dev_str = m_via.groups()
                    try:
                        pfx_net = ipaddress.IPv6Network(pfx_str, strict=False)
                        entry = {"network": pfx_net, "gateway": gw_str, "dev": dev_str}
                        v6_routes.append(entry)
                        v6_routes_all.append(entry)
                    except Exception:
                        pass
                else:
                    m_kern = re.search(r"^([0-9a-fA-F:]+/\d+)\s+dev\s+([^\s]+)", line)
                    if m_kern:
                        pfx_str, dev_str = m_kern.groups()
                        try:
                            pfx_net = ipaddress.IPv6Network(pfx_str, strict=False)
                            v6_routes_all.append({"network": pfx_net, "gateway": None, "dev": dev_str})
                        except Exception:
                            pass
        return v6_routes, v6_routes_all

    @classmethod
    def _enrich_tunnel(cls, tunnel, subnet_map, tunnel_events, kea_leases_by_pfx, neigh_cache, v6_routes, v6_routes_all,
                       static_notes_mac=None, static_notes_ip=None):
        v4 = tunnel["ipv4"]
        ce_ip6 = tunnel["ce_ipv6"]

        matched_v6_route = None
        try:
            ce_ip6_obj = ipaddress.IPv6Address(ce_ip6)
            for r in v6_routes:
                if ce_ip6_obj in r["network"]:
                    matched_v6_route = r
                    break
            if not matched_v6_route:
                for r in v6_routes_all:
                    if ce_ip6_obj in r["network"]:
                        matched_v6_route = r
                        break
        except Exception:
            pass

        if matched_v6_route:
            prefix_str = str(matched_v6_route["network"])
            iface_val = matched_v6_route["dev"]
            gw_ip6 = matched_v6_route["gateway"] or "-"
            # SLAAC オンリンクルート: gateway が None の場合、同一 dev の NDP ルーターエントリから補完
            if gw_ip6 == "-" and iface_val and iface_val != "-":
                for (nip, ndev), ninfo in neigh_cache.items():
                    if ndev == iface_val and ninfo.get("is_router") and nip.startswith("fe80:"):
                        gw_ip6 = nip
                        break
        else:
            try:
                p_net = ipaddress.IPv6Network(f"{ce_ip6}/64", strict=False)
                prefix_str = str(p_net)
            except Exception:
                prefix_str = f"{ce_ip6}/64"
            iface_val = "-"
            gw_ip6 = "-"

        matched_lease = None
        try:
            p_net_obj = ipaddress.IPv6Network(prefix_str, strict=False)
            matched_lease = kea_leases_by_pfx.get(p_net_obj)
        except Exception:
            pass

        duid = "-"
        hwaddr = "-"
        initial_lease_start_dt = "-"
        lease_start_dt = "-"
        lease_expire_dt = "-"
        valid_lft_str = "-"
        vlan_val = "-"

        if matched_lease:
            duid = matched_lease.get("duid", "-")
            hwaddr = matched_lease.get("hwaddr", "-")
            exp = int(matched_lease.get("expire", 0))
            vlft = int(matched_lease.get("valid_lifetime", 7200))
            first_st = int(matched_lease.get("first_seen_st", 0))
            if first_st > 0:
                initial_lease_start_dt = datetime.datetime.fromtimestamp(first_st).strftime("%Y-%m-%d %H:%M:%S")
            if exp > 0:
                st = exp - vlft
                lease_start_dt = datetime.datetime.fromtimestamp(st).strftime("%Y-%m-%d %H:%M:%S")
                if initial_lease_start_dt == "-":
                    initial_lease_start_dt = lease_start_dt
                lease_expire_dt = datetime.datetime.fromtimestamp(exp).strftime("%Y-%m-%d %H:%M:%S")
                valid_lft_str = f"{vlft}s"
            sid = str(matched_lease.get("subnet_id", ""))
            sub_info = subnet_map.get(sid, {})
            vlan_val = sub_info.get("vlan", "-")
            if sub_info.get("interface"):
                iface_val = sub_info.get("interface")

        if vlan_val == "-" and "." in iface_val:
            vlan_val = iface_val.split(".")[-1]

        # --- 段階的 MAC / VLAN 補完 (4-tier fallback) ---
        # 1. CE IPv6 直近エントリ
        if hwaddr == "-" or iface_val == "-" or vlan_val == "-":
            for (nip, ndev), ninfo in neigh_cache.items():
                if nip == ce_ip6 and ninfo.get("mac"):
                    if hwaddr == "-":
                        hwaddr = ninfo["mac"]
                    if iface_val == "-" and ndev:
                        iface_val = ndev
                    break

        # 2. IPv6 ルートテーブルの dev (SLAAC /64 kernel route)
        if iface_val == "-":
            for r in v6_routes_all:
                try:
                    ce_addr = ipaddress.IPv6Address(ce_ip6)
                    if ce_addr in r["network"]:
                        iface_val = r["dev"]
                        break
                except Exception:
                    pass

        # 3. 同一 dev 内の Modified EUI-64 neigh から MAC を逆算
        if hwaddr == "-" and iface_val != "-":
            try:
                ce_net = ipaddress.IPv6Network(f"{ce_ip6}/64", strict=False)
            except Exception:
                ce_net = None
            if ce_net:
                for (nip, ndev), ninfo in neigh_cache.items():
                    if ndev != iface_val or not ninfo.get("mac"):
                        continue
                    try:
                        n_addr = ipaddress.IPv6Address(nip)
                        if n_addr not in ce_net:
                            continue
                        exp_n = n_addr.exploded.split(":")
                        iid_n = "".join(exp_n[4:8])
                        if iid_n[6:10] == "fffe":
                            hwaddr = ninfo["mac"]
                            break
                    except Exception:
                        pass

        # 4. gateway_ipv6 で neigh_cache 検索
        if hwaddr == "-" and gw_ip6 != "-":
            for (nip, ndev), ninfo in neigh_cache.items():
                if nip == gw_ip6 and ninfo.get("mac"):
                    hwaddr = ninfo["mac"]
                    break

        if vlan_val == "-" and iface_val and "." in iface_val:
            vlan_val = iface_val.split(".")[-1]

        hostname = f"ce-{v4}.map.ocn.ad.jp"
        # ddns.conf の登録ホスト名があれば優先連動
        try:
            ddns_map = ConfigManager._parse_ddns_conf()
            ddns_ent = None
            if hwaddr and hwaddr != "-":
                mac_norm = hwaddr.lower().replace("-", ":")
                if ":" not in mac_norm and len(mac_norm) == 12:
                    mac_norm = ":".join(mac_norm[i:i+2] for i in range(0, 12, 2))
                ddns_ent = ddns_map.get(mac_norm)
            if not ddns_ent and vlan_val and vlan_val != "-":
                ddns_ent = ddns_map.get(str(vlan_val))
            if ddns_ent:
                if ddns_ent.get("v4"):
                    hostname = ddns_ent["v4"]
                elif ddns_ent.get("v6"):
                    hostname = ddns_ent["v6"]
        except Exception:
            pass

        # IPv6 近隣探索 (NDP) 判定: Firewall等でPingが遮断されていてもNDPが健全なら接続中
        ndp_ok, ndp_state, ndp_mac = check_ndp_status(ce_ip6, gw_ip6, iface_val, neigh_cache, hwaddr=hwaddr)
        if hwaddr == "-" and ndp_mac:
            hwaddr = ndp_mac

        mode_display = classify_mape_mode(prefix_str, vlan_val, ce_ip6)

        psid_val = 0
        try:
            exp_v6 = ipaddress.IPv6Address(ce_ip6).exploded.split(":")
            last_hextet = exp_v6[7]
            if last_hextet != "0000":
                psid_val = int(last_hextet[:2], 16)
        except Exception:
            psid_val = 0

        tev = tunnel_events.get(v4) or tunnel_events.get(f"{v4}/32") or tunnel_events.get(v4.split('/')[0]) or {}
        tunnel_created = normalize_datetime(tev.get("tunnel_created", "-"))
        last_provisioned = normalize_datetime(tev.get("last_provisioned", "-"))
        # 接続開始時刻 = 初回接続日時 (初期リース開始時刻 or トンネル作成時刻の最も古い方)
        # リース更新(Renew)があっても初期接続日時を維持する
        start_candidates = []
        if initial_lease_start_dt != "-":
            start_candidates.append(normalize_datetime(initial_lease_start_dt))
        if tunnel_created != "-":
            start_candidates.append(tunnel_created)

        if start_candidates:
            connect_start = min(start_candidates)
        elif lease_start_dt != "-":
            connect_start = normalize_datetime(lease_start_dt)
        else:
            connect_start = "-"

        if tunnel_created == "-" and connect_start != "-":
            tunnel_created = connect_start

        # 備考の取得 (MACまたはIPからマッチング)
        client_note = ""
        if static_notes_mac:
            mac_key = hwaddr.strip().lower()
            if mac_key != "-" and mac_key in static_notes_mac:
                client_note = static_notes_mac[mac_key]
        if not client_note and static_notes_ip and v4 != "-" and v4 in static_notes_ip:
            client_note = static_notes_ip[v4]

        return {
            "prefix": prefix_str,
            "duid": duid,
            "hwaddr": hwaddr,
            "vlan": vlan_val,
            "interface": iface_val,
            "gateway_ipv6": gw_ip6,
            "ipv4": v4,
            "psid": psid_val,
            "ce_ipv6": ce_ip6,
            "hostname": hostname,
            "note": client_note,
            "connect_start": connect_start,
            "lease_start": connect_start,
            "initial_lease_start": normalize_datetime(initial_lease_start_dt),
            "raw_lease_start": normalize_datetime(lease_start_dt),
            "last_renewed": normalize_datetime(lease_start_dt),
            "lease_expire": normalize_datetime(lease_expire_dt),
            "valid_lifetime": valid_lft_str,
            "mode_display": mode_display,
            "tunnel_created": tunnel_created,
            "last_provisioned": last_provisioned,
            "ndp_ok": ndp_ok,
            "ndp_state": ndp_state,
            "is_online": ndp_ok,
            "status": "active" if ndp_ok else "unreachable"
        }

    @staticmethod
    def _ping_enrich(clients):
        for c in clients:
            ce_ip6 = c.get("ce_ipv6")
            ok, rtt, target = get_cached_ping(ce_ip6)
            c["ping_target"] = target
            c["ping_ok"] = ok
            c["ping_rtt"] = rtt
            # MAP-E トンネルの開通状態はトンネル IPv4 への Ping 到達性を基準にする
            c["is_online"] = ok
            c["status"] = "active" if ok else "unreachable"
        return clients


# =============================================================================
