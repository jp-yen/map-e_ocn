#!/usr/bin/env python3
# =============================================================================
# validate_option94.py — MAP-E Option 94 / Option 89 / Option 93 検証スクリプト
#
# ■ 目的
#   プロビジョニングサーバー (mape-provisioning-server) および
#   Kea DHCPv6 (gen_kea_configs.pl) が出力する Option 94 バイト列が、
#   コードの変更前後で変わっていないことを確認するリグレッションテスト。
#
# ■ 検証内容
#   テスト用設定ファイル (test.map-e.conf) を用いて
#   6 接続方式 × 30 VLAN（各方式 VLAN 101〜130, 131〜160, ...）について
#   Option 94 hex、Option 89 hex、Option 93 hex を独立計算し、
#   現在の gen_kea_configs.pl の実出力と突き合わせる。
#
#   【判定基準】
#   (1) Option 94 全体 hex が期待値と一致すること
#   (2) Option 89 内の ea-len が
#       - 動的方式（① SLAAC動的 / ② PD動的 / ⑤ HGW動的）: 0x12 (18bit)
#       - 固定方式（③ SLAAC固定 / ④ PD固定 / ⑥ HGW固定）: 0x00
#   (3) Option 89 内の prefix4-len が
#       - 動的方式: 0x14 (20bit = /20、/32 でないこと)
#       - 固定方式: 指定された固定IPv4のマスク長 (0x20=/32 または 0x1c=/28)
#   (4) Option 93 (PortParams) の psid-len が動的方式では 0x06 (6bit) であること
#       (1,008 ポート仕様)
#
# ■ 注意
#   このスクリプト自体は「チェック専用」であり、通常の実装ファイル
#   (gen_kea_configs.pl / mape_calc.tmpl 等) の修正指示には含まれません。
#   「チェック用のプログラムを変更する」と明示された指示でのみ変更可能です。
#
# ■ 使用方法
#   make validate             # Makefile から自動実行
#   python3 generator/validate_option94.py [--conf <設定ファイル>] [--verbose]
# =============================================================================

import argparse
import ipaddress
import os
import re
import shutil
import subprocess
import sys
import tempfile

# ---------------------------------------------------------------------------
# 定数定義
# ---------------------------------------------------------------------------
VLAN_START = 101    # 各接続方式の開始 VLAN
VLAN_COUNT = 30     # 各接続方式のテスト VLAN 数

# テスト用静的 IP マッピング（固定方式テスト用）
FIXED_MACS = {
    "00:00:5e:00:53:01": ("198.51.100.10", 32),
    "00:00:5e:00:53:02": ("198.51.100.20", 32),
    "00:00:5e:00:53:03": ("198.51.100.32", 28),
}

# ---------------------------------------------------------------------------
# Helper: hex utilities (Perl MapeCommon.pm の ipv6_to_hex / ipv4_to_hex と同等)
# ---------------------------------------------------------------------------

def ipv4_to_hex(ipv4_str):
    octets = [int(x) for x in ipv4_str.split(".")]
    return "".join(f"{o:02x}" for o in octets)

def ipv6_to_hex(ipv6_str):
    addr = ipaddress.IPv6Address(ipv6_str)
    return addr.packed.hex()

def calc_br_ipv6(br_prefix, br_ipv4_addr):
    octets = [int(x) for x in br_ipv4_addr.split(".")]
    hex_suffix = f"{octets[0]:02x}{octets[1]:02x}:{octets[2]:02x}{octets[3]:02x}"
    return f"{br_prefix}::{hex_suffix}"

# ---------------------------------------------------------------------------
# Helper: load config file
# ---------------------------------------------------------------------------

def load_conf(path):
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r'^(?:export\s+)?(\w+)\s*=\s*(.+)$', line)
            if not m:
                continue
            key, val = m.group(1), m.group(2)
            if (val.startswith('"') and '"' in val[1:]) or (val.startswith("'") and "'" in val[1:]):
                inner = val[1:]
                q = val[0]
                end = inner.find(q)
                val = inner[:end]
            else:
                val = re.sub(r'\s*#.*$', '', val).strip()
            env[key] = val
    for _ in range(5):
        changed = False
        for k in env:
            new_val = re.sub(r'\$\{(\w+)\}', lambda m: env.get(m.group(1), m.group(0)), env[k])
            if new_val != env[k]:
                env[k] = new_val
                changed = True
        if not changed:
            break
    return env

def derive_vars(env):
    e = dict(env)
    pool_raw = e.get("BR_IPV4_POOL", "")
    if "/" in pool_raw:
        pool_ip, mask = pool_raw.split("/", 1)
        e["BR_IPV4_POOL"] = pool_ip
        e["BR_IPV4_MASK"] = mask
    else:
        e.setdefault("BR_IPV4_MASK", "16")
    if not e.get("BR_IPV6") and e.get("BR_PREFIX") and e.get("BR_IPV4_ADDR"):
        e["BR_IPV6"] = calc_br_ipv6(e["BR_PREFIX"], e["BR_IPV4_ADDR"])
    if e.get("BASE_SUBNET") and e.get("PD_POOL") and "MAPE_PD_PREFIX" not in e:
        e["MAPE_PD_PREFIX"] = f"{e['BASE_SUBNET']}:{e['PD_POOL']}:"
    if e.get("BASE_SUBNET") and e.get("PD_FIX_POOL") and "MAPE_PD_FIX_PREFIX" not in e:
        e["MAPE_PD_FIX_PREFIX"] = f"{e['BASE_SUBNET']}:{e['PD_FIX_POOL']}:"
    if e.get("BASE_SUBNET") and "MAPE_HGW_PREFIX" not in e:
        pfx = e.get("HGW_BR_BASE", "5000")
        e["MAPE_HGW_PREFIX"] = f"{e['BASE_SUBNET']}:{pfx}:"
    if e.get("BASE_SUBNET") and "MAPE_HGW_FIX_PREFIX" not in e:
        pfx = e.get("HGW_FIX_BR_BASE", "6000")
        e["MAPE_HGW_FIX_PREFIX"] = f"{e['BASE_SUBNET']}:{pfx}:"
    return e

# ---------------------------------------------------------------------------
# 期待値計算：動的方式 Option 94
# (gen_kea_configs.pl _build_slaac_flex_option / _build_pd_flex_option と同等)
# ---------------------------------------------------------------------------

def calc_dyn_option94(br_ipv6, pool_ip, base_subnet, base_hex, vlan):
    """
    動的接続方式の Option 94 期待値を計算して返す。
    base_hex: SLAAC_BR_BASE, PD_POOL, HGW_BR_BASE のいずれか (十進数文字列)
    """
    br_hex = ipv6_to_hex(br_ipv6)

    # 全体プール(/16)から VLAN 向けに /20 を切り出す (4 bytes 固定長: RFC 7598)
    pool_octets = pool_ip.split(".")
    pool_upper_hex = f"{int(pool_octets[0]):02x}{int(pool_octets[1]):02x}"
    v4_sub_idx = (vlan - 1) % 16
    octet2 = (v4_sub_idx * 16) & 0xf0
    pool_v4_32_hex = f"{pool_upper_hex}{octet2:02x}00"

    # Rule IPv6 /38 (5 bytes = 10 hex chars)
    raw_hex = ipv6_to_hex(f"{base_subnet}:{base_hex}::")
    v6_prefix_bytes = bytearray.fromhex(raw_hex[:10])
    v6_prefix_bytes[4] &= 0xfc   # /38: 末尾2bit をゼロにマスク
    v6_prefix_hex = v6_prefix_bytes.hex()

    # PSID (1..63) と Option 93
    psid = ((vlan - 1) % 63) + 1
    psid_hex = f"{psid << 10:04x}"
    opt93_hex = f"005d00040606{psid_hex}"

    # Option 89: flags(1B)+ea-len(1B)+v4-len(1B)+ipv4-pfx(4B)+v6-len(1B)+v6-pfx(5B)+opt93(8B) = 21B (0x0015)
    opt89_hex = f"00590015001214{pool_v4_32_hex}26{v6_prefix_hex}{opt93_hex}"
    opt94_hex = f"005a0010{br_hex}{opt89_hex}"

    return {
        "opt94": opt94_hex,
        "opt90": f"005a0010{br_hex}",
        "opt89": opt89_hex,
        "opt93": opt93_hex,
        "ea_len": 18,
        "prefix4_len": 20,
        "psid_len": 6,
        "psid": psid,
    }

# ---------------------------------------------------------------------------
# 期待値計算：固定方式 Option 94
# (gen_kea_configs.pl calculate_option94_fixed_hex と同等)
# ---------------------------------------------------------------------------

def calc_fix_option94(br_ipv6, base_subnet, mac_ip, mac_mask,
                      mode, vlan=0, slaac_fix_br_prefix="3000",
                      pd_fix_pool="4000", hgw_fix_br_base="6000"):
    br_hex = ipv6_to_hex(br_ipv6)
    flags = "00"
    ea_len_hex = "00"
    v4_len_hex = f"{mac_mask:02x}"
    v4_hex = ipv4_to_hex(mac_ip)

    if mode == "slaac_fix":
        # Perl: my $hextet = hex($slaac_fix_pfx) + $vlan;  <- hex() interprets as hex string
        hextet = int(slaac_fix_br_prefix, 16) + vlan
        prefix = f"{base_subnet}:{hextet:x}::"
    elif mode == "hgw_fix":
        # Perl: my $hextet = hex($hgw_fix_pfx) + $vlan;   <- hex() interprets as hex string
        hextet = int(hgw_fix_br_base, 16) + vlan
        prefix = f"{base_subnet}:{hextet:x}::"
    else:  # pd_fix
        prefix = f"{base_subnet}:{pd_fix_pool}::"

    v6_prefix_hex = ipv6_to_hex(prefix)[:14]  # 56bit = 7 bytes = 14 hex chars (0x38)
    rule_payload = f"{flags}{ea_len_hex}{v4_len_hex}{v4_hex}38{v6_prefix_hex}"
    rule_len = len(rule_payload) // 2
    rule_len_hex = f"{rule_len:04x}"
    opt89_hex = f"0059{rule_len_hex}{rule_payload}"
    opt94_hex = f"005a0010{br_hex}{opt89_hex}"

    return {
        "opt94": opt94_hex,
        "opt90": f"005a0010{br_hex}",
        "opt89": opt89_hex,
        "opt93": None,
        "ea_len": 0,
        "prefix4_len": mac_mask,
        "psid_len": 0,
        "psid": 0,
    }

# ---------------------------------------------------------------------------
# Option 94 hex のパース（検証用）
# ---------------------------------------------------------------------------

def parse_opt94(hex_str):
    """Option 94 hex を解析して各フィールドを返す"""
    s = hex_str.lower()
    idx = 0

    def read(n_bytes):
        nonlocal idx
        chunk = s[idx:idx + n_bytes * 2]
        idx += n_bytes * 2
        return chunk

    result = {}

    # Option 90 (BR)
    result["opt90_code"] = int(read(2), 16)
    result["opt90_len"] = int(read(2), 16)
    br_hex = read(result["opt90_len"])
    try:
        result["br_ipv6"] = str(ipaddress.IPv6Address(bytes.fromhex(br_hex)))
    except Exception:
        result["br_ipv6"] = br_hex

    # Option 89 (Rule)
    result["opt89_code"] = int(read(2), 16)
    result["opt89_len"] = int(read(2), 16)
    opt89_payload_start = idx
    result["flags"] = int(read(1), 16)
    result["ea_len"] = int(read(1), 16)
    result["prefix4_len"] = int(read(1), 16)
    result["ipv4_prefix_hex"] = read(4)    # RFC 7598: 固定4バイト
    try:
        result["ipv4_prefix"] = str(ipaddress.IPv4Address(bytes.fromhex(result["ipv4_prefix_hex"])))
    except Exception:
        result["ipv4_prefix"] = result["ipv4_prefix_hex"]
    result["prefix6_len"] = int(read(1), 16)
    v6_pfx_bytes = (result["prefix6_len"] + 7) // 8
    result["ipv6_prefix_hex"] = read(v6_pfx_bytes)

    consumed = (idx - opt89_payload_start) // 2
    remaining = result["opt89_len"] - consumed
    result["is_dynamic"] = remaining >= 8

    if result["is_dynamic"]:
        result["opt93_code"] = int(read(2), 16)
        result["opt93_len"] = int(read(2), 16)
        result["psid_offset"] = int(read(1), 16)
        result["psid_len"] = int(read(1), 16)
        result["psid_raw"] = read(2)
        result["psid"] = int(result["psid_raw"], 16) >> 10
    else:
        result["opt93_code"] = None
        result["psid_offset"] = 0
        result["psid_len"] = 0
        result["psid"] = 0

    return result

# ---------------------------------------------------------------------------
# gen_kea_configs.pl の実出力から Option 94 を抽出
# ---------------------------------------------------------------------------

def extract_kea_opt94_from_conf(conf_file):
    """kea-dhcp6.conf から flex-option の code:94 supersede 値を client-class 名ごとに返す"""
    with open(conf_file) as f:
        content = f.read()

    result = {}
    # "supersede" が "client-class" より先に来るパターン
    pattern = re.compile(
        r'"supersede"\s*:\s*"(0x[0-9a-fA-F]+)"[^}]{0,300}?"client-class"\s*:\s*"([^"]+)"',
        re.DOTALL
    )
    for m in pattern.finditer(content):
        supersede, cls_name = m.group(1), m.group(2)
        result[cls_name] = supersede.lower().replace("0x", "")

    # "client-class" が "supersede" より先に来るパターン
    pattern2 = re.compile(
        r'"client-class"\s*:\s*"([^"]+)"[^}]{0,300}?"supersede"\s*:\s*"(0x[0-9a-fA-F]+)"',
        re.DOTALL
    )
    for m in pattern2.finditer(content):
        cls_name, supersede = m.group(1), m.group(2)
        if cls_name not in result:
            result[cls_name] = supersede.lower().replace("0x", "")

    return result


# ---------------------------------------------------------------------------
# テスト用 map-e.conf の生成
# ---------------------------------------------------------------------------

def generate_test_conf(base_conf, output_path):
    """
    本番の map-e.conf を読み込み、テスト用に VLAN を上書きして書き出す。
    ① 101-130, ② 131-160, ③ 161-190, ④ 191-220, ⑤ 221-250, ⑥ 251-280
    """
    env = load_conf(base_conf)
    env = derive_vars(env)

    vlan_start = VLAN_START
    env["SLAAC_DYN_VLANS"] = " ".join(str(v) for v in range(vlan_start, vlan_start + VLAN_COUNT))
    env["PD_DYN_VLANS"] = " ".join(str(v) for v in range(vlan_start + VLAN_COUNT, vlan_start + 2 * VLAN_COUNT))
    env["SLAAC_FIX_VLANS"] = " ".join(str(v) for v in range(vlan_start + 2 * VLAN_COUNT, vlan_start + 3 * VLAN_COUNT))
    env["PD_FIX_VLANS"] = " ".join(str(v) for v in range(vlan_start + 3 * VLAN_COUNT, vlan_start + 4 * VLAN_COUNT))
    env["HGW_VLANS"] = " ".join(str(v) for v in range(vlan_start + 4 * VLAN_COUNT, vlan_start + 5 * VLAN_COUNT))
    env["HGW_FIX_VLANS"] = " ".join(str(v) for v in range(vlan_start + 5 * VLAN_COUNT, vlan_start + 6 * VLAN_COUNT))
    env["PPPOE_VLANS"] = ""

    with open(output_path, "w") as f:
        f.write("# AUTO-GENERATED by validate_option94.py — DO NOT EDIT\n")
        for k, v in env.items():
            safe_v = str(v).replace('"', '\\"')
            f.write(f'{k}="{safe_v}"\n')

    return env

# ---------------------------------------------------------------------------
# VLAN 固有 reservation 抽出ヘルパー (validate_fixed の内部用)
# ---------------------------------------------------------------------------

def _extract_reservation_for_vlan(content, env, mode, vlan, mac):
    """
    kea-dhcp6.conf の文字列から、指定 mode/VLAN/MAC の opt94 hex を返す。

    固定方式の subnet アドレスは calc_vlan_prefix と同じ算術
    (BASE は 10 進整数として加算) で決定される:
      SLAAC固定: base_subnet:{int(SLAAC_FIX_BR_PREFIX) + vlan}::/64
      HGW固定  : base_subnet:{int(HGW_FIX_BR_BASE)     + vlan}::/64
      PD固定   : base_subnet:{int(PD_FIX_POOL)          + vlan}::/64
    """
    base_subnet = env.get("BASE_SUBNET", "")
    if mode == "slaac_fix":
        base = int(env.get("SLAAC_FIX_BR_PREFIX", "3000"))
    elif mode == "hgw_fix":
        base = int(env.get("HGW_FIX_BR_BASE", "6000"))
    elif mode == "pd_fix":
        base = int(env.get("PD_FIX_POOL", "4000"))
    else:
        return None

    hextet = base + vlan
    target_subnet = f"{base_subnet}:{hextet}::/64"

    subnet_pos = content.find(f'"subnet": "{target_subnet}"')
    if subnet_pos == -1:
        return None

    next_subnet = content.find('"subnet":', subnet_pos + len(target_subnet) + 10)
    search_block = content[subnet_pos: next_subnet] if next_subnet != -1 else content[subnet_pos:]

    m = re.search(
        r'"hw-address"\s*:\s*"' + re.escape(mac) + r'".*?"code"\s*:\s*94.*?"data"\s*:\s*"([^"]+)"',
        search_block,
        re.DOTALL | re.IGNORECASE,
    )
    return m.group(1).lower() if m else None

# ---------------------------------------------------------------------------
# 接続方式別セクション定義 (Validator.run() から参照)
#   _DYN_SECTIONS: (mode, section_num, label, env_base_key, vlan_range_offset)
#   _FIX_SECTIONS: (mode, section_num, label, vlan_range_offset)
#   vlan_range_offset: 0=SLAAC動的, 1=PD動的, 2=SLAAC固定,
#                      3=PD固定, 4=HGW動的, 5=HGW固定
# ---------------------------------------------------------------------------

_DYN_SECTIONS = (
    ("slaac_dyn", "1", "SLAAC動的 ①", "SLAAC_BR_BASE", 0),
    ("pd_dyn",    "2", "PD動的 ②",    "PD_POOL",       1),
    ("hgw_dyn",   "5", "HGW動的 ⑤",  "HGW_BR_BASE",   4),
)
_FIX_SECTIONS = (
    ("slaac_fix", "3", "SLAAC固定 ③", 2),
    ("pd_fix",    "4", "PD固定 ④",    3),
    ("hgw_fix",   "6", "HGW固定 ⑥",  5),
)

# ---------------------------------------------------------------------------
# メイン検証ロジック
# ---------------------------------------------------------------------------

PASS_MARK = "PASS"
FAIL_MARK = "FAIL"

class Validator:
    def __init__(self, env, verbose=False):
        self.env = env
        self.verbose = verbose
        self.errors = []
        self.passed = 0
        self.total = 0

    def _check(self, name, cond, detail=""):
        self.total += 1
        if cond:
            self.passed += 1
            if self.verbose:
                print(f"  [PASS] {name}")
        else:
            msg = f"[FAIL] {name}"
            if detail:
                msg += f"\n         -> {detail}"
            self.errors.append(msg)
            print(f"  {msg}")

    def validate_dynamic(self, mode, base_key, vlans, kea_opt94):
        base = self.env.get(base_key, "")
        br_ipv6 = self.env["BR_IPV6"]
        pool_ip = self.env["BR_IPV4_POOL"]
        base_subnet = self.env["BASE_SUBNET"]
        class_prefix = {
            "slaac_dyn": "class-slaac-dyn",
            "pd_dyn": "class-pd-dyn",
            "hgw_dyn": "class-hgw-dyn",
        }[mode]

        print(f"\n  [{mode}] base_key={base_key}={base}  VLANs={vlans[0]}..{vlans[-1]}")

        for vlan in vlans:
            expected = calc_dyn_option94(br_ipv6, pool_ip, base_subnet, base, vlan)
            class_name = f"{class_prefix}-{vlan}"
            actual_hex = kea_opt94.get(class_name)

            if actual_hex is not None:
                self._check(
                    f"VLAN{vlan} opt94 match",
                    actual_hex == expected["opt94"],
                    f"expected={expected['opt94']}\n           actual  ={actual_hex}"
                )
            else:
                self._check(
                    f"VLAN{vlan} opt94 present",
                    False,
                    f"class '{class_name}' not found in kea conf"
                )
                continue

            parsed = parse_opt94(actual_hex)

            # ea-len = 18bit (動的)
            self._check(
                f"VLAN{vlan} ea-len=18",
                parsed["ea_len"] == 18,
                f"got ea_len={parsed['ea_len']}"
            )

            # prefix4-len = 20 (/20、/32 でないこと)
            self._check(
                f"VLAN{vlan} prefix4-len=20 (not /32)",
                parsed["prefix4_len"] == 20,
                f"got prefix4_len={parsed['prefix4_len']}"
            )

            # psid-len = 6bit (1,008 ポート)
            self._check(
                f"VLAN{vlan} psid-len=6",
                parsed["psid_len"] == 6,
                f"got psid_len={parsed['psid_len']}"
            )

            # PSID 値 = ((vlan-1) % 63) + 1
            expected_psid = ((vlan - 1) % 63) + 1
            self._check(
                f"VLAN{vlan} psid={expected_psid}",
                parsed["psid"] == expected_psid,
                f"got psid={parsed['psid']}, expected {expected_psid}"
            )

            # Option 93 が Option 89 内に内包されていること
            self._check(
                f"VLAN{vlan} opt93 encapsulated in opt89",
                parsed["is_dynamic"],
                "Option 93 not found inside Option 89 payload"
            )

    def validate_fixed(self, mode, vlans, kea_conf_content):
        """固定方式の Option 94 検証。

        SLAAC固定・HGW固定は VLAN ごとに IPv6 prefix が異なるため、
        各 VLAN に対応する subnet ブロック内の reservation を個別に参照する。
        PD固定は opt94 が VLAN 非依存のため先頭 VLAN のみ参照する。
        """
        br_ipv6 = self.env["BR_IPV6"]
        base_subnet = self.env["BASE_SUBNET"]
        slaac_fix_br_prefix = self.env.get("SLAAC_FIX_BR_PREFIX", "3000")
        pd_fix_pool = self.env.get("PD_FIX_POOL", "4000")
        hgw_fix_br_base = self.env.get("HGW_FIX_BR_BASE", "6000")

        print(f"\n  [{mode}] VLANs={vlans[0]}..{vlans[-1]}")

        for mac, (ip, mask) in FIXED_MACS.items():
            # SLAAC固定・HGW固定: VLAN ごとに IPv6 prefix が変わるため先頭 3 VLAN 分チェック
            # PD固定: opt94 が VLAN 非依存のため 1 VLAN のみ
            check_vlans = vlans[:3] if mode != "pd_fix" else [vlans[0]]
            for vlan in check_vlans:
                expected = calc_fix_option94(
                    br_ipv6, base_subnet, ip, mask, mode, vlan,
                    slaac_fix_br_prefix=slaac_fix_br_prefix,
                    pd_fix_pool=pd_fix_pool,
                    hgw_fix_br_base=hgw_fix_br_base,
                )

                # VLAN に対応する subnet ブロック内の reservation を取得
                actual_hex = _extract_reservation_for_vlan(
                    kea_conf_content, self.env, mode, vlan, mac
                )

                if actual_hex is None:
                    if self.verbose:
                        print(f"     (skip: mac={mac} VLAN{vlan} not in kea reservations for mode={mode})")
                    continue

                # (1) Option 94 全体一致
                self._check(
                    f"{mode} VLAN{vlan} mac={mac} opt94 match",
                    actual_hex == expected["opt94"],
                    f"expected={expected['opt94']}\n           actual  ={actual_hex}"
                )

                parsed = parse_opt94(actual_hex)

                # (2) ea-len = 0 (固定)
                self._check(
                    f"{mode} VLAN{vlan} mac={mac} ea-len=0",
                    parsed["ea_len"] == 0,
                    f"got ea_len={parsed['ea_len']}"
                )

                # (3) prefix4-len = 指定マスク長
                self._check(
                    f"{mode} VLAN{vlan} mac={mac} prefix4-len={mask}",
                    parsed["prefix4_len"] == mask,
                    f"got prefix4_len={parsed['prefix4_len']}"
                )

    def run(self, kea_conf):
        print()
        print("=" * 70)
        print("  MAP-E Option 94 / Option 89 / Option 93 Regression Test")
        print("=" * 70)

        with open(kea_conf) as _f:
            kea_conf_content = _f.read()

        kea_opt94 = extract_kea_opt94_from_conf(kea_conf)

        # VLAN レンジを方式番号順 (①〜⑥) で生成
        vlan_ranges = [
            list(range(VLAN_START + i * VLAN_COUNT, VLAN_START + (i + 1) * VLAN_COUNT))
            for i in range(6)
        ]

        for mode, num, label, base_key, offset in _DYN_SECTIONS:
            print(f"\n[{num}] {label}")
            self.validate_dynamic(mode, base_key, vlan_ranges[offset], kea_opt94)

        for mode, num, label, offset in _FIX_SECTIONS:
            print(f"\n[{num}] {label}")
            self.validate_fixed(mode, vlan_ranges[offset], kea_conf_content)

        print()
        print("=" * 70)
        if self.errors:
            print(f"  RESULT: FAILED  ({len(self.errors)} failures / {self.total} checks)")
            print("=" * 70)
            for e in self.errors:
                print(f"  {e}")
            print("=" * 70)
            return False
        else:
            print(f"  RESULT: ALL PASSED ({self.passed} / {self.total} checks)")
            print("=" * 70)
            return True


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="MAP-E Option 94 / Option 89 / Option 93 regression test"
    )
    parser.add_argument(
        "--conf", default=None,
        help="map-e.conf のパス (デフォルト: スクリプト親ディレクトリの map-e.conf)"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="PASS も含めて全チェック結果を表示"
    )
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)

    base_conf = args.conf or os.path.join(repo_root, "map-e.conf")
    if not os.path.exists(base_conf):
        print(f"[ERROR] map-e.conf が見つかりません: {base_conf}", file=sys.stderr)
        sys.exit(2)

    static_ip_conf = os.path.join(repo_root, "map-e-static-ip.conf")
    static_ip_conf_tmpl = os.path.join(repo_root, "map-e-static-ip.conf.tmpl")

    with tempfile.TemporaryDirectory(prefix="mape_validate_") as tmpdir:
        # ① テスト用 map-e.conf を tmpdir/map-e.conf に書き出す
        #
        #    gen_kea_configs.pl は先頭で
        #      check_config("$script_dir/../map-e.conf")
        #    を呼んでおり、$script_dir は __FILE__ の dirname で決まる。
        #    perl $tmpdir/generator/gen_kea_configs.pl のように
        #    tmpdir 経由のパスで呼ぶと __FILE__ = $tmpdir/generator/...
        #    → $script_dir/../map-e.conf = $tmpdir/map-e.conf となり、
        #    テスト用 conf が正しく読まれる。
        test_conf = os.path.join(tmpdir, "map-e.conf")
        env = generate_test_conf(base_conf, test_conf)

        # ② static-ip.conf をコピー (存在すれば本番、なければテンプレート)
        src_static = static_ip_conf if os.path.exists(static_ip_conf) else static_ip_conf_tmpl
        shutil.copy(src_static, os.path.join(tmpdir, "map-e-static-ip.conf"))

        # ③ generator/ を実コピー、kea-dhcp6/ はシンボリックリンク
        #
        #    【注意】generator/ はシンボリックリンクでは NG。
        #    Perl の __FILE__ はシンボリックリンクを解決した実パスを返すため、
        #    symlink($script_dir, tmpdir/generator) にすると
        #    $script_dir/../map-e.conf = repo_root/map-e.conf (本番) を読んでしまう。
        #    copytree() で実コピーすることで __FILE__ = tmpdir/generator/gen_kea_configs.pl
        #    → $script_dir/../map-e.conf = tmpdir/map-e.conf (テスト用) が正しく読まれる。
        shutil.copytree(script_dir, os.path.join(tmpdir, "generator"))
        os.symlink(
            os.path.join(repo_root, "kea-dhcp6"),
            os.path.join(tmpdir, "kea-dhcp6"),
        )

        # ④ gen_kea_configs.pl を tmpdir/generator 経由で実行
        gen_script_via_tmp = os.path.join(tmpdir, "generator", "gen_kea_configs.pl")
        env_for_perl = dict(os.environ)
        env_for_perl.setdefault("KEA_HOOKS_DIR", "/usr/lib/kea/hooks")
        # VLAN 環境変数は Perl が tmpdir/map-e.conf から読み取るため個別指定不要

        try:
            result = subprocess.run(
                ["perl", gen_script_via_tmp, "dhcp6"],
                capture_output=True, text=True,
                env=env_for_perl,
                cwd=tmpdir,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            print("[ERROR] gen_kea_configs.pl がタイムアウトしました (60s)", file=sys.stderr)
            sys.exit(2)
        except FileNotFoundError:
            print("[ERROR] perl が見つかりません", file=sys.stderr)
            sys.exit(2)

        if result.returncode != 0:
            print(f"[ERROR] gen_kea_configs.pl の実行に失敗しました:\n{result.stderr}", file=sys.stderr)
            if args.verbose:
                print(f"STDOUT:\n{result.stdout[:2000]}", file=sys.stderr)
            sys.exit(2)

        test_kea_conf = os.path.join(tmpdir, "kea-dhcp6.conf")
        with open(test_kea_conf, "w") as f:
            f.write(result.stdout)

        validator = Validator(env, verbose=args.verbose)
        ok = validator.run(test_kea_conf)

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
