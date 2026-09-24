# web-dashboard/utils.py
"""
Section 2: System & Network Utilities
  - ファイル読み込みヘルパー群 (load_pppoe_conf_notes 等)
  - classify_mape_mode : MAP-E 接続方式の判定
  - run_cmd            : サブプロセス安全実行
  - format_duration / normalize_datetime : データ整形
  - get_fping_path / ping_target : 死活監視
  - KeaClient          : Kea DHCPv6 制御ソケット通信
"""

import os
import sys
import re
import json
import time
import socket
import datetime
import ipaddress
import subprocess
from concurrent.futures import ThreadPoolExecutor

from constants import (
    WORKSPACE_DIR, KEA_LEASE_CSV, KEA_CTRL_SOCK, TUNNEL_EVENT_LOG,
)


def _get_app_config():
    """循環 import を避けるための遅延 APP_CONFIG 取得ヘルパー"""
    from config import APP_CONFIG  # noqa: PLC0415
    return APP_CONFIG



def load_pppoe_conf_notes():
    """
    pppoe.conf を解析し、ユーザー名ごとの設定情報（サービス名・備考・割当IP）を辞書で返す。
    形式: Username Password Client_IP_or_Subnet [Service_Name] [Note...]
    """
    notes = {}

    # pppoe.conf の候補パスを優先順位順に試す
    conf_candidates = []
    for candidate_dir in [WORKSPACE_DIR, "/app/workspace", "/app", "."]:
        if candidate_dir and os.path.exists(candidate_dir):
            conf_candidates.append(os.path.join(candidate_dir, "pppoe.conf"))

    conf_path = None
    for c in conf_candidates:
        if os.path.exists(c):
            conf_path = c
            break

    if conf_path:
        try:
            with open(conf_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split(None, 4)
                    if len(parts) >= 3:
                        uname = parts[0].strip()
                        subnet = parts[2].strip()
                        srv_name = parts[3].strip() if len(parts) >= 4 else "*"
                        note_text = parts[4].strip() if len(parts) >= 5 else ""
                        notes[uname] = {
                            "service_name": srv_name,
                            "ac_name": srv_name,
                            "note": note_text,
                            "configured_ip": subnet
                        }
        except Exception as e:
            print(f"[Config] Error reading {conf_path}: {e}", file=sys.stderr)

    return notes



def load_static_ip_notes():
    """
    map-e-static-ip.conf を解析し、MACアドレスおよび割当IPごとの備考情報を辞書で返す。
    形式: MAC, IP, [Note...]
    """
    notes_by_mac = {}
    notes_by_ip = {}
    candidates = [
        os.path.join(WORKSPACE_DIR, "map-e-static-ip.conf"),
        "/etc/map-e-static-ip.conf",
        "/app/workspace/map-e-static-ip.conf",
        "./map-e-static-ip.conf"
    ]
    target_path = None
    for c in candidates:
        if os.path.exists(c):
            target_path = c
            break

    if target_path:
        try:
            with open(target_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = [x.strip() for x in line.split(",")]
                    if len(parts) >= 2:
                        mac = parts[0].lower()
                        ip_raw = parts[1]
                        ip_clean = ip_raw.split("/")[0] if "/" in ip_raw else ip_raw
                        note = parts[2] if len(parts) >= 3 else ""
                        if note:
                            notes_by_mac[mac] = note
                            if ip_clean and ip_clean != "-":
                                notes_by_ip[ip_clean] = note
        except Exception as e:
            print(f"[Config] Error reading {target_path}: {e}", file=sys.stderr)

    return notes_by_mac, notes_by_ip


def load_kea_subnet_vlan_map():
    """Kea の subnet_id を VLAN 番号およびインターフェース名に対応付ける"""
    subnets = {}
    candidates = [
        os.path.join(WORKSPACE_DIR, "kea-dhcp6", "kea-dhcp6.conf"),
        os.path.join("/app", "workspace", "kea-dhcp6", "kea-dhcp6.conf"),
        "/app/kea-dhcp6.conf"
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
                pattern = r"\{\s*\"id\":\s*(\d+).*?\"interface\":\s*\"([^\"]+)\""
                for m in re.finditer(pattern, text, re.DOTALL):
                    sid = m.group(1)
                    iface = m.group(2)
                    vm = re.search(r"\.(\d+)$", iface)
                    vlan = vm.group(1) if vm else iface
                    subnets[sid] = {
                        "interface": iface,
                        "vlan": vlan
                    }
                if subnets:
                    break
            except Exception as e:
                print(f"[Config] Error loading {p}: {e}", file=sys.stderr)
    return subnets


def load_tunnel_events():
    """
    /var/log/mape-tunnel-events.log を解析し、IPv4 をキーとする
    トンネル作成・プロビジョニング時刻の辞書を返す。
    フォーマット: EVENT_TYPE \t TIMESTAMP \t IPv4 \t CE_IPv6
    """
    events = {}
    event_lines = []
    if os.path.exists(TUNNEL_EVENT_LOG):
        try:
            with open(TUNNEL_EVENT_LOG, "r", encoding="utf-8", errors="ignore") as f:
                event_lines = f.readlines()
        except OSError:
            event_lines = []
    if not event_lines:
        # Compose 更新前のイベントは provisioning コンテナ内に残っているため、
        # ホスト側の共有ログが空の場合だけ読み出す。
        ret, out, _ = run_cmd([
            "docker", "exec", "mape-provisioning-server",
            "cat", "/var/log/mape-tunnel-events.log"
        ])
        if ret == 0:
            event_lines = out.splitlines()
    try:
        for line in event_lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            evt_type, evt_time, evt_ipv4 = parts[0], parts[1], parts[2]
            norm_time = normalize_datetime(evt_time)
            # /32 あり・なしの両方のキーで登録して検索漏れを防ぐ
            keys = [evt_ipv4]
            bare_ip = evt_ipv4.split("/")[0]
            if bare_ip != evt_ipv4:
                keys.append(bare_ip)
            else:
                keys.append(f"{evt_ipv4}/32")

            for k in keys:
                if k not in events:
                    events[k] = {
                        "tunnel_created": norm_time,
                        "last_provisioned": norm_time
                    }
                else:
                    events[k]["last_provisioned"] = norm_time
                    if evt_type == "TUNNEL_CREATE":
                        events[k]["tunnel_created"] = norm_time
    except Exception as e:
        print(f"[Events] Error reading {TUNNEL_EVENT_LOG}: {e}", file=sys.stderr)
    return events


def classify_mape_mode(prefix_str, vlan_str, ce_ip6, config=None):
    """
    MAP-E の接続方式 (SLAAC動的/固定, DHCP-PD動的/固定) を判定して返す。
    返り値: 'SLAAC (動的)', 'SLAAC (固定IP)', 'DHCP-PD (動的)', 'DHCP-PD (固定IP)'
    """
    cfg = config if config is not None else _get_app_config()

    vlan = None
    if vlan_str and vlan_str != "-":
        m = re.search(r"(\d+)", str(vlan_str))
        if m:
            try:
                vlan = int(m.group(1))
            except ValueError:
                pass

    if vlan is not None:
        slaac_dyn = cfg.get_vlan_list("SLAAC_DYN_VLANS", [60, 61])
        pd_dyn = cfg.get_vlan_list("PD_DYN_VLANS", [62, 63])
        slaac_fix = cfg.get_vlan_list("SLAAC_FIX_VLANS", [64, 65])
        pd_fix = cfg.get_vlan_list("PD_FIX_VLANS", [66, 67])

        if vlan in slaac_dyn:
            return "SLAAC (動的)"
        elif vlan in pd_dyn:
            return "DHCP-PD (動的)"
        elif vlan in slaac_fix:
            return "SLAAC (固定IP)"
        elif vlan in pd_fix:
            return "DHCP-PD (固定IP)"

    # VLAN で判定できない場合はプレフィックスの第3ヘクステットから判定
    target_ip = prefix_str if (prefix_str and prefix_str != "-") else ce_ip6
    if target_ip and target_ip != "-":
        try:
            clean = target_ip.split("/")[0].split("%")[0]
            exp = ipaddress.IPv6Address(clean).exploded.split(":")
            if len(exp) >= 3:
                seg = exp[2]
                if seg.startswith("1"):
                    return "SLAAC (動的)"
                elif seg.startswith("2"):
                    return "DHCP-PD (動的)"
                elif seg.startswith("3"):
                    return "SLAAC (固定IP)"
                elif seg.startswith("4"):
                    return "DHCP-PD (固定IP)"
        except Exception:
            pass

    return "DHCP-PD (動的)"


def run_cmd(args, timeout=5, cwd=None, privileged=False):
    """コマンドを安全に実行し (returncode, stdout, stderr) を返す"""
    try:
        cmd = list(args)
        if privileged and os.geteuid() != 0:
            cmd = ["sudo"] + cmd
        res = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout, cwd=cwd)
        return res.returncode, res.stdout, res.stderr
    except Exception as e:
        return -1, "", str(e)


def format_duration(seconds):
    """秒数を読みやすい経過時間表記 (例: 1h 23m 45s) に変換する"""
    if seconds < 0:
        return "0s"
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    if hours > 0:
        return f"{hours}h {minutes}m"
    if minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12
}


def normalize_datetime(val):
    """
    様々な形式の日時表現（Syslog形式 'Sep 4 20:27:30'、ISO8601形式 '2026-09-04T21:41:35+09:00'、
    UNIXエポック秒、等）を標準的な 'YYYY-MM-DD HH:MM:SS' 形式に統一する。
    """
    if not val or val == "-":
        return "-"
    if isinstance(val, (int, float)):
        try:
            return datetime.datetime.fromtimestamp(val).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(val)

    val = str(val).strip()
    if not val or val == "-":
        return "-"

    # 1. 既に "YYYY-MM-DD HH:MM:SS" の場合
    if re.match(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}$", val):
        return val

    # 2. ISO8601 "2026-09-04T21:41:35+09:00" または "2026-09-04T21:41:35"
    m_iso = re.match(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})", val)
    if m_iso:
        return f"{m_iso.group(1)} {m_iso.group(2)}"

    # 3. Syslog形式 "Sep  4 20:27:30" または "Sep 4 20:27:30"
    m_syslog = re.match(r"^([A-Za-z]{3})\s+(\d{1,2})\s+(\d{2}:\d{2}:\d{2})", val)
    if m_syslog:
        mon_str, day_str, time_str = m_syslog.groups()
        mon = _MONTH_MAP.get(mon_str.lower())
        if mon:
            year = datetime.datetime.now().year
            return f"{year:04d}-{mon:02d}-{int(day_str):02d} {time_str}"

    # 4. 数字のみ（エポック秒文字列）
    if re.match(r"^\d{9,10}(?:\.\d+)?$", val):
        try:
            return datetime.datetime.fromtimestamp(float(val)).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass

    return val


_FPING_BIN = None


def get_fping_path():
    """fping バイナリの存在確認とパスキャッシュ"""
    global _FPING_BIN
    if _FPING_BIN is not None:
        return _FPING_BIN
    for p in ["/usr/bin/fping", "/usr/local/bin/fping"]:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            _FPING_BIN = p
            return _FPING_BIN
    ret, out, _ = run_cmd(["which", "fping"], timeout=1.0)
    if ret == 0 and out.strip():
        _FPING_BIN = out.strip()
        return _FPING_BIN
    _FPING_BIN = ""
    return _FPING_BIN


def ping_target(target_ip, timeout=0.5, vrf=None, iface=None):
    """IPv4 または IPv6 アドレスへ単発の高速 ICMP Echo 送信を行い死活を確認する。
    fping が利用可能な場合は優先使用し、L2環境を考慮して
    タイムアウトはデフォルト0.5秒（500ms）に設定。
    VRF デバイスやインターフェースが指定された場合は -I でバインドする。
    """
    if not target_ip or target_ip in ("0.0.0.0", "::", "-"):
        return False, None
    clean_ip = target_ip.split("/")[0].strip()
    is_v6 = ":" in clean_ip
    device = vrf or iface

    fping_bin = get_fping_path()
    if fping_bin:
        # fping: -e (elapsed RTT表示), -r 0 (リトライなし高速判定), -t <ms> (タイムアウトms)
        timeout_ms = int(timeout * 1000)
        cmd = [fping_bin, "-e", "-r", "0", "-t", str(timeout_ms)]
        if device:
            cmd.extend(["-I", str(device)])
        cmd.append(clean_ip)
        ret, out, err = run_cmd(cmd, timeout=timeout + 0.5)
        combined = (out + " " + err).strip()
        if ret == 0 and "is alive" in combined:
            m = re.search(r"\(([\d\.]+)\s*ms\)", combined)
            rtt = f"{m.group(1)} ms" if m else "OK"
            return True, rtt
        return False, None

    # フォールバック: 標準 ping コマンド
    cmd = ["ping"]
    if device:
        cmd.extend(["-I", str(device)])
    cmd.extend(["-6" if is_v6 else "-4", "-c", "1", "-W", str(max(1, int(timeout))), clean_ip])
    ret, out, _ = run_cmd(cmd, timeout=timeout + 0.5)
    if ret == 0:
        m = re.search(r"time=([\d\.]+)\s*ms", out)
        rtt = f"{m.group(1)} ms" if m else "OK"
        return True, rtt
    return False, None


class KeaClient:
    """Kea DHCPv6 UNIX ドメイン制御ソケット経由での制御コマンド送受信クラス"""

    @staticmethod
    def send_cmd(cmd_dict, timeout=3.0):
        sock_path = KEA_CTRL_SOCK
        if not os.path.exists(sock_path):
            return {"result": -1, "text": f"Socket not found: {sock_path}"}
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                s.connect(sock_path)
                s.sendall(json.dumps(cmd_dict).encode("utf-8"))
                resp_bytes = b""
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    resp_bytes += chunk
                    try:
                        return json.loads(resp_bytes.decode("utf-8"))
                    except Exception:
                        continue
                if resp_bytes:
                    return json.loads(resp_bytes.decode("utf-8"))
        except Exception as e:
            return {"result": -1, "text": str(e)}
        return {"result": -1, "text": "No response"}

    @classmethod
    def delete_pd_lease(cls, prefix_addr):
        """指定した IA_PD プレフィックスリースを Kea から削除し、CSV 側も直接クリーンアップする"""
        results = []
        addr = prefix_addr.split("/")[0].strip()

        deleted = False
        last_err = ""
        for ltype in ["IA_PD", "IA_NA"]:
            cmd = {
                "command": "lease6-del",
                "arguments": {
                    "ip-address": addr,
                    "type": ltype
                }
            }
            resp = cls.send_cmd(cmd)
            res_code = resp.get("result", -1)
            res_text = resp.get("text", "")
            if res_code == 0:
                results.append(f"Kea lease6-del ({ltype}): success")
                deleted = True
                break
            elif ltype == "IA_PD":
                last_err = f"result {res_code} ({res_text})"

        if not deleted:
            results.append(f"Kea lease6-del: {last_err}")

        # CSV も即時更新 (書き込み可能な場合)
        if os.path.exists(KEA_LEASE_CSV) and os.access(KEA_LEASE_CSV, os.W_OK):
            try:
                with open(KEA_LEASE_CSV, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                if lines:
                    header = lines[0]
                    new_lines = [header] + [l for l in lines[1:] if not (l.strip().split(",") and l.strip().split(",")[0].strip() == addr)]
                    with open(KEA_LEASE_CSV, "w", encoding="utf-8") as f:
                        f.writelines(new_lines)
                    results.append(f"Cleaned CSV for {addr}")
            except Exception as e:
                results.append(f"CSV cleanup err: {e}")

        return results


# =============================================================================
# Section 3: Log Parsing Helpers
#   handlers.py から抽出したログ解析ユーティリティ群。
#   DashboardHandler は下記の関数を直接呼び出す。
# =============================================================================

def parse_line_datetime(line):
    """ログ行から日時（エポック秒, YYYY-MM-DD HH:MM:SS, 本文）を抽出する"""
    cur_year = datetime.datetime.now().year
    month_map = _MONTH_MAP

    # 1. Docker --timestamps (UTC ISO 形式): 2026-09-04T14:24:25.789342740Z <rest>
    m_docker = re.match(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)Z\s+(.*)$', line)
    if m_docker:
        utc_str, rest = m_docker.groups()
        try:
            dt_utc = datetime.datetime.fromisoformat(utc_str.split('.')[0])
            dt_jst = dt_utc + datetime.timedelta(hours=9)
            ts = dt_jst.timestamp()
            m_inner = re.match(r'^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+(.*)$', rest)
            if m_inner:
                inner_dt_str, inner_rest = m_inner.groups()
                dt_inner = datetime.datetime.fromisoformat(inner_dt_str.split('.')[0])
                return dt_inner.timestamp(), dt_inner.strftime("%Y-%m-%d %H:%M:%S"), inner_rest
            return ts, dt_jst.strftime("%Y-%m-%d %H:%M:%S"), rest
        except Exception:
            pass

    # 2. ISO 形式: 2026-09-04T22:44:38+09:00 or 2026-09-04 23:24:13
    m_iso = re.search(r'(\d{4})-(\d{2})-(\d{2})[T\s](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(?:[+-]\d{2}:\d{2})?', line)
    if m_iso:
        y, m, d, hh, mm, ss = map(int, m_iso.groups()[:6])
        try:
            dt = datetime.datetime(y, m, d, hh, mm, ss)
            return dt.timestamp(), dt.strftime("%Y-%m-%d %H:%M:%S"), line
        except Exception:
            pass

    # 3. Syslog 形式: Sep  4 23:24:04 or Sep 04 23:24:04
    m_syslog = re.search(r'([A-Za-z]{3})\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})', line)
    if m_syslog:
        mon_str, d, hh, mm, ss = m_syslog.groups()
        mon = month_map.get(mon_str.lower())
        if mon:
            try:
                dt = datetime.datetime(cur_year, mon, int(d), int(hh), int(mm), int(ss))
                return dt.timestamp(), dt.strftime("%Y-%m-%d %H:%M:%S"), line
            except Exception:
                pass

    # 4. HTTP / Provisioning server 形式: [04/Sep/2026 23:24:32]
    m_http = re.search(r'\[?(\d{2})/([A-Za-z]{3})/(\d{4})[:\s](\d{2}):(\d{2}):(\d{2})', line)
    if m_http:
        d, mon_str, y, hh, mm, ss = m_http.groups()
        mon = month_map.get(mon_str.lower())
        if mon:
            try:
                dt = datetime.datetime(int(y), mon, int(d), int(hh), int(mm), int(ss))
                return dt.timestamp(), dt.strftime("%Y-%m-%d %H:%M:%S"), line
            except Exception:
                pass

    # 5. BIND 形式: 04-Sep-2026 23:24:00.123
    m_bind = re.search(r'(\d{2})-([A-Za-z]{3})-(\d{4})\s+(\d{2}):(\d{2}):(\d{2})', line)
    if m_bind:
        d, mon_str, y, hh, mm, ss = m_bind.groups()
        mon = month_map.get(mon_str.lower())
        if mon:
            try:
                dt = datetime.datetime(int(y), mon, int(d), int(hh), int(mm), int(ss))
                return dt.timestamp(), dt.strftime("%Y-%m-%d %H:%M:%S"), line
            except Exception:
                pass

    return None, None, line


def detect_log_level(line):
    """ログ行から重要度レベル (DEBUG, INFO, WARN, ERROR) を抽出する"""
    # ただし -DEBUG や +DEBUG などのコンパイルオプション文字列は除外
    m = re.search(r'(?<![-+A-Za-z0-9_])(DEBUG|INFO|NOTICE|WARN|WARNING|ERROR|ERR|FATAL|CRIT|ALERT|EMERG)(?![-+A-Za-z0-9_])', line, re.IGNORECASE)
    if m:
        w = m.group(1).upper()
        if w == "NOTICE":
            return "INFO"
        elif w == "WARNING":
            return "WARN"
        elif w in ("ERR", "FATAL", "CRIT", "ALERT", "EMERG"):
            return "ERROR"
        return w
    return "INFO"


def read_tail_with_rotations(primary_path, max_lines=200):
    """ログファイルとローテーションファイル (.1, .2.gz, ...) を結合して末尾 max_lines 行を返す"""
    import gzip as _gzip
    lines = []
    if os.path.exists(primary_path) and os.path.getsize(primary_path) > 0:
        ret, out, _ = run_cmd(["tail", "-n", str(max_lines), primary_path])
        if ret == 0 and out:
            lines = out.splitlines()

    rot_idx = 1
    while len(lines) < max_lines:
        plain_path = f"{primary_path}.{rot_idx}"
        gz_path = f"{primary_path}.{rot_idx}.gz"
        needed = max_lines - len(lines)
        found = False

        if os.path.exists(plain_path) and os.path.getsize(plain_path) > 0:
            ret, out, _ = run_cmd(["tail", "-n", str(needed), plain_path])
            if ret == 0 and out:
                lines = out.splitlines() + lines
            found = True
        elif os.path.exists(gz_path) and os.path.getsize(gz_path) > 0:
            try:
                with _gzip.open(gz_path, "rt", encoding="utf-8", errors="replace") as gz:
                    gz_lines = gz.readlines()
                    lines = [l.rstrip("\r\n") for l in gz_lines[-needed:]] + lines
                found = True
            except Exception:
                pass

        if not found:
            break
        rot_idx += 1

    return lines[-max_lines:]


def fetch_logs_by_type(log_type, max_lines=200, level_filter="INFO"):
    """
    ログ種別 (pppoe / mape / kea / bind / radvd / chrony / messages) に応じた
    ログ行リストとソース説明文字列を返す。
    """
    from constants import LOG_DIR as _LOG_DIR  # noqa: PLC0415

    lines = []
    source_desc = ""

    if log_type == "pppoe":
        p = os.path.join(_LOG_DIR, "pppoe-server.log")
        file_lines = read_tail_with_rotations(p, max_lines)
        if file_lines:
            source_desc = f"ファイル: {p}"
            lines = file_lines
        else:
            ret, out, _ = run_cmd(["docker", "ps", "--format", "{{.Names}}"])
            cnames = [n.strip() for n in out.splitlines() if n.strip().startswith("pppoe-server-")]
            if cnames:
                all_p_lines = []
                for cname in cnames:
                    ret_l, out_l, err_l = run_cmd(["docker", "logs", "--timestamps", "--tail", str(max_lines), cname])
                    c_out = (out_l + "\n" + err_l).strip()
                    if c_out:
                        all_p_lines.extend(c_out.splitlines())
                source_desc = f"コンテナ: {', '.join(cnames)}"
                lines = all_p_lines[-max_lines:]
            else:
                source_desc = "pppoe-server (コンテナ未起動)"
                lines = []

    elif log_type == "mape":
        p_tun = os.path.join(_LOG_DIR, "mape-tunnel-events.log")
        p_prov = os.path.join(_LOG_DIR, "mape-provisioning-server.log")
        accum = []
        tun_lines = read_tail_with_rotations(p_tun, max_lines)
        if tun_lines:
            accum.extend(tun_lines)
        prov_lines = read_tail_with_rotations(p_prov, max_lines)
        if prov_lines:
            accum.extend(prov_lines)
        ret, out, err = run_cmd(["docker", "logs", "--timestamps", "--tail", str(max_lines), "mape-provisioning-server"])
        d_lines = (out + "\n" + err).strip().splitlines()
        accum.extend([l for l in d_lines if l.strip()])
        source_desc = "MAP-E トンネル & プロビジョニングログ"
        lines = accum[-max_lines:] if accum else []

    elif log_type == "kea":
        lvl = level_filter.upper()
        if lvl == "DEBUG":
            p = os.path.join(_LOG_DIR, "kea", "kea-dhcp6-debug.log")
            if not os.path.exists(p) or os.path.getsize(p) == 0:
                p = os.path.join(_LOG_DIR, "kea", "kea-dhcp6.log")
        elif lvl in ("WARN", "ERROR"):
            p = os.path.join(_LOG_DIR, "kea", "kea-dhcp6-warn.log")
            if not os.path.exists(p) or os.path.getsize(p) == 0:
                p = os.path.join(_LOG_DIR, "kea", "kea-dhcp6.log")
        else:
            p = os.path.join(_LOG_DIR, "kea", "kea-dhcp6.log")
        file_lines = read_tail_with_rotations(p, max_lines)
        if file_lines:
            source_desc = f"ファイル: {p}"
            lines = file_lines
        else:
            ret, out, err = run_cmd(["docker", "logs", "--timestamps", "--tail", str(max_lines), "kea-dhcp6"])
            d_lines = (out + "\n" + err).strip().splitlines()
            if d_lines:
                source_desc = "コンテナ: kea-dhcp6 (STDOUT)"
                lines = d_lines[-max_lines:]
            else:
                source_desc = "kea-dhcp6"
                lines = []

    elif log_type == "bind":
        p = os.path.join(_LOG_DIR, "bind.log")
        file_lines = read_tail_with_rotations(p, max_lines)
        if file_lines:
            source_desc = f"ファイル: {p}"
            lines = file_lines
        else:
            source_desc = "コンテナ: bind9"
            ret, out, err = run_cmd(["docker", "logs", "--timestamps", "--tail", str(max_lines), "bind9"])
            combined = (out + "\n" + err).strip()
            lines = combined.splitlines() if combined else []

    elif log_type == "radvd":
        p = os.path.join(_LOG_DIR, "radvd.log")
        lines = read_tail_with_rotations(p, max_lines)
        source_desc = f"ファイル: {p}"

    elif log_type == "chrony":
        p = os.path.join(_LOG_DIR, "chrony.log")
        lines = read_tail_with_rotations(p, max_lines)
        source_desc = f"ファイル: {p}"

    elif log_type == "messages":
        if level_filter.upper() in ("WARN", "ERROR"):
            p = os.path.join(_LOG_DIR, "errors.log")
            if not os.path.exists(p) or os.path.getsize(p) == 0:
                p = os.path.join(_LOG_DIR, "messages")
        else:
            p = os.path.join(_LOG_DIR, "messages")
        file_lines = read_tail_with_rotations(p, max_lines * 5)
        # ダッシュボード自身のアクセスログおよび個別サービスログを messages から除外
        exclude_pattern = re.compile(r'\b(pppd|pppoe-server|kea-dhcp6|kea-map-e-hook|named|bind9|radvd|chrony|chronyd|web-dashboard)\b')
        filtered = [l for l in file_lines if not exclude_pattern.search(l)]
        lines = filtered[-max_lines:]
        source_desc = f"ファイル: {p}"

    elif log_type == "dashboard":
        p = os.path.join(_LOG_DIR, "web-dashboard-access.log")
        lines = read_tail_with_rotations(p, max_lines)
        source_desc = f"ファイル: {p}"

    return lines, source_desc


# =============================================================================
# Section 4: Container Status Helpers
# =============================================================================

def get_container_statuses():
    """Docker コンテナの稼働状況を取得し、主要サービス別のステータス辞書を返す。"""
    ret, out, _ = run_cmd(["docker", "ps", "-a", "--format", "{{.Names}}\t{{.State}}\t{{.Status}}"], timeout=3)
    raw = {}
    if ret == 0 and out:
        for line in out.splitlines():
            parts = line.strip().split("\t")
            if len(parts) >= 2:
                name = parts[0]
                state = parts[1].lower()
                status = parts[2] if len(parts) >= 3 else state
                level = "ok" if state == "running" else "down"
                if "unhealthy" in status.lower():
                    level = "warn"
                raw[name] = {
                    "name": name,
                    "state": state,
                    "status": status,
                    "level": level
                }

    def _resolve(target_name):
        if target_name in raw:
            return raw[target_name]
        for k, v in raw.items():
            if target_name in k:
                return v
        return {"name": target_name, "state": "not_found", "status": "停止 / 未検出", "level": "down"}

    # 1. radvd
    c_radvd = _resolve("radvd")
    # 2. bind9
    c_bind9 = _resolve("bind9")
    # 3. kea-dhcp6
    c_kea = _resolve("kea-dhcp6")
    # 4. axosyslog
    c_syslog = _resolve("axosyslog")
    # 5. pppoe (単一統合コンテナまたは旧複数コンテナ)
    pppoe_items = []
    if "pppoe-server" in raw:
        pppoe_items.append(raw["pppoe-server"])
    else:
        for k in sorted(raw.keys()):
            if k.startswith("pppoe-server-"):
                pppoe_items.append(raw[k])
    if not pppoe_items:
        pppoe_items = [
            {"name": "pppoe-server", "state": "not_found", "status": "停止 / 未検出", "level": "down"}
        ]
    # 6. chrony
    c_chrony = _resolve("chrony")
    # 7. provisioning-server
    c_prov = _resolve("mape-provisioning-server")

    return {
        "radvd": c_radvd,
        "bind9": c_bind9,
        "kea_dhcp6": c_kea,
        "axosyslog": c_syslog,
        "pppoe": pppoe_items,
        "chrony": c_chrony,
        "provisioning_server": c_prov
    }
