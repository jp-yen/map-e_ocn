# web-dashboard/config_manager.py
"""
ConfigManager : 設定ファイルの読み書き・ステージング・反映を統括するファサードクラス。

本モジュールは専門モジュール群をオーケストレーションし、統一的なインターフェースを提供する：
  - config_parsers.py    : 設定ファイルの解析
  - config_validators.py : 設定入力値のバリデーション
  - config_generators.py : 設定ファイル内容の生成
  - host_syncer.py       : ホストネットワーク/sysctlの同期

API メソッド:
  - get_live_config()        : 現在のライブ設定を返す
  - get_staging_config()     : ステージング設定を返す（なければライブ設定）
  - has_pending_changes()    : 未反映の変更があるか確認
  - save_staging()           : ステージング保存
  - discard_staging()        : ステージング破棄
  - get_template_config()    : テンプレートから初期設定を返す
  - validate_staging_config(): 設定バリデーション
  - apply_staging()          : ステージングをファイル書き出し + コンテナ反映
  - restart_containers()     : 設定再生成 + コンテナ再起動
"""

import os
import re
import json
import datetime
import shutil
import glob
import traceback
from typing import Dict, List, Any, Optional

from constants import WORKSPACE_DIR, get_host_workspace_dir
from utils import run_cmd
from config_app import AppConfig

from config_parsers import (
    parse_env_file,
    parse_pppoe_users,
    parse_static_ips,
    parse_ddns_conf,
)
from config_validators import (
    is_valid_ddns_hostname,
    validate_staging_config,
)
from config_generators import (
    generate_map_e_conf_content,
    generate_pppoe_conf_content,
    generate_static_ip_content,
    generate_ddns_conf_content,
)
from host_syncer import (
    sync_pppoe_vrf,
    sync_host_vlan_interfaces,
    install_system_configs,
)


class ConfigManager:
    """
    map-e.conf、pppoe.conf、map-e-static-ip.conf、ddns.conf の読み込み、
    ステージング（反映待ち状態）の管理、ファイルへの書き出し、
    および make generate / コンテナ再起動による設定反映を統括する。
    """
    MAP_E_CONF_PATH = os.path.join(WORKSPACE_DIR, "map-e.conf")
    STATIC_IP_CONF_PATH = os.path.join(WORKSPACE_DIR, "map-e-static-ip.conf")
    DDNS_CONF_PATH = os.path.join(WORKSPACE_DIR, "ddns.conf")
    STAGING_CONF_PATH = os.path.join(WORKSPACE_DIR, ".config_staging.json")

    # 後方互換性のための静的エイリアス
    _parse_env_file = staticmethod(parse_env_file)
    _is_valid_ddns_hostname = staticmethod(is_valid_ddns_hostname)
    validate_staging_config = staticmethod(validate_staging_config)
    _generate_map_e_conf_content = staticmethod(generate_map_e_conf_content)
    _generate_pppoe_conf_content = staticmethod(generate_pppoe_conf_content)
    _generate_static_ip_content = staticmethod(generate_static_ip_content)
    _sync_pppoe_vrf = staticmethod(sync_pppoe_vrf)
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
    def get_live_config(cls) -> Dict[str, Any]:
        """現在のファイル群から稼働中（Live）設定を読み取って辞書形式で返す"""
        raw = parse_env_file(cls.MAP_E_CONF_PATH)
        low_layer = {
            "PPPOE_VLANS": raw.get("PPPOE_VLANS", ""),
            "SLAAC_DYN_VLANS": raw.get("SLAAC_DYN_VLANS", ""),
            "PD_DYN_VLANS": raw.get("PD_DYN_VLANS", ""),
            "SLAAC_FIX_VLANS": raw.get("SLAAC_FIX_VLANS", ""),
            "PD_FIX_VLANS": raw.get("PD_FIX_VLANS", ""),
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

        ddns_map = cls._parse_ddns_conf()
        pppoe_vlans = [v for v in raw.get("PPPOE_VLANS", "").split() if v]
        base_server_ip = raw.get("PPPOE_SERVER_BASE_IP", "203.0.113.0").strip() or "203.0.113.0"

        # 1. 共通ユーザー一覧の取得 (pppoe.conf)
        common_users = cls._parse_pppoe_users()
        for u in common_users:
            uname = u.get("username", "").strip().lower()
            ddns_info = ddns_map.get(uname, {})
            u["ddns_v4"] = ddns_info.get("v4", "")

        # 2. サーバーインスタンスの構築 (map-e.conf の PPPOE_SERVICES またはサービス定義から)
        server_instances: List[Dict[str, Any]] = []
        srv_inst_map: Dict[str, Dict[str, Any]] = {}

        srv_names_raw = raw.get("PPPOE_SERVICES", "").split()

        for s_idx, s_name in enumerate(srv_names_raw):
            s_name = s_name.strip()
            if not s_name:
                continue
            safe_s = re.sub(r"[^A-Za-z0-9_]", "_", s_name)
            vrf_en = (raw.get(f"PPPOE_VRF_ENABLED_{safe_s}") or raw.get(f"PPPOE_VRF_ENABLED_{s_name}", "0")) == "1"
            inst = {
                "name": s_name,
                "vrf_enabled": vrf_en,
            }
            srv_inst_map[s_name] = inst
            server_instances.append(inst)

        # ユーザー定義から未登録の固有サービス名があれば補完
        for u in common_users:
            stag = (u.get("service_name") or u.get("ac_name") or "").strip()
            if stag and stag != "*" and stag not in srv_inst_map:
                inst = {
                    "name": stag,
                    "vrf_enabled": False,
                }
                srv_inst_map[stag] = inst
                server_instances.append(inst)

        all_static = cls._parse_static_ips()
        matched_ddns_keys = set()
        for s in all_static:
            mac_raw = s.get("mac", "").strip()
            mac_norm = mac_raw.lower().replace("-", ":")
            if ":" not in mac_norm and len(mac_norm) == 12:
                mac_norm = ":".join(mac_norm[i:i+2] for i in range(0, 12, 2))
            ddns_info = ddns_map.get(mac_norm) or ddns_map.get(mac_raw.lower()) or {}
            if mac_norm in ddns_map:
                matched_ddns_keys.add(mac_norm)
            if mac_raw.lower() in ddns_map:
                matched_ddns_keys.add(mac_raw.lower())
            s["ddns_v6"] = ddns_info.get("v6", "")
            s["ddns_v4"] = ddns_info.get("v4", "")

        seen_pppoe_users = {u.get("username", "").strip().lower() for u in common_users}
        for k, v in ddns_map.items():
            if k in matched_ddns_keys or k in seen_pppoe_users:
                continue
            all_static.append({
                "mac": k,
                "ip": "",
                "note": f"VLAN {k} DDNS" if k.isdigit() else "",
                "ddns_v6": v.get("v6", ""),
                "ddns_v4": v.get("v4", "")
            })

        ipoe_common = {
            "BR_IPV4_POOL": raw.get("BR_IPV4_POOL", "198.51.0.0/16"),
            "BASE_SUBNET": raw.get("BASE_SUBNET", "2001:db8"),
            "BR_PREFIX": raw.get("BR_PREFIX", "${BASE_SUBNET}:aaaa"),
            "BR_IPV4_ADDR": raw.get("BR_IPV4_ADDR", "192.0.2.1"),
            "BR_IF": raw.get("BR_IF", "dummy0"),
            "PROV_IF": raw.get("PROV_IF", "dummy1"),
            "MAPE_PROV_PREFIX": raw.get("MAPE_PROV_PREFIX", "${BASE_SUBNET}:9999"),
            "MAPE_DNS_IP": raw.get("MAPE_DNS_IP", "${MAPE_PROV_PREFIX}::53"),
            "MAPE_NTP_IP": raw.get("MAPE_NTP_IP", "${MAPE_PROV_PREFIX}::123"),
            "MAPE_PROV_IP": raw.get("MAPE_PROV_IP", "${MAPE_PROV_PREFIX}::ff"),
            "MAPE_DOMAIN_SEARCH": raw.get("MAPE_DOMAIN_SEARCH", "map.example.ad.jp"),
            "static_ips": all_static
        }

        slaac = {
            "SLAAC_BR_BASE": raw.get("SLAAC_BR_BASE", "1000"),
            "SLAAC_BR_SUFFIX": raw.get("SLAAC_BR_SUFFIX", "::ff0a"),
            "SLAAC_FIX_BR_PREFIX": raw.get("SLAAC_FIX_BR_PREFIX", "3000"),
            "SLAAC_FIX_BR_SUFFIX": raw.get("SLAAC_FIX_BR_SUFFIX", "::ff0d"),
            "static_ips": all_static
        }

        dhcp_pd = {
            "PD_POOL": raw.get("PD_POOL", "2000"),
            "PD_FIX_POOL": raw.get("PD_FIX_POOL", "4000"),
            "static_ips": all_static
        }

        return {
            "low_layer": low_layer,
            "pppoe": {
                "server_instances": server_instances,
                "users": common_users,
                "common_users": common_users
            },
            "ipoe_common": ipoe_common,
            "slaac": slaac,
            "dhcp_pd": dhcp_pd
        }

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
        live = cls.get_live_config()
        staging = cls.get_staging_config()
        return json.dumps(live, sort_keys=True) != json.dumps(staging, sort_keys=True)

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

    # 管理LAN設定キー (テンプレート初期化時に現在値を引き継ぐ)
    _MGT_KEYS = {
        "MGT_IF", "MGT_IP", "MGT_GW",
        "SYSTEM_DNS", "SYSTEM_NTP", "DOMAIN", "WEB_DASHBOARD_PORT",
    }

    @classmethod
    def get_template_config(cls) -> Dict[str, Any]:
        """
        テンプレートファイル (*.tmpl) から設定を読み込み、
        現在の map-e.conf の管理 LAN 設定を上書き保護した上で
        get_live_config() と同じ形式のデータを返す。
        """
        tmpl_dir = WORKSPACE_DIR
        map_e_tmpl = os.path.join(tmpl_dir, "map-e.conf.tmpl")
        static_tmpl = os.path.join(tmpl_dir, "map-e-static-ip.conf.tmpl")
        pppoe_tmpl = os.path.join(tmpl_dir, "pppoe.conf.tmpl")

        # --- map-e.conf.tmpl を読み込む ---
        if os.path.exists(map_e_tmpl):
            tmpl_vars = parse_env_file(map_e_tmpl)
        else:
            tmpl_vars = parse_env_file(cls.MAP_E_CONF_PATH)

        # --- 管理 LAN 設定を現在値で上書き ---
        live_vars = parse_env_file(cls.MAP_E_CONF_PATH)
        for key in cls._MGT_KEYS:
            if key in live_vars:
                tmpl_vars[key] = live_vars[key]

        low_layer = {
            "PPPOE_VLANS":          tmpl_vars.get("PPPOE_VLANS", ""),
            "SLAAC_DYN_VLANS":      tmpl_vars.get("SLAAC_DYN_VLANS", ""),
            "PD_DYN_VLANS":         tmpl_vars.get("PD_DYN_VLANS", ""),
            "SLAAC_FIX_VLANS":      tmpl_vars.get("SLAAC_FIX_VLANS", ""),
            "PD_FIX_VLANS":         tmpl_vars.get("PD_FIX_VLANS", ""),
            "MAPE_IF":              tmpl_vars.get("MAPE_IF", "eth1"),
            "MGT_IF":               tmpl_vars.get("MGT_IF", "eth0"),
            "MGT_IP":               tmpl_vars.get("MGT_IP", ""),
            "MGT_GW":               tmpl_vars.get("MGT_GW", ""),
            "SYSTEM_DNS":           tmpl_vars.get("SYSTEM_DNS", ""),
            "SYSTEM_NTP":           tmpl_vars.get("SYSTEM_NTP", ""),
            "DOMAIN":               tmpl_vars.get("DOMAIN", ""),
            "PPPOE_SERVER_BASE_IP": tmpl_vars.get("PPPOE_SERVER_BASE_IP", "203.0.113.0"),
            "PPPOE_IP_POOL":        tmpl_vars.get("PPPOE_IP_POOL", ""),
            "WEB_DASHBOARD_PORT":   tmpl_vars.get("WEB_DASHBOARD_PORT", "1600"),
            "NAT_ENABLED":          tmpl_vars.get("NAT_ENABLED", "0"),
        }

        pppoe_vlans = [v for v in tmpl_vars.get("PPPOE_VLANS", "").split() if v]

        # --- PPPoE サービス定義の構築 (PPPOE_SERVICES から) ---
        srv_names_raw = tmpl_vars.get("PPPOE_SERVICES", "").split()
        server_instances_tmpl: List[Dict[str, Any]] = []
        srv_map_tmpl = {}
        for s_name in srv_names_raw:
            s_name = s_name.strip()
            if not s_name:
                continue
            safe_s = re.sub(r"[^A-Za-z0-9_]", "_", s_name)
            vrf_en = (tmpl_vars.get(f"PPPOE_VRF_ENABLED_{safe_s}") or tmpl_vars.get(f"PPPOE_VRF_ENABLED_{s_name}", "0")) == "1"
            inst = {"name": s_name, "vrf_enabled": vrf_en}
            srv_map_tmpl[s_name] = inst
            server_instances_tmpl.append(inst)

        # --- pppoe.conf.tmpl からユーザー一覧を読み込む ---
        pppoe_users_tmpl: List[Dict[str, str]] = []
        if os.path.exists(pppoe_tmpl):
            with open(pppoe_tmpl, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split(None, 4)
                    if len(parts) >= 3:
                        pppoe_users_tmpl.append({
                            "username":     parts[0].strip(),
                            "password":     parts[1].strip(),
                            "ip":           parts[2].strip(),
                            "service_name": parts[3].strip() if len(parts) >= 4 else "*",
                            "ac_name":      parts[3].strip() if len(parts) >= 4 else "*",
                            "note":         parts[4].strip() if len(parts) >= 5 else "",
                            "ddns_v4":      "",
                        })
        pppoe_users = {v: pppoe_users_tmpl for v in pppoe_vlans}

        # --- map-e-static-ip.conf.tmpl から固定IP一覧を読み込む ---
        static_ips: List[Dict[str, str]] = []
        if os.path.exists(static_tmpl):
            with open(static_tmpl, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = [x.strip() for x in line.split(",")]
                    if len(parts) >= 2:
                        raw_ip = parts[1].strip()
                        static_ips.append({
                            "mac":    parts[0].strip(),
                            "ip":     "" if raw_ip == "-" else raw_ip,
                            "note":   parts[2].strip() if len(parts) >= 3 else "",
                            "ddns_v6": "",
                            "ddns_v4": "",
                        })

        ipoe_common = {
            "BR_IPV4_POOL":     tmpl_vars.get("BR_IPV4_POOL", "198.51.0.0/16"),
            "BASE_SUBNET":      tmpl_vars.get("BASE_SUBNET", "2001:db8"),
            "BR_PREFIX":        tmpl_vars.get("BR_PREFIX", ""),
            "BR_IPV4_ADDR":     tmpl_vars.get("BR_IPV4_ADDR", "192.0.2.1"),
            "BR_IF":            tmpl_vars.get("BR_IF", "dummy0"),
            "PROV_IF":          tmpl_vars.get("PROV_IF", "dummy1"),
            "MAPE_PROV_PREFIX": tmpl_vars.get("MAPE_PROV_PREFIX", ""),
            "MAPE_DNS_IP":      tmpl_vars.get("MAPE_DNS_IP", ""),
            "MAPE_NTP_IP":      tmpl_vars.get("MAPE_NTP_IP", ""),
            "MAPE_PROV_IP":     tmpl_vars.get("MAPE_PROV_IP", ""),
            "MAPE_DOMAIN_SEARCH": tmpl_vars.get("MAPE_DOMAIN_SEARCH", "map.example.ad.jp"),
            "static_ips":       static_ips,
        }
        slaac = {
            "SLAAC_BR_BASE":       tmpl_vars.get("SLAAC_BR_BASE", "1000"),
            "SLAAC_BR_SUFFIX":     tmpl_vars.get("SLAAC_BR_SUFFIX", "::ff0a"),
            "SLAAC_FIX_BR_PREFIX": tmpl_vars.get("SLAAC_FIX_BR_PREFIX", "3000"),
            "SLAAC_FIX_BR_SUFFIX": tmpl_vars.get("SLAAC_FIX_BR_SUFFIX", "::ff0d"),
            "static_ips":          static_ips,
        }
        dhcp_pd = {
            "PD_POOL":     tmpl_vars.get("PD_POOL", "2000"),
            "PD_FIX_POOL": tmpl_vars.get("PD_FIX_POOL", "4000"),
            "static_ips":  static_ips,
        }

        # ユーザー定義から未登録の固有サービス名があれば補完
        for u in pppoe_users_tmpl:
            stag = (u.get("service_name") or u.get("ac_name") or "").strip()
            if stag and stag != "*" and stag not in srv_map_tmpl:
                inst = {"name": stag, "vrf_enabled": False}
                srv_map_tmpl[stag] = inst
                server_instances_tmpl.append(inst)

        if not server_instances_tmpl:
            server_instances_tmpl = [{"name": "OCN", "vrf_enabled": False}]

        return {
            "low_layer":   low_layer,
            "pppoe": {
                "server_instances": server_instances_tmpl,
                "users": pppoe_users_tmpl,
                "common_users": pppoe_users_tmpl
            },
            "ipoe_common": ipoe_common,
            "slaac":       slaac,
            "dhcp_pd":     dhcp_pd,
        }

    @classmethod
    def _create_backup(cls, filepath: str, max_keep: int = 1) -> Optional[str]:
        """
        指定ファイルのバックアップを作成し、古いバックアップを削除して
        最新 max_keep 世代のみを維持する。
        """
        if not os.path.exists(filepath):
            return None

        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        bak_path = f"{filepath}.bak.{ts}"
        shutil.copy2(filepath, bak_path)

        pattern = f"{filepath}.bak.*"
        existing = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
        for old_bak in existing[max_keep:]:
            try:
                os.remove(old_bak)
            except OSError:
                pass

        return os.path.basename(bak_path)

    @classmethod
    def apply_staging(cls) -> Dict[str, Any]:
        """ステージング設定をファイルに書き出し、make generate とホスト適用を実行する"""
        staging = cls.get_staging_config()
        logs: List[str] = []

        # 0. 事前バリデーション
        val_errors = validate_staging_config(staging)
        if val_errors:
            error_details = "\n".join(f"・{e}" for e in val_errors)
            logs.append("✕ 設定検証エラー:\n" + error_details)
            logs.append("⚠ 入力内容に不備があるため、安全のため反映処理を中断しました。")
            return {
                "success": False,
                "message": f"設定内容に不備があります。入力内容を確認してください:\n{error_details}",
                "logs": "\n".join(logs)
            }

        try:
            # 1. バックアップの作成
            bak_map_e = cls._create_backup(cls.MAP_E_CONF_PATH, max_keep=1)
            if bak_map_e:
                logs.append(f"Backup created: {bak_map_e}")

            bak_static = cls._create_backup(cls.STATIC_IP_CONF_PATH, max_keep=1)
            if bak_static:
                logs.append(f"Backup created: {bak_static}")

            bak_ddns = cls._create_backup(cls.DDNS_CONF_PATH, max_keep=1)
            if bak_ddns:
                logs.append(f"Backup created: {bak_ddns}")

            # 2. map-e.conf の更新
            with open(cls.MAP_E_CONF_PATH, "r", encoding="utf-8", errors="ignore") as f:
                orig_content = f.read()
            new_map_e = generate_map_e_conf_content(staging, orig_content)
            with open(cls.MAP_E_CONF_PATH, "w", encoding="utf-8") as f:
                f.write(new_map_e)
            logs.append("Saved: map-e.conf")

            # 3. pppoe.conf の更新 (全 VLAN 共通認証リスト)
            raw_pppoe_users = staging.get("pppoe", {}).get("common_users") or staging.get("pppoe", {}).get("users", [])
            if isinstance(raw_pppoe_users, list):
                common_users = raw_pppoe_users
            elif isinstance(raw_pppoe_users, dict):
                common_users = []
                for u_list in raw_pppoe_users.values():
                    if isinstance(u_list, list) and u_list:
                        common_users = u_list
                        break
            else:
                common_users = []

            common_pppoe_path = os.path.join(WORKSPACE_DIR, "pppoe.conf")
            bak_common = cls._create_backup(common_pppoe_path, max_keep=1)
            if bak_common:
                logs.append(f"Backup created: {bak_common}")
            new_common_pppoe = generate_pppoe_conf_content(common_users)
            with open(common_pppoe_path, "w", encoding="utf-8") as f:
                f.write(new_common_pppoe)
            logs.append(f"Saved: pppoe.conf ({len(common_users)} users)")

            # 旧 pppoe_<VLAN>.conf ファイルおよび旧 pppoe/vlan-* ディレクトリの削除
            for old_f in glob.glob(os.path.join(WORKSPACE_DIR, "pppoe_*.conf")):
                try:
                    os.remove(old_f)
                    logs.append(f"Cleaned up legacy file: {os.path.basename(old_f)}")
                except Exception:
                    pass
            for old_d in glob.glob(os.path.join(WORKSPACE_DIR, "pppoe", "vlan-*")):
                try:
                    shutil.rmtree(old_d, ignore_errors=True)
                    logs.append(f"Cleaned up legacy dir: {os.path.basename(old_d)}")
                except Exception:
                    pass

            # 4. map-e-static-ip.conf の更新
            static_ips = staging.get("ipoe_common", {}).get("static_ips")
            if static_ips is None:
                slaac_static = staging.get("slaac", {}).get("static_ips", [])
                pd_static = staging.get("dhcp_pd", {}).get("static_ips", [])
                static_ips = (slaac_static or []) + (pd_static or [])

            new_static = generate_static_ip_content(static_ips or [])
            with open(cls.STATIC_IP_CONF_PATH, "w", encoding="utf-8") as f:
                f.write(new_static)
            logs.append(f"Saved: map-e-static-ip.conf ({len(static_ips or [])} entries)")

            # 4.5 ddns.conf の更新
            new_ddns = generate_ddns_conf_content(staging, cls.DDNS_CONF_PATH)
            with open(cls.DDNS_CONF_PATH, "w", encoding="utf-8") as f:
                f.write(new_ddns)
            logs.append("Saved: ddns.conf")

            # 5. make generate の実行
            cmd_gen = ["make", "-o", "check-user", "generate"]
            ret_g, out_g, err_g = run_cmd(cmd_gen, timeout=40, cwd=WORKSPACE_DIR)
            if ret_g == 0:
                logs.append("✔ 関連設定ファイルを生成しました")
            else:
                logs.append(f"✕ 設定ファイル生成に失敗しました (code {ret_g})")
                if out_g:
                    logs.append("--- make generate output ---\n" + out_g.strip())
                if err_g:
                    logs.append("--- make generate stderr ---\n" + err_g.strip())
                logs.append("⚠ 設定検証またはファイル生成に失敗したため、安全のため反映処理を中断しました。")
                return {
                    "success": False,
                    "message": "設定ファイル生成に失敗しました。map-e.conf の未定義パラメータ等を確認してください。",
                    "logs": "\n".join(logs)
                }

            # 永続設定のインストール & sysctl 適用 & VRF セットアップスクリプト実行
            install_system_configs(WORKSPACE_DIR, logs)

            # 5.6 ホスト側 VLAN インターフェースの自動作成・同期
            sync_host_vlan_interfaces(staging, logs)

            # 6. docker compose up -d による全サービス反映
            host_dir = get_host_workspace_dir()
            compose_file = os.path.join(WORKSPACE_DIR, "docker-compose.yml")
            cmd_up = ["docker", "compose", "--project-directory", host_dir, "-f", compose_file, "-p", "map-e_ocn", "up", "-d", "--remove-orphans"]
            ret_u, out_u, err_u = run_cmd(cmd_up, timeout=60, cwd=WORKSPACE_DIR)
            if ret_u == 0:
                logs.append("✔ サービスを更新しました")
                run_cmd(["docker", "exec", "axosyslog", "syslog-ng-ctl", "reload"], timeout=10)
                run_cmd(["docker", "exec", "bind9", "rndc", "reload"], timeout=10)
                run_cmd(["docker", "exec", "chrony", "chronyc", "reload", "sources"], timeout=10)
                # 新設された VLAN インターフェースへのマルチキャストバインドを確実にするため、kea-dhcp6 と radvd を明示的に再起動
                ret_r, _, _ = run_cmd(["docker", "compose", "--project-directory", host_dir, "-f", compose_file, "-p", "map-e_ocn", "restart", "kea-dhcp6", "radvd"], timeout=20)
                if ret_r == 0:
                    logs.append("✔ DHCPv6 (Kea) / RA (radvd) を再起動してインターフェースをバインドしました (NTP 設定も同期済)")

            # 6.5 コンテナの稼働ステータスを取得してログに表示
            cmd_ps = ["docker", "compose", "--project-directory", host_dir, "-f", compose_file, "-p", "map-e_ocn", "ps", "--format", "table {{.Service}}\t{{.Status}}"]
            ret_ps, out_ps, _ = run_cmd(cmd_ps, timeout=10, cwd=WORKSPACE_DIR)
            if ret_ps == 0 and out_ps:
                logs.append("\n【サービス稼働ステータス】\n" + out_ps.strip())

            # 7. ステージングの破棄（反映完了）
            cls.discard_staging()

            # 設定インスタンスのリロード
            import config as _config_mod  # noqa: PLC0415
            _config_mod.APP_CONFIG = AppConfig()

            success = (ret_g == 0 and ret_u == 0)
            return {
                "success": success,
                "message": "設定の反映が完了しました" if success else "設定の反映中にエラーが発生しました",
                "logs": "\n".join(logs)
            }
        except Exception as e:
            err_details = f"反映例外: {str(e)}"
            logs.append(f"\n✕ {err_details}\n{traceback.format_exc()}")
            return {
                "success": False,
                "message": err_details,
                "logs": "\n".join(logs)
            }

    @classmethod
    def restart_containers(cls) -> Dict[str, Any]:
        """ネットワーク設定を再適用してから Web 以外の Compose コンテナを再作成する"""
        logs: List[str] = []
        ret_gen, out_gen, err_gen = run_cmd(
            ["make", "-o", "check-user", "generate"], timeout=40, cwd=WORKSPACE_DIR
        )
        if ret_gen != 0:
            return {
                "success": False,
                "message": "設定ファイルの再生成に失敗しました",
                "logs": (out_gen + "\n" + err_gen).strip()
            }
        logs.append("設定ファイルを再生成しました")

        install_system_configs(WORKSPACE_DIR, logs)

        live_config = cls.get_live_config()
        sync_host_vlan_interfaces(live_config, logs)

        host_dir = get_host_workspace_dir()
        compose_file = os.path.join(WORKSPACE_DIR, "docker-compose.yml")
        base_cmd = ["docker", "compose", "--project-directory", host_dir,
                    "-f", compose_file, "-p", "map-e_ocn"]
        ret_cfg, out_cfg, err_cfg = run_cmd(base_cmd + ["config", "--services"], timeout=20)
        if ret_cfg != 0:
            return {"success": False, "message": "Compose サービス一覧を取得できませんでした。",
                    "logs": (out_cfg + "\n" + err_cfg).strip()}

        services = [s.strip() for s in out_cfg.splitlines() if s.strip() and s.strip() != "web-dashboard"]
        if not services:
            return {"success": False, "message": "再起動対象のコンテナがありません。", "logs": ""}

        ret, out, err = run_cmd(
            base_cmd + ["up", "-d", "--force-recreate", "--remove-orphans"] + services,
            timeout=120,
        )
        compose_logs = (out + "\n" + err).strip()
        if compose_logs:
            logs.append(compose_logs)
        return {
            "success": ret == 0,
            "message": "ネットワークを再設定し、コンテナを再起動しました" if ret == 0 else "ネットワーク再設定後のコンテナ再起動に失敗しました",
            "logs": "\n".join(logs)
        }
