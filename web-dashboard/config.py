# web-dashboard/config.py
"""
設定・環境管理モジュール (AppConfig / ConfigManager)

  - AppConfig     : map-e.conf の読み込み・動的パラメータ提供
  - ConfigManager : 設定ファイルのパース・バリデーション・生成・ステージング・ホスト反映
"""

import datetime
import glob
import ipaddress
import json
import os
import re
import shutil
import sys
import traceback
from typing import Any, Dict, List, Optional

from constants import WORKSPACE_DIR, get_host_workspace_dir
from utils import run_cmd


# ===========================================================================
# 1. 設定ファイル解析ヘルパー
# ===========================================================================

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
                srv = parts[3].strip() if len(parts) >= 4 else "*"
                users.append({
                    "username": parts[0].strip(),
                    "password": parts[1].strip(),
                    "ip": parts[2].strip(),
                    "service_name": srv,
                    "ac_name": srv,
                    "note": parts[4].strip() if len(parts) >= 5 else "",
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
                raw_ip = parts[1]
                entries.append({
                    "mac": parts[0],
                    "ip": "" if raw_ip == "-" else raw_ip,
                    "note": parts[2] if len(parts) >= 3 else "",
                })
    return entries


def parse_ddns_conf(ddns_conf_path: str) -> Dict[str, Dict[str, str]]:
    """ddns.conf を読み込み、{識別キー(VLAN番号 or 小文字ユーザー名): {"v6": ..., "v4": ...}} を返す"""
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
                raw_key = parts[0]
                v6 = parts[1] if len(parts) >= 2 else ""
                v4 = parts[2] if len(parts) >= 3 else ""
                key = str(int(raw_key)) if raw_key.isdigit() else raw_key.lower()
                ddns_map[key] = {
                    "v6": "" if v6 == "-" else v6,
                    "v4": "" if v4 == "-" else v4,
                }
    return ddns_map


# ===========================================================================
# 2. バリデーションヘルパー
# ===========================================================================

def is_valid_ddns_hostname(val: Any) -> bool:
    """DDNS ホスト名が RFC 準拠の FQDN 形式であるか判定する"""
    if not val or not isinstance(val, str):
        return False
    val = val.strip().rstrip(".")
    if len(val) < 3 or len(val) > 253 or any(c in val for c in ["@", "_", " "]):
        return False
    parts = val.split(".")
    if len(parts) < 2:
        return False
    label_re = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")
    return all(bool(label_re.match(p)) for p in parts)


def validate_staging_config(staging: Dict[str, Any]) -> List[str]:
    """ステージング設定全体のバリデーションを行い、エラーメッセージのリストを返す"""
    errors: List[str] = []
    if not staging or not isinstance(staging, dict):
        return ["ステージング設定データが存在しません。"]

    # 1. PPPoE 設定の検証
    pppoe = staging.get("pppoe", {})
    server_instances = pppoe.get("server_instances")
    if isinstance(server_instances, list):
        seen_svcs = set()
        for idx, s in enumerate(server_instances):
            name = re.sub(r"\s+", "_", s.get("name", "").strip())
            s["name"] = name
            row_num = idx + 1
            if not name:
                errors.append(f"PPPoE サービス定義: {row_num}行目のサービス名が未入力です。")
            elif len(name) > 11:
                errors.append(
                    f"PPPoE サービス定義: サービス名「{name}」が {len(name)} 文字です。"
                    f"最大 11 文字以下にしてください（Linux VRF デバイス名 'vrf-<サービス名>' の 15 文字制限のため）。"
                )
            elif not re.match(r"^[a-zA-Z0-9_-]+$", name):
                errors.append(
                    f"PPPoE サービス定義: サービス名「{name}」に使用できない不正な文字が含まれています。"
                    f"半角英数字、アンダースコア（_）、ハイフン（-）を使用してください（例: VPN_Group1）。"
                )
            elif name.lower() in seen_svcs:
                errors.append(f"PPPoE サービス定義: サービス名「{name}」が重複しています。")
            else:
                seen_svcs.add(name.lower())

    users_raw = pppoe.get("users", [])
    users_by_vlan = {"common": users_raw} if isinstance(users_raw, list) else (users_raw if isinstance(users_raw, dict) else {})
    ip_re = re.compile(r'^(\d{1,3}\.){3}\d{1,3}(/([0-9]|[12]\d|3[012]))?$')

    for vlan, users in users_by_vlan.items():
        if not isinstance(users, list):
            continue
        seen_unames = set()
        for idx, u in enumerate(users):
            uname = u.get("username", "").strip()
            pwd = u.get("password", "").strip()
            ip = u.get("ip", "").strip()
            norm_srv = re.sub(r"\s+", "_", u.get("service_name", u.get("ac_name", "*")).strip() or "*")
            u["service_name"] = norm_srv
            u["ac_name"] = norm_srv
            note = u.get("note", "").strip()
            ddns = u.get("ddns_v4", "").strip()
            row_num = idx + 1

            if not uname and not pwd and not ip and not note and not ddns:
                continue

            is_pool_user = (ip in ('*', ''))
            missing = []
            if not uname:
                missing.append("ユーザー名")
            if not pwd:
                missing.append("パスワード")
            if not ip and not is_pool_user:
                missing.append("クライアントIP")

            prefix = f"PPPoE (VLAN {vlan})" if vlan != "common" else "PPPoE 認証情報"
            if missing:
                target = f"ユーザー「{uname}」" if uname else f"{row_num}行目"
                errors.append(f"{prefix}: {target} の必須項目（{', '.join(missing)}）が未入力です。")
            elif uname:
                if uname in seen_unames:
                    errors.append(f"{prefix}: ユーザー名「{uname}」が重複しています。")
                seen_unames.add(uname)
                if not is_pool_user and ip:
                    ip_ok = False
                    if ip_re.match(ip):
                        parts_ip = ip.split('/')[0].split('.')
                        if all(0 <= int(o) <= 255 for o in parts_ip):
                            if '/' in ip:
                                plen = int(ip.split('/')[1])
                                if 24 <= plen <= 32:
                                    ip_ok = True
                                else:
                                    errors.append(
                                        f"{prefix}: ユーザー「{uname}」のクライアントIPのサブネットは"
                                        f" /24 〜 /32 の範囲で指定してください（入力値: /{plen}）。"
                                    )
                            else:
                                ip_ok = True
                    if not ip_ok and not errors:
                        errors.append(
                            f"{prefix}: ユーザー「{uname}」のクライアントIPの形式が正しくありません"
                            f"（例: 192.168.1.1 または 192.168.1.0/29）。"
                        )
                if norm_srv and norm_srv != "*" and len(norm_srv) > 11:
                    errors.append(
                        f"{prefix}: ユーザー「{uname}」のサービス名「{norm_srv}」が {len(norm_srv)} 文字です。"
                        f"最大 11 文字以下にしてください（Linux VRF デバイス名制限のため）。"
                    )

    # 2. 固定IP設定 (map-e-static-ip.conf)
    static_ips = staging.get("ipoe_common", {}).get("static_ips")
    if static_ips is None:
        static_ips = (staging.get("slaac", {}).get("static_ips") or []) + (staging.get("dhcp_pd", {}).get("static_ips") or [])

    if isinstance(static_ips, list):
        seen_vlans_static = set()
        for idx, entry in enumerate(static_ips):
            vlan_key = entry.get("mac", "").strip()
            ip = entry.get("ip", "").strip()
            note = entry.get("note", "").strip()
            row_num = idx + 1
            if not vlan_key and not ip and not note:
                continue
            if not vlan_key:
                errors.append(f"固定IP設定: {row_num}行目のVLAN番号が未入力です。")
            elif not vlan_key.isdigit() or not (1 <= int(vlan_key) <= 4094):
                errors.append(f"固定IP設定: {row_num}行目のVLAN番号「{vlan_key}」が不正です。1〜4094 の半角数字で指定してください（MACアドレス指定は廃止されました）。")
            elif int(vlan_key) in seen_vlans_static:
                errors.append(f"固定IP設定: VLAN番号「{vlan_key}」が重複しています。")
            else:
                seen_vlans_static.add(int(vlan_key))

    # 3. MAP-E VLAN 重複チェック
    low_layer = staging.get("low_layer", {})
    mape_categories = [
        ("SLAAC_DYN_VLANS", "SLAAC (動的)"),
        ("PD_DYN_VLANS", "DHCP-PD (動的)"),
        ("SLAAC_FIX_VLANS", "SLAAC (固定)"),
        ("PD_FIX_VLANS", "DHCP-PD (固定)"),
        ("HGW_VLANS", "HGW配下模擬 (動的)"),
        ("HGW_FIX_VLANS", "HGW配下模擬 (固定)"),
    ]
    seen_vlans: Dict[str, str] = {}
    for key, label in mape_categories:
        vlan_val = low_layer.get(key, "")
        vlans = (
            [str(v).strip() for v in vlan_val if str(v).strip()]
            if isinstance(vlan_val, list)
            else [v.strip() for v in str(vlan_val).split() if v.strip()]
        )
        for v in vlans:
            if v in seen_vlans:
                errors.append(
                    f"MAP-E VLAN重複: VLAN {v} が「{seen_vlans[v]}」と「{label} ({key})」で重複しています。"
                    f"MAP-E の各方式間で同一 VLAN ID を重複指定することはできません（Kea DHCPv6 および radvd が起動失敗します）。"
                )
            else:
                seen_vlans[v] = f"{label} ({key})"

    return errors


# ===========================================================================
# 3. 設定ファイル生成ヘルパー
# ===========================================================================

def generate_map_e_conf_content(staging_data: Dict[str, Any], original_content: str = "") -> str:
    """staging_data に基づき map-e.conf の内容を構築する"""
    pppoe_vlans_str = staging_data.get("low_layer", {}).get("PPPOE_VLANS", "").strip()

    flat_vars: Dict[str, Any] = {}
    for section in ["low_layer", "ipoe_common", "slaac", "dhcp_pd", "hgw"]:
        for k, v in staging_data.get(section, {}).items():
            if k != "static_ips" and not isinstance(v, (list, dict)):
                flat_vars[k] = v

    server_instances = staging_data.get("pppoe", {}).get("server_instances")
    all_srv_names: List[str] = []
    vrf_enabled_vars: Dict[str, str] = {}

    if isinstance(server_instances, list):
        for inst in server_instances:
            s_name = re.sub(r"\s+", "_", (inst.get("name") or "").strip())
            if not s_name:
                continue
            if s_name not in all_srv_names:
                all_srv_names.append(s_name)
            safe_var_name = re.sub(r"[^A-Za-z0-9_]", "_", s_name)
            vrf_enabled_vars[safe_var_name] = "1" if inst.get("vrf_enabled") else "0"

    def g(key: str, default: str = "") -> str:
        return str(flat_vars.get(key, default))

    pppoe_services = " ".join(all_srv_names) if all_srv_names else g("PPPOE_SERVICES")

    out = [
        "# =============================================================================\n",
        "# Management Network Configuration (管理用ネットワーク)\n",
        "# =============================================================================\n",
        f'MGT_IF="{g("MGT_IF", "ens18")}"\n',
        f'MGT_IP="{g("MGT_IP")}"\n',
        f'MGT_GW="{g("MGT_GW")}"\n\n',
        f'SYSTEM_DNS="{g("SYSTEM_DNS")}"\n',
        f'SYSTEM_NTP="{g("SYSTEM_NTP")}"\n',
        f'DOMAIN="{g("DOMAIN")}"\n\n',
        "# IPv6 アドレスの省略記法 :: は変数の後ろには置かない\n",
        "# PREFIX では省略記法を使わず :0: とする\n",
        "# =============================================================================\n",
        "# WAN & MAP-E Network Configuration\n",
        "# =============================================================================\n",
        f'MAPE_IF="{g("MAPE_IF", "ens19")}"\n',
        "# それぞれの方式で接続するVLAN番号（接続宣言VLAN）\n",
        "# ①SLAAC動的 / ②PD動的 / ③SLAAC固定 / ④PD固定 / ⑤HGW動的 / ⑥HGW固定 / PPPoE\n",
        f'SLAAC_DYN_VLANS="{g("SLAAC_DYN_VLANS")}"\n',
        f'PD_DYN_VLANS="{g("PD_DYN_VLANS")}"\n',
        f'SLAAC_FIX_VLANS="{g("SLAAC_FIX_VLANS")}"\n',
        f'PD_FIX_VLANS="{g("PD_FIX_VLANS")}"\n',
        f'HGW_VLANS="{g("HGW_VLANS")}"\n',
        f'HGW_FIX_VLANS="{g("HGW_FIX_VLANS")}"\n',
        f'PPPOE_VLANS="{pppoe_vlans_str}"\n\n',
        "# PPPoE Server Local IP Base\n",
        f'PPPOE_SERVER_BASE_IP="{g("PPPOE_SERVER_BASE_IP", "203.0.113.0")}"\n\n',
        "# Web Dashboard Port\n",
        f'WEB_DASHBOARD_PORT="{g("WEB_DASHBOARD_PORT", "1600")}"\n\n',
        "# 外部接続 NAT (1: 有効, 0: 無効)\n",
        f'NAT_ENABLED="{g("NAT_ENABLED", "0")}"\n\n',
        f'BR_IF="{g("BR_IF", "dummy0")}"\n',
        f'PROV_IF="{g("PROV_IF", "dummy1")}"\n\n',
        "# 基幹IPv6プレフィックス (末尾にコロンは含めない)\n",
        f'BASE_SUBNET="{g("BASE_SUBNET", "2400:4150")}"\n\n',
        "# =============================================================================\n",
        "# 6. MAP-E (OCN Virtual Connect) Parameters\n",
        "# =============================================================================\n",
        f'BR_PREFIX="{g("BR_PREFIX", "${BASE_SUBNET}:3620")}"\n',
        f'BR_IPV4_ADDR="{g("BR_IPV4_ADDR", "100.127.255.255")}"\n',
        "# BR_IPV6 : 自動生成\n\n",
        "# 動的 IPv4 クライアント(CE)へ払い出すIPv4プールセグメント\n",
        f'BR_IPV4_POOL="{g("BR_IPV4_POOL", "10.248.0.0/16")}"\n\n',
        "# プロビジョニング用ネットワーク (DNS, NTP, provisioning server)\n",
        f'MAPE_PROV_PREFIX="{g("MAPE_PROV_PREFIX", "${BASE_SUBNET}:9999")}"\n',
        f'MAPE_DNS_IP="{g("MAPE_DNS_IP", "${MAPE_PROV_PREFIX}::53")}"\n',
        f'MAPE_NTP_IP="{g("MAPE_NTP_IP", "${MAPE_PROV_PREFIX}::123")}"\n',
        f'MAPE_PROV_IP="{g("MAPE_PROV_IP", "${MAPE_PROV_PREFIX}::ff")}"\n',
        f'MAPE_DOMAIN_SEARCH="{g("MAPE_DOMAIN_SEARCH", "map.ocn.ad.jp")}"\n\n',
        "# --- 1. ひかり電話無し動的 IPv4 SLAAC WAN ---\n",
        f'SLAAC_BR_BASE="{g("SLAAC_BR_BASE", "1000")}"\n',
        f'SLAAC_BR_SUFFIX="{g("SLAAC_BR_SUFFIX", "::ff0a")}"\n\n',
        "# --- 2. ひかり電話あり動的 IPv4 DHCPv6-PD ---\n",
        f'PD_POOL="{g("PD_POOL", "2000")}"\n\n',
        "# --- 3. ひかり電話なし固定IP SLAAC ---\n",
        f'SLAAC_FIX_BR_PREFIX="{g("SLAAC_FIX_BR_PREFIX", "3000")}"\n',
        f'SLAAC_FIX_BR_SUFFIX="{g("SLAAC_FIX_BR_SUFFIX", "::ff0b")}"\n\n',
        "# --- 4. ひかり電話あり・固定 IPv4 (DHCPv6-PD固定) ---\n",
        f'PD_FIX_POOL="{g("PD_FIX_POOL", "4000")}"\n\n',
        "# --- 5. HGW配下模擬 動的 (RA /64 + DHCPv6-PD /60 併用) ---\n",
        f'HGW_BR_BASE="{g("HGW_BR_BASE", "5000")}"\n',
        f'HGW_BR_SUFFIX="{g("HGW_BR_SUFFIX", "::ff0c")}"\n',
        f'HGW_PD_DELEGATED_LEN="{g("HGW_PD_DELEGATED_LEN", "60")}"\n\n',
        "# --- 6. HGW配下模擬 固定IPv4 (RA /64 + DHCPv6-PD /60 + 固定IPv4) ---\n",
        f'HGW_FIX_BR_BASE="{g("HGW_FIX_BR_BASE", "6000")}"\n',
        f'HGW_FIX_BR_SUFFIX="{g("HGW_FIX_BR_SUFFIX", "::ff0d")}"\n\n',
        "# =============================================================================\n",
        "# PPPoE Multi-Service Configuration\n",
        "# =============================================================================\n",
        f'PPPOE_SERVICES="{pppoe_services}"\n\n',
        "# PPPoE Dynamic IP Pool (固定IPを指定しないユーザーへ払い出す範囲)\n",
        "# CIDR形式 (例: 10.9.0.0/24)  空欄時はプール払い出しなし (固定IP必須)\n",
        f'PPPOE_IP_POOL="{g("PPPOE_IP_POOL")}"\n\n',
    ]

    if vrf_enabled_vars:
        out.append("# --- Services VRF Settings ---\n")
        for srv_key, val in vrf_enabled_vars.items():
            out.append(f'PPPOE_VRF_ENABLED_{srv_key}="{val}"\n')
        out.append("\n")

    return "".join(out)


def generate_pppoe_conf_content(users: List[Dict[str, Any]]) -> str:
    """pppoe.conf の内容を生成する"""
    lines = [
        "# PPPoE User Configuration\n",
        "# Format: Username Password Client_IP_or_Subnet Service_Name Note\n",
    ]
    for idx, u in enumerate(users):
        uname = u.get("username", "").strip()
        pwd = u.get("password", "").strip()
        ip = u.get("ip", "").strip()
        note = u.get("note", "").strip()
        ddns = u.get("ddns_v4", "").strip()
        row_num = idx + 1

        if not uname and not pwd and not ip and not note and not ddns:
            continue

        if not uname or not pwd:
            missing = []
            if not uname:
                missing.append("ユーザー名")
            if not pwd:
                missing.append("パスワード")
            target = f"ユーザー「{uname}」" if uname else f"{row_num}行目"
            raise ValueError(f"PPPoE: {target} の必須項目（{', '.join(missing)}）が未入力です。")

        srv = re.sub(r"\s+", "_", u.get("service_name", u.get("ac_name", "*")).strip() or "*")
        ip_out = ip if (ip and ip != '*') else '*'
        lines.append(f"{uname:<30} {pwd:<14} {ip_out:<20} {srv:<12} {note}\n")
    return "".join(lines)


def generate_static_ip_content(static_ips: List[Dict[str, str]]) -> str:
    """map-e-static-ip.conf の内容を生成する"""
    lines = ["# VLAN番号, 固定IPv4プレフィックス, 備考\n"]
    seen = set()
    for idx, entry in enumerate(static_ips):
        vlan_key = entry.get("mac", "").strip()
        ip = entry.get("ip", "").strip()
        note = entry.get("note", "").strip()
        row_num = idx + 1

        if not vlan_key and not ip and not note:
            continue
        if not vlan_key:
            raise ValueError(f"固定IP設定: {row_num}行目のVLAN番号が未入力です。")
        if not vlan_key.isdigit() or not (1 <= int(vlan_key) <= 4094):
            raise ValueError(f"固定IP設定: {row_num}行目のVLAN番号「{vlan_key}」が不正です（1〜4094の半角数字で指定してください）。")
        vlan_num = int(vlan_key)
        if vlan_num in seen:
            raise ValueError(f"固定IP設定: VLAN番号「{vlan_num}」が重複しています。")
        seen.add(vlan_num)

        ip_val = ip if (ip and ip != "-") else "-"
        if ip_val != "-":
            try:
                net = ipaddress.IPv4Network(ip_val, strict=False)
                ip_val = f"{net.network_address}/{net.prefixlen}"
            except Exception:
                pass

        lines.append(f"{vlan_num}, {ip_val}, {note}\n" if note else f"{vlan_num}, {ip_val}\n")
    return "".join(lines)


def generate_ddns_conf_content(staging_data: Dict[str, Any], ddns_conf_path: str = "") -> str:
    """staging_data (IPoE の static_ips、PPPoE の users) から ddns.conf の内容を構築する"""
    lines = [
        "# =============================================================================\n",
        "# DDNS (Dynamic DNS) Hostname Mapping Configuration (DDNS設定ファイル)\n",
        "# =============================================================================\n",
        "# フォーマット:\n",
        "# 識別キー (VLAN番号 または PPPoEユーザー名), IPv6用FQDN, IPv4用FQDN\n",
        "#\n",
        "# ・1列目: VLAN番号 (半角数字) または PPPoEユーザー名\n",
        "# ・2列目: IPv6用FQDN (AAAA レコード + TXT レコードが登録されます。PPPoE等は -)\n",
        "# ・3列目: IPv4用FQDN (A レコード + TXT レコードが登録されます)\n",
        "# ※ 未登録のクライアントは標準形式 (ce-<IPv4アドレス>.map.ocn.ad.jp) が自動適用されます。\n",
        "#\n",
        "# --- IPoE (MAP-E) クライアント (VLAN ID 指定) ---\n",
    ]

    static_ips = staging_data.get("ipoe_common", {}).get("static_ips")
    if static_ips is None:
        static_ips = (staging_data.get("slaac", {}).get("static_ips") or []) + (staging_data.get("dhcp_pd", {}).get("static_ips") or [])

    seen_vlans = set()
    vlan_entries = []
    for entry in (static_ips or []):
        raw_key = entry.get("mac", "").strip()
        if not raw_key or not raw_key.isdigit():
            continue
        raw_v6 = entry.get("ddns_v6", "").strip()
        raw_v4 = entry.get("ddns_v4", "").strip()
        v6_fqdn = raw_v6 if is_valid_ddns_hostname(raw_v6) else "-"
        v4_fqdn = raw_v4 if is_valid_ddns_hostname(raw_v4) else "-"
        if v6_fqdn == "-" and v4_fqdn == "-":
            continue
        vlan_id = str(int(raw_key))
        if vlan_id not in seen_vlans:
            seen_vlans.add(vlan_id)
            vlan_entries.append((vlan_id, v6_fqdn, v4_fqdn))

    if vlan_entries:
        for vl, v6, v4 in vlan_entries:
            lines.append(f"{vl}, {v6}, {v4}\n")
    else:
        lines.append("# (登録なし)\n")

    lines.append("\n# --- PPPoE クライアント (ユーザー名指定) ---\n")
    pppoe_users = staging_data.get("pppoe", {}).get("common_users") or staging_data.get("pppoe", {}).get("users", {})
    pppoe_users_iter = (
        [("common", pppoe_users)] if isinstance(pppoe_users, list)
        else (pppoe_users.items() if isinstance(pppoe_users, dict) else [])
    )

    seen_users = set()
    pppoe_count = 0
    for _, ulist in pppoe_users_iter:
        if not isinstance(ulist, list):
            continue
        for u in ulist:
            uname = u.get("username", "").strip()
            if not uname or uname.lower() in seen_users:
                continue
            seen_users.add(uname.lower())
            raw_v4 = u.get("ddns_v4", "").strip()
            if is_valid_ddns_hostname(raw_v4):
                lines.append(f"{uname}, -, {raw_v4}\n")
                pppoe_count += 1
    if pppoe_count == 0:
        lines.append("# (登録なし)\n")

    return "".join(lines)


# ===========================================================================
# 4. ホストネットワーク同期ヘルパー
# ===========================================================================

def sync_host_vlan_interfaces(staging: Dict[str, Any], logs: List[str]) -> None:
    """map-e.conf で指定された MAPE_IF と各 VLAN サブインターフェース・dummy デバイスをホスト上に同期する"""
    lowlayer = staging.get("low_layer", staging.get("lowlayer", {}))
    mape_if = lowlayer.get("MAPE_IF", "eth1").strip() or "eth1"
    common = staging.get("ipoe_common", {})
    slaac = staging.get("slaac", {})
    hgw = staging.get("hgw", {})

    ret, _, _ = run_cmd(["ip", "link", "show", mape_if], privileged=True)
    if ret != 0:
        logs.append(f"[WARN] 親インターフェース '{mape_if}' が見つかりません。ホストVLAN同期をスキップします。")
        return

    run_cmd(["ip", "link", "set", "dev", mape_if, "up"], privileged=True)

    vlan_types: Dict[int, set] = {}
    active_vlan_set = set()
    for key in ["SLAAC_DYN_VLANS", "PD_DYN_VLANS", "SLAAC_FIX_VLANS", "PD_FIX_VLANS", "PPPOE_VLANS", "HGW_VLANS", "HGW_FIX_VLANS"]:
        for v in str(lowlayer.get(key, "")).split():
            v = v.strip()
            if v.isdigit():
                vid = int(v)
                vlan_types.setdefault(vid, set()).add(key)
                active_vlan_set.add(vid)

    # 1. 不要な既存 VLAN インターフェースの自動削除
    ret_links, out_links, _ = run_cmd(["ip", "-o", "link", "show"], privileged=True)
    if ret_links == 0:
        pattern = re.compile(r"^[0-9]+:\s+(" + re.escape(mape_if) + r"\.(\d+))(@\S+)?:")
        for line in out_links.splitlines():
            m = pattern.match(line.strip())
            if m and int(m.group(2)) not in active_vlan_set:
                dev_name = m.group(1)
                del_ret, _, del_err = run_cmd(["ip", "link", "del", "dev", dev_name], privileged=True)
                if del_ret == 0:
                    logs.append(f"不要なホストVLANを削除しました: {dev_name}")
                else:
                    logs.append(f"[WARN] 不要VLAN {dev_name} 削除失敗: {del_err.strip()}")

    # 2. 必要な VLAN インターフェースの作成・UP・IPv6有効化・アドレス設定
    for vlan in sorted(active_vlan_set):
        types = vlan_types[vlan]
        vif = f"{mape_if}.{vlan}"
        ret_chk, _, _ = run_cmd(["ip", "link", "show", vif], privileged=True)
        if ret_chk != 0:
            ret_add, _, err_add = run_cmd(
                ["ip", "link", "add", "link", mape_if, "name", vif, "type", "vlan", "id", str(vlan)],
                privileged=True
            )
            if ret_add == 0:
                logs.append(f"ホストVLAN作成: {vif} (VLAN ID: {vlan})")
            else:
                logs.append(f"[WARN] {vif} 作成失敗: {err_add.strip()}")

        run_cmd(["ip", "link", "set", "dev", vif, "up"], privileged=True)

        sysctl_vif = vif.replace(".", "/")
        run_cmd(["sysctl", "-w", f"net.ipv6.conf.{sysctl_vif}.disable_ipv6=0"], privileged=True)
        run_cmd(["sysctl", "-w", f"net.ipv6.conf.{sysctl_vif}.accept_ra=0"], privileged=True)
        run_cmd(["sysctl", "-w", f"net.ipv6.conf.{sysctl_vif}.autoconf=0"], privileged=True)

        if types & {"SLAAC_DYN_VLANS", "SLAAC_FIX_VLANS", "HGW_VLANS", "HGW_FIX_VLANS"}:
            base = common.get("BASE_SUBNET", "").strip()
            is_dyn = False
            if "SLAAC_FIX_VLANS" in types:
                base_value = slaac.get("SLAAC_FIX_BR_PREFIX", "3000")
                suffix = slaac.get("SLAAC_FIX_BR_SUFFIX", "::ff0b")
            elif "HGW_VLANS" in types:
                base_value = hgw.get("HGW_BR_BASE", "5000")
                suffix = hgw.get("HGW_BR_SUFFIX", "::ff0c")
                is_dyn = True
            elif "HGW_FIX_VLANS" in types:
                base_value = hgw.get("HGW_FIX_BR_BASE", "6000")
                suffix = hgw.get("HGW_FIX_BR_SUFFIX", "::ff0d")
            else:
                base_value = slaac.get("SLAAC_BR_BASE", "1000")
                suffix = slaac.get("SLAAC_BR_SUFFIX", "::ff0a")
                is_dyn = True
            try:
                hextet4 = f":{((vlan - 1) % 63) + 1:02x}00" if is_dyn else ""
                address = f"{base}:{int(base_value) + vlan}{hextet4}::{suffix.lstrip(':')}/64"
                run_cmd(["ip", "-6", "addr", "replace", address, "dev", vif], privileged=True)
            except (TypeError, ValueError):
                logs.append(f"[WARN] SLAAC/HGW アドレスを計算できません: {vif}")

    def ensure_dummy(name: str) -> None:
        if run_cmd(["ip", "link", "show", name], privileged=True)[0] != 0:
            run_cmd(["ip", "link", "add", name, "type", "dummy"], privileged=True)
        run_cmd(["ip", "link", "set", "dev", name, "up"], privileged=True)

    try:
        base = common.get("BASE_SUBNET", "5f00:3aa").strip() or "5f00:3aa"
        br_prefix = common.get("BR_PREFIX", f"{base}:aaaa").replace("${BASE_SUBNET}", base)
        br_ipv4 = common.get("BR_IPV4_ADDR", "192.0.2.1")
        ipv4 = ipaddress.IPv4Address(br_ipv4)
        br_ipv6 = f"{br_prefix.rstrip(':')}::{ipv4.packed.hex()[:4]}:{ipv4.packed.hex()[4:]}"
        ensure_dummy("dummy0")
        run_cmd(["ip", "-4", "addr", "replace", f"{br_ipv4}/32", "dev", "dummy0"], privileged=True)
        run_cmd(["ip", "-6", "addr", "flush", "dev", "dummy0", "scope", "global"], privileged=True)
        run_cmd(["ip", "-6", "addr", "replace", f"{br_ipv6}/64", "dev", "dummy0"], privileged=True)

        prov_prefix = common.get("MAPE_PROV_PREFIX", f"{base}:9999").replace("${BASE_SUBNET}", base).rstrip(":")
        ensure_dummy("dummy1")
        run_cmd(["ip", "-6", "addr", "flush", "dev", "dummy1", "scope", "global"], privileged=True)
        for suffix in ("53", "ff", "123"):
            run_cmd(["ip", "-6", "addr", "replace", f"{prov_prefix}::{suffix}/64", "dev", "dummy1"], privileged=True)
    except (TypeError, ValueError) as exc:
        logs.append(f"[WARN] BR 内部アドレスの同期に失敗しました: {exc}")


def install_system_configs(workspace_dir: str, logs: List[str]) -> bool:
    """生成された system/* 設定ファイルをホストの /etc へ反映し、sysctl / VRF / トンネル同期を実行する"""
    for source, target in (
        (os.path.join(workspace_dir, "system", "interfaces"), "/etc/network/interfaces"),
        (os.path.join(workspace_dir, "system", "99-network-routing.conf"), "/etc/sysctl.d/99-network-routing.conf"),
    ):
        ret_i, _, err_i = run_cmd(
            ["install", "-o", "root", "-g", "root", "-m", "644", source, target],
            privileged=True,
        )
        if ret_i == 0:
            logs.append(f"✔ 永続設定を更新しました: {target}")
        else:
            logs.append(f"✕ 永続設定の更新に失敗しました: {target}: {err_i.strip()}")
            return False

    run_cmd(["sysctl", "-p", "/etc/sysctl.d/99-network-routing.conf"], privileged=True)

    vrf_script = os.path.join(workspace_dir, "system", "vrf-setup.sh")
    if os.path.isfile(vrf_script):
        run_cmd(["modprobe", "vrf"], privileged=True)
        ret_v, out_v, err_v = run_cmd(["sh", vrf_script], privileged=True)
        if ret_v == 0:
            logs.append("✔ VRF デバイスを同期しました (system/vrf-setup.sh)")
            if out_v.strip():
                logs.append(out_v.strip())
        else:
            logs.append(f"[WARN] vrf-setup.sh の実行に失敗しました: {err_v.strip()}")

    return True


# ===========================================================================
# 5. AppConfig : 動的環境パラメータ提供クラス
# ===========================================================================

class AppConfig:
    """map-e.conf を読み込み、動的な環境パラメータを提供する管理クラス"""

    def __init__(self):
        self._raw_conf: Dict[str, str] = {}
        self.reload()

    def reload(self) -> None:
        """map-e.conf を探索して再読み込みを行う"""
        candidates = [
            os.path.join(WORKSPACE_DIR, "map-e.conf"),
            "/app/workspace/map-e.conf",
            "/app/map-e.conf",
            "./map-e.conf",
            "../map-e.conf",
        ]
        self._raw_conf = {}
        for path in candidates:
            if os.path.exists(path):
                self._raw_conf = self._parse_shell_conf(path)
                break

    @staticmethod
    def _parse_shell_conf(filepath: str) -> Dict[str, str]:
        """Shell スタイルの KEY=VALUE 設定ファイルを解析し、${VAR} 変数展開を行う"""
        conf: Dict[str, str] = {}
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    for vk, vv in list(conf.items()):
                        v = v.replace(f"${{{vk}}}", vv)
                    conf[k] = v
        except Exception as e:
            print(f"[Config] Error reading {filepath}: {e}", file=sys.stderr)
        return conf

    def get(self, key: str, default: str = "") -> str:
        return self._raw_conf.get(key, default)

    def get_vlan_list(self, key: str, default_list: Optional[List[int]] = None) -> List[int]:
        """空白区切りの VLAN 文字列を整数のリストとして返す"""
        val = self._raw_conf.get(key, "")
        if not val:
            return default_list or []
        vlans = [int(item) for item in val.split() if item.isdigit()]
        return vlans if vlans else (default_list or [])

    @property
    def br_info(self) -> Dict[str, str]:
        """BR の IPv6 アドレスと IPv4 アドレスを取得または合成して返す"""
        br_ipv4 = self.get("BR_IPV4_ADDR", "")
        br_prefix = self.get("BR_PREFIX", "")
        if not br_prefix:
            base_subnet = self.get("BASE_SUBNET", "")
            if base_subnet:
                br_prefix = f"{base_subnet}:3220"

        hex_suffix = ""
        if br_ipv4:
            try:
                octets = [int(x) for x in br_ipv4.split(".")]
                hex_suffix = f"{octets[0]:02x}{octets[1]:02x}:{octets[2]:02x}{octets[3]:02x}"
            except Exception:
                pass

        br_ipv6 = f"{br_prefix.rstrip(':')}::{hex_suffix}" if br_prefix and hex_suffix else ""
        return {"br_ipv6": br_ipv6, "br_ipv4": br_ipv4}

    @property
    def pppoe_vlans(self) -> List[str]:
        """アクティブな PPPoE VLAN ID のリストを返す（MAP-E VLAN を合算）"""
        vlan_set = {v.strip() for v in self.get("PPPOE_VLANS", "").split() if v.strip()}
        for k in ["SLAAC_DYN_VLANS", "PD_DYN_VLANS", "SLAAC_FIX_VLANS", "PD_FIX_VLANS"]:
            for v in self.get(k, "").split():
                if v.strip().isdigit():
                    vlan_set.add(v.strip())
        return sorted(vlan_set, key=lambda x: int(x) if x.isdigit() else x)

    @property
    def ipoe_vlans(self) -> Dict[str, List[str]]:
        """IPoE の各接続方式に割り当てられた VLAN 一覧を返す"""
        return {
            "slaac_dyn": [v.strip() for v in self.get("SLAAC_DYN_VLANS", "").split() if v.strip()],
            "pd_dyn":    [v.strip() for v in self.get("PD_DYN_VLANS", "").split() if v.strip()],
            "slaac_fix": [v.strip() for v in self.get("SLAAC_FIX_VLANS", "").split() if v.strip()],
            "pd_fix":    [v.strip() for v in self.get("PD_FIX_VLANS", "").split() if v.strip()],
            "hgw_dyn":   [v.strip() for v in self.get("HGW_VLANS", "").split() if v.strip()],
            "hgw_fix":   [v.strip() for v in self.get("HGW_FIX_VLANS", "").split() if v.strip()],
        }

    @property
    def pppoe_servers(self) -> List[Dict[str, Any]]:
        """map-e.conf に定義された PPPoE サーバー（サービス単位）のメタデータを返す"""
        servers = []
        for s_name in self.get("PPPOE_SERVICES", "").split():
            s_name = s_name.strip()
            if not s_name:
                continue
            servers.append({
                "ac_name": s_name,
                "service_name": s_name,
                "vrf_enabled": self.get(f"PPPOE_VRF_ENABLED_{s_name}", "0") == "1",
            })
        return servers


# ===========================================================================
# 6. ConfigManager : 設定ファイルの読み書き・ステージング・反映クラス
# ===========================================================================

class ConfigManager:
    """
    map-e.conf、pppoe.conf、map-e-static-ip.conf、ddns.conf の読み込み、
    ステージング管理、ファイル書き出し、および設定反映を統括する。
    """
    MAP_E_CONF_PATH = os.path.join(WORKSPACE_DIR, "map-e.conf")
    STATIC_IP_CONF_PATH = os.path.join(WORKSPACE_DIR, "map-e-static-ip.conf")
    DDNS_CONF_PATH = os.path.join(WORKSPACE_DIR, "ddns.conf")
    STAGING_CONF_PATH = os.path.join(WORKSPACE_DIR, ".config_staging.json")

    _MGT_KEYS = {
        "MGT_IF", "MGT_IP", "MGT_GW",
        "SYSTEM_DNS", "SYSTEM_NTP", "DOMAIN", "WEB_DASHBOARD_PORT",
    }

    _parse_env_file = staticmethod(parse_env_file)
    _is_valid_ddns_hostname = staticmethod(is_valid_ddns_hostname)
    validate_staging_config = staticmethod(validate_staging_config)
    _generate_map_e_conf_content = staticmethod(generate_map_e_conf_content)
    _generate_pppoe_conf_content = staticmethod(generate_pppoe_conf_content)
    _generate_static_ip_content = staticmethod(generate_static_ip_content)
    _sync_host_vlan_interfaces = staticmethod(sync_host_vlan_interfaces)

    @classmethod
    def _parse_pppoe_users(cls) -> List[Dict[str, Any]]:
        return parse_pppoe_users(os.path.join(WORKSPACE_DIR, "pppoe.conf"))

    @classmethod
    def _parse_static_ips(cls) -> List[Dict[str, str]]:
        return parse_static_ips(cls.STATIC_IP_CONF_PATH)

    @classmethod
    def _parse_ddns_conf(cls) -> Dict[str, Dict[str, str]]:
        return parse_ddns_conf(cls.DDNS_CONF_PATH)

    @classmethod
    def _generate_ddns_conf_content(cls, staging_data: Dict[str, Any]) -> str:
        return generate_ddns_conf_content(staging_data, cls.DDNS_CONF_PATH)

    @classmethod
    def _build_config_dict(
        cls,
        raw: Dict[str, str],
        common_users: List[Dict[str, Any]],
        all_static: List[Dict[str, str]],
        ddns_map: Dict[str, Dict[str, str]],
        default_srv_fallback: bool = False,
    ) -> Dict[str, Any]:
        """パース済みの辞書・リストから Web UI 用の設定データ構造を組み立てる共通ヘルパー"""
        low_layer = {
            "PPPOE_VLANS": raw.get("PPPOE_VLANS", ""),
            "SLAAC_DYN_VLANS": raw.get("SLAAC_DYN_VLANS", ""),
            "PD_DYN_VLANS": raw.get("PD_DYN_VLANS", ""),
            "SLAAC_FIX_VLANS": raw.get("SLAAC_FIX_VLANS", ""),
            "PD_FIX_VLANS": raw.get("PD_FIX_VLANS", ""),
            "HGW_VLANS": raw.get("HGW_VLANS", ""),
            "HGW_FIX_VLANS": raw.get("HGW_FIX_VLANS", ""),
            "MAPE_IF": raw.get("MAPE_IF", "eth1"),
            "MGT_IF": raw.get("MGT_IF", "eth0"),
            "MGT_IP": raw.get("MGT_IP", ""),
            "MGT_GW": raw.get("MGT_GW", ""),
            "SYSTEM_DNS": raw.get("SYSTEM_DNS", ""),
            "SYSTEM_NTP": raw.get("SYSTEM_NTP", ""),
            "DOMAIN": raw.get("DOMAIN", ""),
            "PPPOE_SERVER_BASE_IP": raw.get("PPPOE_SERVER_BASE_IP", "203.0.113.0"),
            "PPPOE_IP_POOL": raw.get("PPPOE_IP_POOL", ""),
            "WEB_DASHBOARD_PORT": raw.get("WEB_DASHBOARD_PORT", "1600"),
            "NAT_ENABLED": raw.get("NAT_ENABLED", "0"),
        }

        for u in common_users:
            uname = u.get("username", "").strip().lower()
            u["ddns_v4"] = ddns_map.get(uname, {}).get("v4", "")

        server_instances: List[Dict[str, Any]] = []
        srv_inst_map: Dict[str, Dict[str, Any]] = {}
        for s_name in raw.get("PPPOE_SERVICES", "").split():
            s_name = s_name.strip()
            if not s_name:
                continue
            safe_s = re.sub(r"[^A-Za-z0-9_]", "_", s_name)
            vrf_en = (raw.get(f"PPPOE_VRF_ENABLED_{safe_s}") or raw.get(f"PPPOE_VRF_ENABLED_{s_name}", "0")) == "1"
            inst = {"name": s_name, "vrf_enabled": vrf_en}
            srv_inst_map[s_name] = inst
            server_instances.append(inst)

        for u in common_users:
            stag = (u.get("service_name") or u.get("ac_name") or "").strip()
            if stag and stag != "*" and stag not in srv_inst_map:
                inst = {"name": stag, "vrf_enabled": False}
                srv_inst_map[stag] = inst
                server_instances.append(inst)

        if default_srv_fallback and not server_instances:
            server_instances = [{"name": "OCN", "vrf_enabled": False}]

        matched_ddns_keys = set()
        for s in all_static:
            vlan_raw = s.get("mac", "").strip()
            vlan_key = str(int(vlan_raw)) if vlan_raw.isdigit() else vlan_raw.lower()
            ddns_info = ddns_map.get(vlan_key) or ddns_map.get(vlan_raw.lower()) or {}
            if vlan_key in ddns_map:
                matched_ddns_keys.add(vlan_key)
            if vlan_raw.lower() in ddns_map:
                matched_ddns_keys.add(vlan_raw.lower())
            s["ddns_v6"] = ddns_info.get("v6", "")
            s["ddns_v4"] = ddns_info.get("v4", "")

        seen_pppoe_users = {u.get("username", "").strip().lower() for u in common_users}
        for k, v in ddns_map.items():
            if k in matched_ddns_keys or k in seen_pppoe_users or not k.isdigit():
                continue
            all_static.append({
                "mac": k,
                "ip": "",
                "note": f"VLAN {k} DDNS",
                "ddns_v6": v.get("v6", ""),
                "ddns_v4": v.get("v4", ""),
            })

        return {
            "low_layer": low_layer,
            "pppoe": {
                "server_instances": server_instances,
                "users": common_users,
                "common_users": common_users,
            },
            "ipoe_common": {
                "BR_IPV4_POOL": raw.get("BR_IPV4_POOL", "198.51.0.0/16"),
                "BASE_SUBNET": raw.get("BASE_SUBNET", "5f00:3aa"),
                "BR_PREFIX": raw.get("BR_PREFIX", "${BASE_SUBNET}:aaaa"),
                "BR_IPV4_ADDR": raw.get("BR_IPV4_ADDR", "192.0.2.1"),
                "BR_IF": raw.get("BR_IF", "dummy0"),
                "PROV_IF": raw.get("PROV_IF", "dummy1"),
                "MAPE_PROV_PREFIX": raw.get("MAPE_PROV_PREFIX", "${BASE_SUBNET}:9999"),
                "MAPE_DNS_IP": raw.get("MAPE_DNS_IP", "${MAPE_PROV_PREFIX}::53"),
                "MAPE_NTP_IP": raw.get("MAPE_NTP_IP", "${MAPE_PROV_PREFIX}::123"),
                "MAPE_PROV_IP": raw.get("MAPE_PROV_IP", "${MAPE_PROV_PREFIX}::ff"),
                "MAPE_DOMAIN_SEARCH": raw.get("MAPE_DOMAIN_SEARCH", "map.example.ad.jp"),
                "static_ips": all_static,
            },
            "slaac": {
                "SLAAC_BR_BASE": raw.get("SLAAC_BR_BASE", "1000"),
                "SLAAC_BR_SUFFIX": raw.get("SLAAC_BR_SUFFIX", "::ff0a"),
                "SLAAC_FIX_BR_PREFIX": raw.get("SLAAC_FIX_BR_PREFIX", "3000"),
                "SLAAC_FIX_BR_SUFFIX": raw.get("SLAAC_FIX_BR_SUFFIX", "::ff0b"),
                "static_ips": all_static,
            },
            "dhcp_pd": {
                "PD_POOL": raw.get("PD_POOL", "2000"),
                "PD_FIX_POOL": raw.get("PD_FIX_POOL", "4000"),
                "static_ips": all_static,
            },
            "hgw": {
                "HGW_BR_BASE": raw.get("HGW_BR_BASE", "5000"),
                "HGW_BR_SUFFIX": raw.get("HGW_BR_SUFFIX", "::ff0c"),
                "HGW_FIX_BR_BASE": raw.get("HGW_FIX_BR_BASE", "6000"),
                "HGW_FIX_BR_SUFFIX": raw.get("HGW_FIX_BR_SUFFIX", "::ff0d"),
            },
        }

    @classmethod
    def get_live_config(cls) -> Dict[str, Any]:
        """現在のファイル群から稼働中（Live）設定を読み取って辞書形式で返す"""
        raw = parse_env_file(cls.MAP_E_CONF_PATH)
        return cls._build_config_dict(
            raw=raw,
            common_users=cls._parse_pppoe_users(),
            all_static=cls._parse_static_ips(),
            ddns_map=cls._parse_ddns_conf(),
        )

    @classmethod
    def get_template_config(cls) -> Dict[str, Any]:
        """テンプレートファイル (*.tmpl) から初期設定を読み込み、管理 LAN 設定を保護して返す"""
        map_e_tmpl = os.path.join(WORKSPACE_DIR, "map-e.conf.tmpl")
        static_tmpl = os.path.join(WORKSPACE_DIR, "map-e-static-ip.conf.tmpl")
        pppoe_tmpl = os.path.join(WORKSPACE_DIR, "pppoe.conf.tmpl")

        tmpl_vars = parse_env_file(map_e_tmpl if os.path.exists(map_e_tmpl) else cls.MAP_E_CONF_PATH)
        live_vars = parse_env_file(cls.MAP_E_CONF_PATH)
        for key in cls._MGT_KEYS:
            if key in live_vars:
                tmpl_vars[key] = live_vars[key]

        return cls._build_config_dict(
            raw=tmpl_vars,
            common_users=parse_pppoe_users(pppoe_tmpl),
            all_static=parse_static_ips(static_tmpl),
            ddns_map={},
            default_srv_fallback=True,
        )

    @classmethod
    def get_staging_config(cls) -> Dict[str, Any]:
        """ステージングファイルがあれば読み込み、なければライブ設定を返す"""
        if os.path.exists(cls.STAGING_CONF_PATH):
            try:
                with open(cls.STAGING_CONF_PATH, "r", encoding="utf-8") as f:
                    stg = json.load(f)
                    if "NAT_ENABLED" not in stg.get("low_layer", {}):
                        live = cls.get_live_config()
                        stg.setdefault("low_layer", {})["NAT_ENABLED"] = live.get("low_layer", {}).get("NAT_ENABLED", "0")
                    return stg
            except Exception:
                pass
        return cls.get_live_config()

    @classmethod
    def has_pending_changes(cls) -> bool:
        """未反映の変更（ステージングとライブの差分）が存在するか判定する"""
        if not os.path.exists(cls.STAGING_CONF_PATH):
            return False
        return json.dumps(cls.get_live_config(), sort_keys=True) != json.dumps(cls.get_staging_config(), sort_keys=True)

    @classmethod
    def save_staging(cls, data: Dict[str, Any]) -> bool:
        """ステージング設定を一時ファイルに保存する"""
        with open(cls.STAGING_CONF_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True

    @classmethod
    def discard_staging(cls) -> bool:
        """ステージング設定ファイルを破棄する"""
        if os.path.exists(cls.STAGING_CONF_PATH):
            os.remove(cls.STAGING_CONF_PATH)
        return True

    @classmethod
    def _create_backup(cls, filepath: str, max_keep: int = 1) -> Optional[str]:
        """指定ファイルのバックアップを作成し、最新 max_keep 世代のみを維持する"""
        if not os.path.exists(filepath):
            return None
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        bak_path = f"{filepath}.bak.{ts}"
        shutil.copy2(filepath, bak_path)

        existing = sorted(glob.glob(f"{filepath}.bak.*"), key=os.path.getmtime, reverse=True)
        for old_bak in existing[max_keep:]:
            try:
                os.remove(old_bak)
            except OSError:
                pass
        return os.path.basename(bak_path)

    @classmethod
    def apply_staging(cls) -> Dict[str, Any]:
        """ステージング設定をファイルに書き出し、make generate とホスト適用を実行する"""
        global APP_CONFIG
        staging = cls.get_staging_config()
        logs: List[str] = []

        val_errors = validate_staging_config(staging)
        if val_errors:
            error_details = "\n".join(f"・{e}" for e in val_errors)
            logs.append("✕ 設定検証エラー:\n" + error_details)
            logs.append("⚠ 入力内容に不備があるため、安全のため反映処理を中断しました。")
            return {
                "success": False,
                "message": f"設定内容に不備があります。入力内容を確認してください:\n{error_details}",
                "logs": "\n".join(logs),
            }

        try:
            for path in (cls.MAP_E_CONF_PATH, cls.STATIC_IP_CONF_PATH, cls.DDNS_CONF_PATH):
                bak = cls._create_backup(path, max_keep=1)
                if bak:
                    logs.append(f"Backup created: {bak}")

            new_map_e = generate_map_e_conf_content(staging)
            with open(cls.MAP_E_CONF_PATH, "w", encoding="utf-8") as f:
                f.write(new_map_e)
            logs.append("Saved: map-e.conf")

            raw_pppoe_users = staging.get("pppoe", {}).get("common_users") or staging.get("pppoe", {}).get("users", [])
            if isinstance(raw_pppoe_users, list):
                common_users = raw_pppoe_users
            elif isinstance(raw_pppoe_users, dict):
                common_users = next((u for u in raw_pppoe_users.values() if isinstance(u, list) and u), [])
            else:
                common_users = []

            common_pppoe_path = os.path.join(WORKSPACE_DIR, "pppoe.conf")
            bak_common = cls._create_backup(common_pppoe_path, max_keep=1)
            if bak_common:
                logs.append(f"Backup created: {bak_common}")
            with open(common_pppoe_path, "w", encoding="utf-8") as f:
                f.write(generate_pppoe_conf_content(common_users))
            logs.append(f"Saved: pppoe.conf ({len(common_users)} users)")

            static_ips = staging.get("ipoe_common", {}).get("static_ips")
            if static_ips is None:
                static_ips = (staging.get("slaac", {}).get("static_ips") or []) + (staging.get("dhcp_pd", {}).get("static_ips") or [])

            with open(cls.STATIC_IP_CONF_PATH, "w", encoding="utf-8") as f:
                f.write(generate_static_ip_content(static_ips or []))
            logs.append(f"Saved: map-e-static-ip.conf ({len(static_ips or [])} entries)")

            with open(cls.DDNS_CONF_PATH, "w", encoding="utf-8") as f:
                f.write(generate_ddns_conf_content(staging, cls.DDNS_CONF_PATH))
            logs.append("Saved: ddns.conf")

            ret_g, out_g, err_g = run_cmd(["make", "-o", "check-user", "generate"], timeout=40, cwd=WORKSPACE_DIR)
            if ret_g == 0:
                logs.append("✔ 関連設定ファイルを生成しました")
            else:
                logs.append(f"✕ 設定ファイル生成に失敗しました (code {ret_g})")
                if out_g:
                    logs.append("--- make generate output ---\n" + out_g.strip())
                if err_g:
                    logs.append("--- make generate stderr ---\n" + err_g.strip())
                return {
                    "success": False,
                    "message": "設定ファイル生成に失敗しました。map-e.conf の未定義パラメータ等を確認してください。",
                    "logs": "\n".join(logs),
                }

            install_system_configs(WORKSPACE_DIR, logs)
            sync_host_vlan_interfaces(staging, logs)

            host_dir = get_host_workspace_dir()
            compose_file = os.path.join(WORKSPACE_DIR, "docker-compose.yml")
            base_compose = ["docker", "compose", "--project-directory", host_dir, "-f", compose_file, "-p", "map-e_ocn"]
            ret_u, _, _ = run_cmd(base_compose + ["up", "-d", "--remove-orphans"], timeout=60, cwd=WORKSPACE_DIR)
            if ret_u == 0:
                logs.append("✔ サービスを更新しました")
                run_cmd(["docker", "exec", "axosyslog", "syslog-ng-ctl", "reload"], timeout=10)
                run_cmd(["docker", "exec", "bind9", "rndc", "reload"], timeout=10)
                run_cmd(["docker", "exec", "chrony", "chronyc", "reload", "sources"], timeout=10)
                ret_r, _, _ = run_cmd(base_compose + ["restart", "kea-dhcp6", "radvd"], timeout=20)
                if ret_r == 0:
                    logs.append("✔ DHCPv6 (Kea) / RA (radvd) を再起動してインターフェースをバインドしました")

            ret_ps, out_ps, _ = run_cmd(base_compose + ["ps", "--format", "table {{.Service}}\t{{.Status}}"], timeout=10, cwd=WORKSPACE_DIR)
            if ret_ps == 0 and out_ps:
                logs.append("\n【サービス稼働ステータス】\n" + out_ps.strip())

            cls.discard_staging()
            APP_CONFIG = AppConfig()

            success = (ret_g == 0 and ret_u == 0)
            return {
                "success": success,
                "message": "設定の反映が完了しました" if success else "設定の反映中にエラーが発生しました",
                "logs": "\n".join(logs),
            }
        except Exception as e:
            err_details = f"反映例外: {str(e)}"
            logs.append(f"\n✕ {err_details}\n{traceback.format_exc()}")
            return {"success": False, "message": err_details, "logs": "\n".join(logs)}

    @classmethod
    def restart_containers(cls) -> Dict[str, Any]:
        """ネットワーク設定を再適用してから Web 以外の Compose コンテナを再作成する"""
        logs: List[str] = []
        ret_gen, out_gen, err_gen = run_cmd(["make", "-o", "check-user", "generate"], timeout=40, cwd=WORKSPACE_DIR)
        if ret_gen != 0:
            return {
                "success": False,
                "message": "設定ファイルの再生成に失敗しました",
                "logs": (out_gen + "\n" + err_gen).strip(),
            }
        logs.append("設定ファイルを再生成しました")

        install_system_configs(WORKSPACE_DIR, logs)
        sync_host_vlan_interfaces(cls.get_live_config(), logs)

        host_dir = get_host_workspace_dir()
        compose_file = os.path.join(WORKSPACE_DIR, "docker-compose.yml")
        base_cmd = ["docker", "compose", "--project-directory", host_dir, "-f", compose_file, "-p", "map-e_ocn"]
        ret_cfg, out_cfg, err_cfg = run_cmd(base_cmd + ["config", "--services"], timeout=20)
        if ret_cfg != 0:
            return {
                "success": False,
                "message": "Compose サービス一覧を取得できませんでした。",
                "logs": (out_cfg + "\n" + err_cfg).strip(),
            }

        services = [s.strip() for s in out_cfg.splitlines() if s.strip() and s.strip() != "web-dashboard"]
        if not services:
            return {"success": False, "message": "再起動対象のコンテナがありません。", "logs": ""}

        ret, out, err = run_cmd(base_cmd + ["up", "-d", "--force-recreate", "--remove-orphans"] + services, timeout=120)
        compose_logs = (out + "\n" + err).strip()
        if compose_logs:
            logs.append(compose_logs)
        return {
            "success": ret == 0,
            "message": "ネットワークを再設定し、コンテナを再起動しました" if ret == 0 else "ネットワーク再設定後のコンテナ再起動に失敗しました",
            "logs": "\n".join(logs),
        }


APP_CONFIG = AppConfig()
