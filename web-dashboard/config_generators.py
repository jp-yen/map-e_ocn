# web-dashboard/config_generators.py
"""
設定ファイル生成モジュール。
ステージングデータから map-e.conf, pppoe.conf, map-e-static-ip.conf, ddns.conf の内容を構築する。
"""

import os
import re
from typing import Dict, List, Any

from config_validators import is_valid_ddns_hostname


def generate_map_e_conf_content(staging_data: Dict[str, Any], original_content: str = "") -> str:
    """staging_data に基づき map-e.conf をスクラッチから新規生成する。
    既存ファイルの内容は参照せず、staging_data の値のみで完全な設定ファイルを構築する。
    original_content 引数は後方互換のために残しているが使用しない。
    """
    pppoe_vlans_str = staging_data.get("low_layer", {}).get("PPPOE_VLANS", "").strip()

    flat_vars: Dict[str, Any] = {}
    for section in ["low_layer", "ipoe_common", "slaac", "dhcp_pd"]:
        sec_data = staging_data.get(section, {})
        for k, v in sec_data.items():
            # static_ips やリスト/辞書データはシェル変数として絶対に出力しない
            if k != "static_ips" and not isinstance(v, (list, dict)):
                flat_vars[k] = v

    pppoe_data = staging_data.get("pppoe", {})
    server_instances = pppoe_data.get("server_instances")

    # サービス名は元の文字（ハイフン含む）を維持し、VRF変数キーのみシェル変数用にアンダースコア正規化
    all_srv_names: List[str] = []
    vrf_enabled_vars: Dict[str, str] = {}

    if isinstance(server_instances, list) and len(server_instances) > 0:
        for inst in server_instances:
            s_name = re.sub(r"\s+", "_", (inst.get("name") or "").strip())
            if not s_name:
                continue
            if s_name not in all_srv_names:
                all_srv_names.append(s_name)
            safe_var_name = re.sub(r"[^A-Za-z0-9_]", "_", s_name)
            vrf_enabled_vars[safe_var_name] = "1" if inst.get("vrf_enabled") else "0"

    def g(key: str, default: str = "") -> str:
        """flat_vars から値を取得し、文字列として返す"""
        return str(flat_vars.get(key, default))

    # =========================================================================
    # map-e.conf を最初から構築する（パッチではなく完全新規生成）
    # =========================================================================
    out: List[str] = []

    out += [
        "# =============================================================================\n",
        "# Management Network Configuration (管理用ネットワーク)\n",
        "# =============================================================================\n",
        f'MGT_IF="{g("MGT_IF", "ens18")}"\n',
        f'MGT_IP="{g("MGT_IP")}"\n',
        f'MGT_GW="{g("MGT_GW")}"\n',
        "\n",
        f'SYSTEM_DNS="{g("SYSTEM_DNS")}"\n',
        f'SYSTEM_NTP="{g("SYSTEM_NTP")}"\n',
        f'DOMAIN="{g("DOMAIN")}"\n',
        "\n",
        "# IPv6 アドレスの省略記法 :: は変数の後ろには置かない\n",
        "# PREFIX では省略記法を使わず :0: とする\n",
        "# =============================================================================\n",
        "# WAN & MAP-E Network Configuration\n",
        "# =============================================================================\n",
        f'MAPE_IF="{g("MAPE_IF", "ens19")}"\n',
        "# それぞれの方式で接続するVLAN番号（接続宣言VLAN）\n",
        "# ①SLAAC動的 / ②PD動的 / ③SLAAC固定 / ④PD固定 / PPPoE\n",
        f'SLAAC_DYN_VLANS="{g("SLAAC_DYN_VLANS")}"\n',
        f'PD_DYN_VLANS="{g("PD_DYN_VLANS")}"\n',
        f'SLAAC_FIX_VLANS="{g("SLAAC_FIX_VLANS")}"\n',
        f'PD_FIX_VLANS="{g("PD_FIX_VLANS")}"\n',
        f'PPPOE_VLANS="{pppoe_vlans_str}"\n',
        "\n",
        "# PPPoE Server Local IP Base\n",
        f'PPPOE_SERVER_BASE_IP="{g("PPPOE_SERVER_BASE_IP", "203.0.113.0")}"\n',
        "\n",
        "# Web Dashboard Port\n",
        f'WEB_DASHBOARD_PORT="{g("WEB_DASHBOARD_PORT", "1600")}"\n',
        "\n",
        "# 外部接続 NAT (1: 有効, 0: 無効)\n",
        f'NAT_ENABLED="{g("NAT_ENABLED", "0")}"\n',
        "\n",
        f'BR_IF="{g("BR_IF", "dummy0")}"\n',
        f'PROV_IF="{g("PROV_IF", "dummy1")}"\n',
        "\n",
        "# 基幹IPv6プレフィックス (末尾にコロンは含めない)\n",
        f'BASE_SUBNET="{g("BASE_SUBNET", "2400:4150")}"\n',
        "\n",
        "# =============================================================================\n",
        "# 6. MAP-E (OCN Virtual Connect) Parameters\n",
        "# =============================================================================\n",
        f'BR_PREFIX="{g("BR_PREFIX", "${BASE_SUBNET}:3620")}"\n',
        f'BR_IPV4_ADDR="{g("BR_IPV4_ADDR", "100.127.255.255")}"\n',
        "# BR_IPV6 : 自動生成\n",
        "\n",
        "# 動的 IPv4 クライアント(CE)へ払い出すIPv4プールセグメント\n",
        f'BR_IPV4_POOL="{g("BR_IPV4_POOL", "10.248.0.0/16")}"\n',
        "\n",
        "# プロビジョニング用ネットワーク (DNS, NTP, provisioning server)\n",
        f'MAPE_PROV_PREFIX="{g("MAPE_PROV_PREFIX", "${BASE_SUBNET}:9999")}"\n',
        f'MAPE_DNS_IP="{g("MAPE_DNS_IP", "${MAPE_PROV_PREFIX}::53")}"\n',
        f'MAPE_NTP_IP="{g("MAPE_NTP_IP", "${MAPE_PROV_PREFIX}::123")}"\n',
        f'MAPE_PROV_IP="{g("MAPE_PROV_IP", "${MAPE_PROV_PREFIX}::ff")}"\n',
        f'MAPE_DOMAIN_SEARCH="{g("MAPE_DOMAIN_SEARCH", "map.ocn.ad.jp")}"\n',
        "\n",
        "# --- 1. ひかり電話無し動的 IPv4 SLAAC WAN ---\n",
        f'SLAAC_BR_BASE="{g("SLAAC_BR_BASE", "1000")}"\n',
        f'SLAAC_BR_SUFFIX="{g("SLAAC_BR_SUFFIX", "::ff0a")}"\n',
        "\n",
        "# --- 2. ひかり電話あり動的 IPv4 DHCPv6-PD ---\n",
        f'PD_POOL="{g("PD_POOL", "2000")}"\n',
        "\n",
        "# --- 3. ひかり電話なし固定IP SLAAC ---\n",
        f'SLAAC_FIX_BR_PREFIX="{g("SLAAC_FIX_BR_PREFIX", "3000")}"\n',
        f'SLAAC_FIX_BR_SUFFIX="{g("SLAAC_FIX_BR_SUFFIX", "::ff0d")}"\n',
        "\n",
        "# --- 4. ひかり電話あり・固定 IPv4 (DHCPv6-PD固定) ---\n",
        f'PD_FIX_POOL="{g("PD_FIX_POOL", "4000")}"\n',
        "\n",
        "# =============================================================================\n",
        "# PPPoE Multi-Service Configuration\n",
        "# =============================================================================\n",
    ]

    pppoe_services = " ".join(all_srv_names) if all_srv_names else g("PPPOE_SERVICES")
    out.append(f'PPPOE_SERVICES="{pppoe_services}"\n')

    out += [
        "\n",
        "# PPPoE Dynamic IP Pool (固定IPを指定しないユーザーへ払い出す範囲)\n",
        "# CIDR形式 (例: 10.9.0.0/24)  空欄時はプール払い出しなし (固定IP必須)\n",
        f'PPPOE_IP_POOL="{g("PPPOE_IP_POOL")}"\n',
        "\n",
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

        # すべて空欄の行は読み飛ばす
        if not uname and not pwd and not ip and not note and not ddns:
            continue

        # IP が空欄または '*' の場合はプール払い出し -> '*' として出力
        is_pool_user = (ip == '*' or ip == '')
        if not is_pool_user and (not uname or not pwd or not ip):
            missing = []
            if not uname:
                missing.append("ユーザー名")
            if not pwd:
                missing.append("パスワード")
            target = f"ユーザー「{uname}」" if uname else f"{row_num}行目"
            raise ValueError(f"PPPoE: {target} の必須項目（{', '.join(missing)}）が未入力です。")
        elif is_pool_user and not (uname and pwd):
            missing = []
            if not uname:
                missing.append("ユーザー名")
            if not pwd:
                missing.append("パスワード")
            target = f"ユーザー「{uname}」" if uname else f"{row_num}行目"
            raise ValueError(f"PPPoE: {target} の必須項目（{', '.join(missing)}）が未入力です。")

        srv = u.get("service_name", u.get("ac_name", "*")).strip() or "*"
        srv = re.sub(r"\s+", "_", srv)
        # IP が空欄または '*' の場合はプール払い出し -> '*' として出力
        ip_out = ip if (ip and ip != '*') else '*'
        line = f"{uname:<30} {pwd:<14} {ip_out:<20} {srv:<12} {note}\n"
        lines.append(line)
    return "".join(lines)


def generate_static_ip_content(static_ips: List[Dict[str, str]]) -> str:
    """map-e-static-ip.conf の内容を生成する"""
    lines = ["# VLAN番号 または MACアドレス, 固定IPv4プレフィックス, 備考\n"]
    seen = set()
    for idx, entry in enumerate(static_ips):
        mac = entry.get("mac", "").strip().lower()
        ip = entry.get("ip", "").strip()
        note = entry.get("note", "").strip()
        row_num = idx + 1

        # すべて空欄の行は読み飛ばす
        if not mac and not ip and not note:
            continue

        if not mac:
            raise ValueError(f"固定IP設定: {row_num}行目のVLAN・MACアドレスが未入力です。")
        if mac in seen:
            raise ValueError(f"固定IP設定: VLAN・MACアドレス「{mac}」が重複しています。")
        seen.add(mac)

        # IPv4プレフィックスが空欄の場合は '-' に置換
        ip_val = ip if (ip and ip != "-") else "-"
        if ip_val != "-":
            try:
                # ホストアドレスが入力された場合 (例: 172.16.19.38/28)、自動的にネットワークアドレス (172.16.19.32/28) へ正規化して保存
                net = ipaddress.IPv4Network(ip_val, strict=False)
                ip_val = f"{net.network_address}/{net.prefixlen}"
            except Exception:
                pass

        if note:
            lines.append(f"{mac}, {ip_val}, {note}\n")
        else:
            lines.append(f"{mac}, {ip_val}\n")
    return "".join(lines)


def generate_ddns_conf_content(staging_data: Dict[str, Any], ddns_conf_path: str) -> str:
    """
    staging_data (IPoE の static_ips、PPPoE の users) から ddns.conf の内容を構築する。
    ヘッダーの重複増殖を完全に防止する。
    """
    lines = [
        "# =============================================================================\n",
        "# DDNS (Dynamic DNS) Hostname Mapping Configuration (DDNS設定ファイル)\n",
        "# =============================================================================\n",
        "# フォーマット:\n",
        "# 識別キー (VLAN番号 または MACアドレス または PPPoEユーザー名), IPv6用FQDN, IPv4用FQDN\n",
        "#\n",
        "# ・1列目: VLAN番号 (半角数字)、MACアドレス (12桁Hexまたはコロン区切り)、または PPPoEユーザー名\n",
        "# ・2列目: IPv6用FQDN (AAAA レコード + TXT レコードが登録されます。PPPoE等は -)\n",
        "# ・3列目: IPv4用FQDN (A レコード + TXT レコードが登録されます)\n",
        "# ※ 未登録のクライアントは標準形式 (ce-<IPv4アドレス>.map.ocn.ad.jp) が自動適用されます。\n",
        "#\n",
    ]

    # IPoE クライアント (VLAN / MAC) の収集
    static_ips = staging_data.get("ipoe_common", {}).get("static_ips")
    if static_ips is None:
        slaac_static = staging_data.get("slaac", {}).get("static_ips", [])
        pd_static = staging_data.get("dhcp_pd", {}).get("static_ips", [])
        static_ips = (slaac_static or []) + (pd_static or [])

    seen_macs = set()
    seen_vlans = set()
    mac_entries = []
    vlan_entries = []

    for entry in (static_ips or []):
        raw_key = entry.get("mac", "").strip()
        if not raw_key:
            continue

        raw_v6 = entry.get("ddns_v6", "").strip()
        raw_v4 = entry.get("ddns_v4", "").strip()
        v6_fqdn = raw_v6 if is_valid_ddns_hostname(raw_v6) else "-"
        v4_fqdn = raw_v4 if is_valid_ddns_hostname(raw_v4) else "-"

        # DDNS ホスト名が両方 '-' の場合は ddns.conf には出力しない
        if v6_fqdn == "-" and v4_fqdn == "-":
            continue

        if raw_key.isdigit():
            vlan_id = raw_key
            if vlan_id in seen_vlans:
                continue
            seen_vlans.add(vlan_id)
            vlan_entries.append((vlan_id, v6_fqdn, v4_fqdn))
        else:
            mac = raw_key.lower().replace("-", ":")
            if ":" not in mac and len(mac) == 12:
                mac = ":".join(mac[i:i+2] for i in range(0, 12, 2))
            if mac in seen_macs:
                continue
            seen_macs.add(mac)
            mac_entries.append((mac, v6_fqdn, v4_fqdn))

    # 1. MACアドレス指定
    lines.append("# --- IPoE (MAP-E) クライアント (MACアドレス指定) ---\n")
    if mac_entries:
        for m, v6, v4 in mac_entries:
            lines.append(f"{m}, {v6}, {v4}\n")
    else:
        lines.append("# (登録なし)\n")

    # 2. VLAN ID 指定
    lines.append("\n# --- IPoE (MAP-E) クライアント (VLAN ID 指定) ---\n")
    if vlan_entries:
        for vl, v6, v4 in vlan_entries:
            lines.append(f"{vl}, {v6}, {v4}\n")
    else:
        lines.append("# (登録なし)\n")

    # 3. PPPoE ユーザーの DDNS エントリ
    lines.append("\n# --- PPPoE クライアント (ユーザー名指定) ---\n")
    pppoe_users = staging_data.get("pppoe", {}).get("common_users") or staging_data.get("pppoe", {}).get("users", {})
    seen_users = set()
    pppoe_count = 0
    if isinstance(pppoe_users, list):
        pppoe_users_iter = [("common", pppoe_users)]
    elif isinstance(pppoe_users, dict):
        pppoe_users_iter = pppoe_users.items()
    else:
        pppoe_users_iter = []

    for vlan, ulist in pppoe_users_iter:
        if not isinstance(ulist, list):
            continue
        for u in ulist:
            uname = u.get("username", "").strip()
            if not uname:
                continue
            uname_lower = uname.lower()
            if uname_lower in seen_users:
                continue
            seen_users.add(uname_lower)
            raw_v4 = u.get("ddns_v4", "").strip()
            v4_fqdn = raw_v4 if is_valid_ddns_hostname(raw_v4) else "-"
            if v4_fqdn != "-":
                lines.append(f"{uname}, -, {v4_fqdn}\n")
                pppoe_count += 1
    if pppoe_count == 0:
        lines.append("# (登録なし)\n")

    return "".join(lines)
