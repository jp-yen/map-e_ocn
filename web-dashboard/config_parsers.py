# web-dashboard/config_parsers.py
"""
設定ファイル解析モジュール。
map-e.conf, pppoe.conf, map-e-static-ip.conf, ddns.conf をパースして辞書やリストに変換する。
"""

import os
import re
from typing import Dict, List, Any


def parse_env_file(path: str) -> Dict[str, str]:
    """Shell スタイルの KEY=VALUE 設定ファイルを解析し、文字列の辞書を返す"""
    vars_dict: Dict[str, str] = {}
    if not os.path.exists(path):
        return vars_dict
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^([A-Za-z0-9_]+)=([\"']?)(.*?)\2$", line)
            if m:
                vars_dict[m.group(1)] = m.group(3)
    return vars_dict


def parse_pppoe_users(pppoe_conf_path: str) -> List[Dict[str, Any]]:
    """pppoe.conf を読み込み、ユーザー情報のリストを返す"""
    users: List[Dict[str, Any]] = []
    if not os.path.exists(pppoe_conf_path):
        return users
    with open(pppoe_conf_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 4)
            if len(parts) >= 3:
                u = parts[0].strip()
                pw = parts[1].strip()
                ip = parts[2].strip()
                srv = parts[3].strip() if len(parts) >= 4 else "*"
                note = parts[4].strip() if len(parts) >= 5 else ""
                users.append({
                    "username": u,
                    "password": pw,
                    "ip": ip,
                    "service_name": srv,
                    "ac_name": srv,
                    "note": note
                })
    return users


def parse_static_ips(static_ip_conf_path: str) -> List[Dict[str, str]]:
    """map-e-static-ip.conf を読み込み、固定IP対応エントリのリストを返す"""
    entries: List[Dict[str, str]] = []
    if not os.path.exists(static_ip_conf_path):
        return entries
    with open(static_ip_conf_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [x.strip() for x in line.split(",")]
            if len(parts) >= 2:
                raw_ip = parts[1].strip()
                ip_val = "" if raw_ip == "-" else raw_ip
                entry = {"mac": parts[0].strip(), "ip": ip_val}
                entry["note"] = parts[2].strip() if len(parts) >= 3 else ""
                entries.append(entry)
    return entries


def parse_ddns_conf(ddns_conf_path: str) -> Dict[str, Dict[str, str]]:
    """
    ddns.conf を読み込み、識別キー (小文字MAC / 小文字ユーザー名) を
    キーとする辞書 {key: {"v6": v6_fqdn, "v4": v4_fqdn}} を返す。
    """
    ddns_map: Dict[str, Dict[str, str]] = {}
    if not os.path.exists(ddns_conf_path):
        return ddns_map
    with open(ddns_conf_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [x.strip() for x in line.split(",")]
            if len(parts) >= 2:
                raw_key = parts[0].strip()
                v6 = parts[1].strip() if len(parts) >= 2 else ""
                v4 = parts[2].strip() if len(parts) >= 3 else ""
                v6_val = "" if v6 == "-" else v6
                v4_val = "" if v4 == "-" else v4

                clean_key = raw_key.lower().replace("-", ":")
                if ":" in clean_key or len(clean_key) == 12:
                    if ":" not in clean_key:
                        clean_key = ":".join(clean_key[i:i+2] for i in range(0, 12, 2))
                    ddns_map[clean_key] = {"v6": v6_val, "v4": v4_val}
                else:
                    ddns_map[raw_key.lower()] = {"v6": v6_val, "v4": v4_val}
    return ddns_map
