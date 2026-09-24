# web-dashboard/config_validators.py
"""
設定バリデーションモジュール。
ステージングデータの整合性、IP/CIDR、MACアドレス、ホスト名の形式チェックを行う。
"""

import re
from typing import Dict, List, Any


def is_valid_ddns_hostname(val: Any) -> bool:
    """DDNS ホスト名が RFC 準拠の FQDN 形式であるか判定する"""
    if not val or not isinstance(val, str):
        return False
    val = val.strip().rstrip(".")
    if len(val) < 3 or len(val) > 253:
        return False
    if any(c in val for c in ["@", "_", " "]):
        return False
    parts = val.split(".")
    if len(parts) < 2:
        return False
    label_re = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")
    return all(label_re.match(p) for p in parts)


def validate_staging_config(staging: Dict[str, Any]) -> List[str]:
    """
    ステージング設定全体のバリデーションを行い、
    未入力や不整合のエラーメッセージのリストを返す。
    正常なら空リストを返す。
    """
    errors: List[str] = []
    if not staging or not isinstance(staging, dict):
        return ["ステージング設定データが存在しません。"]

    # 1. PPPoE 設定の検証
    pppoe = staging.get("pppoe", {})
    # サービス定義
    server_instances = pppoe.get("server_instances")
    if isinstance(server_instances, list):
        seen_svcs = set()
        for idx, s in enumerate(server_instances):
            raw_name = s.get("name", "").strip()
            name = re.sub(r"\s+", "_", raw_name)
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
    else:
        services_by_vlan = pppoe.get("services", {})
        for vlan, svcs in services_by_vlan.items():
            if not isinstance(svcs, list):
                continue
            seen_svcs = set()
            for idx, s in enumerate(svcs):
                raw_name = s.get("name", "").strip()
                name = re.sub(r"\s+", "_", raw_name)
                s["name"] = name
                row_num = idx + 1
                if not name:
                    continue
                if len(name) > 11:
                    errors.append(
                        f"PPPoE サービス定義 (VLAN {vlan}): サービス名「{name}」が {len(name)} 文字です。"
                        f"最大 11 文字以下にしてください（Linux VRF デバイス名 'vrf-<サービス名>' の 15 文字制限のため）。"
                    )
                elif not re.match(r"^[a-zA-Z0-9_-]+$", name):
                    errors.append(
                        f"PPPoE サービス定義 (VLAN {vlan}): サービス名「{name}」に使用できない不正な文字が含まれています。"
                        f"半角英数字、アンダースコア（_）、ハイフン（-）を使用してください（例: VPN_Group1）。"
                    )
                elif name.lower() in seen_svcs:
                    errors.append(f"PPPoE サービス定義 (VLAN {vlan}): サービス名「{name}」が重複しています。")
                else:
                    seen_svcs.add(name.lower())

    # ユーザー設定
    users_raw = pppoe.get("users", {})
    if isinstance(users_raw, list):
        users_by_vlan = {"common": users_raw}
    elif isinstance(users_raw, dict):
        users_by_vlan = users_raw
    else:
        users_by_vlan = {}

    for vlan, users in users_by_vlan.items():
        if not isinstance(users, list):
            continue
        seen_unames = set()
        for idx, u in enumerate(users):
            uname = u.get("username", "").strip()
            pwd = u.get("password", "").strip()
            ip = u.get("ip", "").strip()
            # サービス名の空白はアンダースコアに正規化
            raw_srv = u.get("service_name", u.get("ac_name", "*")).strip() or "*"
            norm_srv = re.sub(r"\s+", "_", raw_srv)
            u["service_name"] = norm_srv
            u["ac_name"] = norm_srv
            note = u.get("note", "").strip()
            ddns = u.get("ddns_v4", "").strip()
            row_num = idx + 1

            # すべて空欄の行は入力意図がないため読み飛ばす
            if not uname and not pwd and not ip and not note and not ddns:
                continue

            # IP 欄が '*' または空欄の場合はプール払い出し指定 -> バリデーションスキップ
            is_pool_user = (ip == '*' or ip == '')

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
                # IP/CIDR バリデーション: '-' (プール) はスキップ、それ以外は x.x.x.x または x.x.x.x/N (N: 24〜32)
                if not is_pool_user and ip:
                    ip_ok = False
                    ip_re = re.compile(
                        r'^(\d{1,3}\.){3}\d{1,3}(/([0-9]|[12]\d|3[012]))?$'
                    )
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
                                ip_ok = True  # プレフィックスなしは /32 扱い
                    if ip and not ip_ok and not errors:  # 形式エラー未登録の場合のみ
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
        slaac_static = staging.get("slaac", {}).get("static_ips", [])
        pd_static = staging.get("dhcp_pd", {}).get("static_ips", [])
        static_ips = (slaac_static or []) + (pd_static or [])

    if isinstance(static_ips, list):
        seen_macs = set()
        for idx, entry in enumerate(static_ips):
            mac = entry.get("mac", "").strip()
            ip = entry.get("ip", "").strip()
            note = entry.get("note", "").strip()
            row_num = idx + 1

            # すべて空欄の行は入力意図がないため読み飛ばす
            if not mac and not ip and not note:
                continue

            if not mac:
                errors.append(f"固定IP設定: {row_num}行目のVLAN・MACアドレスが未入力です。")
            elif mac.lower() in seen_macs:
                errors.append(f"固定IP設定: VLAN・MACアドレス「{mac}」が重複しています。")
            else:
                seen_macs.add(mac.lower())

    return errors
