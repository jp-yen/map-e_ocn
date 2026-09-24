# web-dashboard/config_app.py
"""
AppConfig : map-e.conf の読み込みと動的パラメータ提供。

  - reload()        : 設定ファイルを再読み込み
  - get()           : キーを文字列で取得
  - get_vlan_list() : VLAN リストを整数リストで取得
  - br_info         : BR の IPv4 / IPv6 アドレス
  - pppoe_vlans     : アクティブ PPPoE VLAN リスト
  - pppoe_servers   : PPPoE サーバーメタデータ
"""

import os
import sys

from constants import WORKSPACE_DIR



class AppConfig:
    """
    map-e.conf およびワークスペース内の設定ファイルを読み込み、
    ハードコード値を排除して動的な環境パラメータを提供する管理クラス。
    """

    def __init__(self):
        self._raw_conf = {}
        self.reload()

    def reload(self):
        """map-e.conf を探索して再読み込みを行う"""
        candidates = [
            os.path.join(WORKSPACE_DIR, "map-e.conf"),
            "/app/workspace/map-e.conf",
            "/app/map-e.conf",
            "./map-e.conf",
            "../map-e.conf"
        ]
        self._raw_conf = {}
        for path in candidates:
            if os.path.exists(path):
                self._raw_conf = self._parse_shell_conf(path)
                break

    @staticmethod
    def _parse_shell_conf(filepath):
        """Shell スタイルの KEY=VALUE 設定ファイルを解析し、変数展開を行う"""
        conf = {}
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        # 既存キーの変数展開 ${VAR}
                        for vk, vv in list(conf.items()):
                            v = v.replace(f"${{{vk}}}", vv)
                        conf[k] = v
        except Exception as e:
            print(f"[Config] Error reading {filepath}: {e}", file=sys.stderr)
        return conf

    def get(self, key, default=""):
        return self._raw_conf.get(key, default)

    def get_vlan_list(self, key, default_list=None):
        """空白区切りの VLAN 文字列を整数のリストとして返す"""
        val = self._raw_conf.get(key, "")
        if not val:
            return default_list or []
        vlans = []
        for item in val.split():
            try:
                vlans.append(int(item))
            except ValueError:
                pass
        return vlans if vlans else (default_list or [])

    @property
    def br_info(self):
        """BR の IPv6 アドレスと IPv4 アドレスを取得または合成して返す"""
        br_ipv4 = self.get("BR_IPV4_ADDR", "")
        br_prefix = self.get("BR_PREFIX", "")
        if not br_prefix:
            base_subnet = self.get("BASE_SUBNET", "")
            if base_subnet:
                br_prefix = f"{base_subnet}:3220"

        # OCN 仕様: BR IPv6 末尾は BR_IPV4 の 16進数 (例: 192.0.2.1 -> c000:0201)
        hex_suffix = ""
        if br_ipv4:
            try:
                octets = [int(x) for x in br_ipv4.split(".")]
                hex_suffix = f"{octets[0]:02x}{octets[1]:02x}:{octets[2]:02x}{octets[3]:02x}"
            except Exception:
                pass

        br_ipv6 = f"{br_prefix.rstrip(':')}::{hex_suffix}" if br_prefix and hex_suffix else ""
        return {
            "br_ipv6": br_ipv6,
            "br_ipv4": br_ipv4
        }

    @property
    def pppoe_vlans(self):
        """アクティブな PPPoE VLAN ID のリストを返す（MAP-E VLAN を合算）"""
        raw_pppoe = [v.strip() for v in self.get("PPPOE_VLANS", "").split() if v.strip()]
        vlan_set = set(raw_pppoe)
        for k in ["SLAAC_DYN_VLANS", "PD_DYN_VLANS", "SLAAC_FIX_VLANS", "PD_FIX_VLANS"]:
            for v in self.get(k, "").split():
                v = v.strip()
                if v and v.isdigit():
                    vlan_set.add(v)
        return sorted(list(vlan_set), key=lambda x: int(x) if x.isdigit() else x)

    @property
    def ipoe_vlans(self):
        """IPoE の各接続方式に割り当てられた VLAN 一覧を返す"""
        return {
            "slaac_dyn": [v.strip() for v in self.get("SLAAC_DYN_VLANS", "").split() if v.strip()],
            "pd_dyn": [v.strip() for v in self.get("PD_DYN_VLANS", "").split() if v.strip()],
            "slaac_fix": [v.strip() for v in self.get("SLAAC_FIX_VLANS", "").split() if v.strip()],
            "pd_fix": [v.strip() for v in self.get("PD_FIX_VLANS", "").split() if v.strip()],
        }

    @property
    def pppoe_servers(self):
        """map-e.conf に定義された PPPoE サーバー（サービス単位）のメタデータを返す"""
        servers = []

        # サービス一覧の取得 (PPPOE_SERVICES)
        srv_names = self.get("PPPOE_SERVICES", "").split()

        for s_name in srv_names:
            s_name = s_name.strip()
            if not s_name:
                continue
            vrf_enabled = self.get(f"PPPOE_VRF_ENABLED_{s_name}", "0") == "1"

            servers.append({
                "ac_name": s_name,
                "service_name": s_name,
                "vrf_enabled": vrf_enabled,
            })

        return servers
