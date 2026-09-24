# web-dashboard/host_syncer.py
"""
ホストネットワーク同期モジュール。
ホスト OS 上の VLAN サブインターフェース、PPPoE VRF デバイス、dummy デバイス、
および interfaces / sysctl 設定ファイルの同期・適用を担う。
"""

import os
import re
import ipaddress
from typing import Dict, List, Any

from utils import run_cmd


def _posix_cksum(s: str) -> int:
    """POSIX cksum (CRC-32) を計算し、シェルコマンド cksum と完全に同一の値を返す"""
    crctab = [
        0x00000000, 0x04c11db7, 0x09823b6e, 0x0d4326d9, 0x130476dc, 0x17c56b6b, 0x1a864db2, 0x1e475005,
        0x2608edb8, 0x22c9f00f, 0x2f8ad6d6, 0x2b4bcb61, 0x350c9b64, 0x31cd86d3, 0x3c8ea00a, 0x384fbdbd,
        0x4c11db70, 0x48d0c6c7, 0x4593e01e, 0x4152fda9, 0x5f15adac, 0x5bd4b01b, 0x569796c2, 0x52568b75,
        0x6a1936c8, 0x6ed82b7f, 0x639b0da6, 0x675a1011, 0x791d4014, 0x7ddc5da3, 0x709f7b7a, 0x745e66cd,
        0x9823b6e0, 0x9ce2ab57, 0x91a18d8e, 0x95609039, 0x8b27c03c, 0x8fe6dd8b, 0x82a5fb52, 0x8664e6e5,
        0xbe2b5b58, 0xbaea46ef, 0xb7a96036, 0xb3687d81, 0xad2f2d84, 0xa9ee3033, 0xa4ad16ea, 0xa06c0b5d,
        0xd4326d90, 0xd0f37027, 0xddb056fe, 0xd9714b49, 0xc7361b4c, 0xc3f706fb, 0xceb42022, 0xca753d95,
        0xf23a8028, 0xf6fb9d9f, 0xfbb8bb46, 0xff79a6f1, 0xe13ef6f4, 0xe5ffeb43, 0xe8bccd9a, 0xec7dd02d,
        0x34867077, 0x30476dc0, 0x3d044b19, 0x39c556ae, 0x278206ab, 0x23431b1c, 0x2e003dc5, 0x2ac12072,
        0x128e9dcf, 0x164f8078, 0x1b0ca6a1, 0x1fcdbb16, 0x018aeb13, 0x054bf6a4, 0x0808d07d, 0x0cc9cdca,
        0x7897ab07, 0x7c56b6b0, 0x71159069, 0x75d48dde, 0x6b93dddb, 0x6f52c06c, 0x6211e6b5, 0x66d0fb02,
        0x5e9f46bf, 0x5a5e5b08, 0x571d7dd1, 0x53dc6066, 0x4d9b3063, 0x495a2dd4, 0x44190b0d, 0x40d816ba,
        0xaca5c697, 0xa864db20, 0xa527fdf9, 0xa1e6e04e, 0xbfa1b04b, 0xbb60adfc, 0xb6238b25, 0xb2e29692,
        0x8aad2b2f, 0x8e6c3698, 0x832f1041, 0x87ee0df6, 0x99a95df3, 0x9d684044, 0x902b669d, 0x94ea7b2a,
        0xe0b41de7, 0xe4750050, 0xe9362689, 0xedf73b3e, 0xf3b06b3b, 0xf771768c, 0xfa325055, 0xfef34de2,
        0xc6bcf05f, 0xc27dede8, 0xcf3ecb31, 0xcbffd686, 0xd5b88683, 0xd1799b34, 0xdc3abded, 0xd8fba05a,
        0x690ce0ee, 0x6dcdfd59, 0x608edb80, 0x644fc637, 0x7a089632, 0x7ec98b85, 0x738aad5c, 0x774bb0eb,
        0x4f040d56, 0x4bc510e1, 0x46863638, 0x42472b8f, 0x5c007b8a, 0x58c1663d, 0x558240e4, 0x51435d53,
        0x251d3b9e, 0x21dc2629, 0x2c9f00f0, 0x285e1d47, 0x36194d42, 0x32d850f5, 0x3f9b762c, 0x3b5a6b9b,
        0x0315d626, 0x07d4cb91, 0x0a97ed48, 0x0e56f0ff, 0x1011a0fa, 0x14d0bd4d, 0x19939b94, 0x1d528623,
        0xf12f560e, 0xf5ee4bb9, 0xf8ad6d60, 0xfc6c70d7, 0xe22b20d2, 0xe6ea3d65, 0xeba91bbc, 0xef68060b,
        0xd727bbb6, 0xd3e6a601, 0xdea580d8, 0xda649d6f, 0xc423cd6a, 0xc0e2d0dd, 0xcda1f604, 0xc960ebb3,
        0xbd3e8d7e, 0xb9ff90c9, 0xb4bcb610, 0xb07daba7, 0xae3afba2, 0xaafbe615, 0xa7b8c0cc, 0xa379dd7b,
        0x9b3660c6, 0x9ff77d71, 0x92b45ba8, 0x9675461f, 0x8832161a, 0x8cf30bad, 0x81b02d74, 0x857130c3,
        0x5d8a9099, 0x594b8d2e, 0x5408abf7, 0x50c9b640, 0x4e8ee645, 0x4a4ffbf2, 0x470cdd2b, 0x43cdc09c,
        0x7b827d21, 0x7f436096, 0x7200464f, 0x76c15bf8, 0x68860bfd, 0x6c47164a, 0x61043093, 0x65c52d24,
        0x119b4be9, 0x155a565e, 0x18197087, 0x1cd86d30, 0x029f3d35, 0x065e2082, 0x0b1d065b, 0x0fdc1bec,
        0x3793a651, 0x3352bbe6, 0x3e119d3f, 0x3ad08088, 0x2497d08d, 0x2056cd3a, 0x2d15ebe3, 0x29d4f654,
        0xc5a92679, 0xc1683bce, 0xcc2b1d17, 0xc8ea00a0, 0xd6ad50a5, 0xd26c4d12, 0xdf2f6bcb, 0xdbee767c,
        0xe3a1cbc1, 0xe760d676, 0xea23f0af, 0xeee2ed18, 0xf0a5bd1d, 0xf464a0aa, 0xf9278673, 0xfde69bc4,
        0x89b8fd09, 0x8d79e0be, 0x803ac667, 0x84fbdbd0, 0x9abc8bd5, 0x9e7d9662, 0x933eb0bb, 0x97ffad0c,
        0xafb010b1, 0xab710d06, 0xa6322bdf, 0xa2f33668, 0xbcb4666d, 0xb8757bda, 0xb5365d03, 0xb1f740b4
    ]
    data = s.encode("utf-8")
    r = 0
    for b in data:
        r = ((r << 8) & 0xffffffff) ^ crctab[((r >> 24) ^ b) & 0xff]
    length = len(data)
    while length > 0:
        c = length & 0xff
        length >>= 8
        r = ((r << 8) & 0xffffffff) ^ crctab[((r >> 24) ^ c) & 0xff]
    return ~r & 0xffffffff


def sync_pppoe_vrf(staging: Dict[str, Any], logs: List[str]) -> None:
    """
    PPPoE サービスの VRF 閉域分離を ホスト上に同期する。
    - vrf_enabled=True  → VRF デバイス vrf-pppoe-<SRV> を作成 (なければ) し UP にする
    - vrf_enabled=False → VRF デバイスが存在すれば削除する
    ルーティングテーブル ID は POSIX cksum から自動決定 (system/vrf-setup.sh, pppoe/ip-up と完全一致)。
    """
    # カーネルモジュールをロード (失敗しても続行)
    run_cmd(["modprobe", "vrf"], privileged=True)

    wanted_vrfs = set()  # 必要な VRF デバイス名セット

    # サービス一覧と VRF 設定の取得 (staging.pppoe.server_instances 優先)
    server_instances = staging.get("pppoe", {}).get("server_instances")
    services_to_sync: List[tuple] = []

    if isinstance(server_instances, list) and len(server_instances) > 0:
        for inst in server_instances:
            s_name = (inst.get("name") or "").strip()
            if s_name:
                services_to_sync.append((s_name, bool(inst.get("vrf_enabled"))))
    else:
        low_layer = staging.get("low_layer", staging.get("lowlayer", {}))
        services_str = low_layer.get("PPPOE_SERVICES", "").strip()
        all_services = [s.strip() for s in services_str.split() if s.strip()]
        for srv in all_services:
            vrf_key = f"PPPOE_VRF_ENABLED_{srv}"
            is_vrf = low_layer.get(vrf_key, "0").strip() == "1"
            services_to_sync.append((srv, is_vrf))

    for srv, is_vrf in services_to_sync:
        if not is_vrf:
            continue
        s_name = re.sub(r"\s+", "_", srv)[:11]
        vrf_name = f"vrf-{s_name}"
        # テーブルIDをサービス名の POSIX cksum から決定 (1001 - 9999 の範囲: ip-up / vrf-setup.sh と完全同一)
        table_id = 1001 + (_posix_cksum(srv) % 8999)
        wanted_vrfs.add(vrf_name)
        # VRF が存在するか確認
        ret_chk, _, _ = run_cmd(["ip", "link", "show", vrf_name], privileged=True)
        if ret_chk != 0:
            # 新規作成
            ret_add, _, err_add = run_cmd(
                ["ip", "link", "add", vrf_name, "type", "vrf", "table", str(table_id)],
                privileged=True
            )
            if ret_add == 0:
                logs.append(f"✔ VRF 作成: {vrf_name} (table {table_id})")
            else:
                logs.append(f"[WARN] VRF 作成失敗 {vrf_name}: {err_add.strip()}")
        # UP にする
        run_cmd(["ip", "link", "set", vrf_name, "up"], privileged=True)
        # 外部への漏洩防止 (閉域閉じ込め): テーブル未登録宛先はメインテーブルへフォールスルーさせず即時破棄
        run_cmd(["ip", "route", "replace", "unreachable", "default", "table", str(table_id)], privileged=True)

    # 不要になった VRF デバイス (vrf-*, 旧 vrf-pppoe-*) を削除
    ret_ls, out_ls, _ = run_cmd(["ip", "-o", "link", "show", "type", "vrf"], privileged=True)
    if ret_ls == 0:
        for line in out_ls.splitlines():
            m = re.search(r":\s+(vrf-\S+)@", line)
            if not m:
                m = re.search(r":\s+(vrf-[^:@\s]+)", line)
            if m:
                existing_vrf = m.group(1).rstrip(":")
                if existing_vrf not in wanted_vrfs:
                    ret_del, _, err_del = run_cmd(["ip", "link", "del", existing_vrf], privileged=True)
                    if ret_del == 0:
                        logs.append(f"✔ VRF 削除: {existing_vrf}")
                    else:
                        logs.append(f"[WARN] VRF 削除失敗 {existing_vrf}: {err_del.strip()}")


def sync_host_vlan_interfaces(staging: Dict[str, Any], logs: List[str]) -> None:
    """
    map-e.conf で指定された MAPE_IF と各 VLAN サブインターフェースを
    ホストネットワーク名前空間上に自動作成・UP状態に同期する。
    """
    lowlayer = staging.get("low_layer", staging.get("lowlayer", {}))
    mape_if = lowlayer.get("MAPE_IF", "eth1").strip() or "eth1"
    common = staging.get("ipoe_common", {})
    slaac = staging.get("slaac", {})

    # 親インターフェースが存在するか確認
    ret, _, _ = run_cmd(["ip", "link", "show", mape_if], privileged=True)
    if ret != 0:
        logs.append(f"[WARN] 親インターフェース '{mape_if}' が見つかりません。ホストVLAN同期をスキップします。")
        return

    # 親インターフェースをUP
    run_cmd(["ip", "link", "set", "dev", mape_if, "up"], privileged=True)

    # 全接続方式のVLANリストを収集 (VLAN ID ごとに所属タイプを管理)
    vlan_types: Dict[int, set] = {}
    active_vlan_set = set()
    for key in ["SLAAC_DYN_VLANS", "PD_DYN_VLANS", "SLAAC_FIX_VLANS", "PD_FIX_VLANS", "PPPOE_VLANS"]:
        raw_val = lowlayer.get(key, "")
        for v in raw_val.split():
            v = v.strip()
            if v.isdigit():
                vid = int(v)
                if vid not in vlan_types:
                    vlan_types[vid] = set()
                vlan_types[vid].add(key)
                active_vlan_set.add(vid)

    # 1. 不要な既存 VLAN インターフェースの自動削除
    ret_links, out_links, _ = run_cmd(["ip", "-o", "link", "show"], privileged=True)
    if ret_links == 0:
        pattern = re.compile(r"^[0-9]+:\s+(" + re.escape(mape_if) + r"\.(\d+))(@\S+)?:")
        for line in out_links.splitlines():
            m = pattern.match(line.strip())
            if m:
                dev_name = m.group(1)
                vid = int(m.group(2))
                if vid not in active_vlan_set:
                    del_ret, _, del_err = run_cmd(["ip", "link", "del", "dev", dev_name], privileged=True)
                    if del_ret == 0:
                        logs.append(f"不要なホストVLANを削除しました: {dev_name}")
                    else:
                        logs.append(f"[WARN] 不要VLAN {dev_name} 削除失敗: {del_err.strip()}")

    # 2. 必要な VLAN インターフェースの作成・UP・IPv6有効化・アドレス設定
    for vlan in sorted(active_vlan_set):
        types = vlan_types[vlan]
        vif = f"{mape_if}.{vlan}"
        # 存在チェック
        ret_chk, _, _ = run_cmd(["ip", "link", "show", vif], privileged=True)
        if ret_chk != 0:
            # VLAN作成
            ret_add, _, err_add = run_cmd(
                ["ip", "link", "add", "link", mape_if, "name", vif, "type", "vlan", "id", str(vlan)],
                privileged=True
            )
            if ret_add == 0:
                logs.append(f"ホストVLAN作成: {vif} (VLAN ID: {vlan})")
            else:
                logs.append(f"[WARN] {vif} 作成失敗: {err_add.strip()}")

        # リンクをUP
        run_cmd(["ip", "link", "set", "dev", vif, "up"], privileged=True)

        # IPv6 を有効化
        sysctl_vif = vif.replace(".", "/")
        run_cmd(["sysctl", "-w", f"net.ipv6.conf.{sysctl_vif}.disable_ipv6=0"], privileged=True)
        run_cmd(["sysctl", "-w", f"net.ipv6.conf.{sysctl_vif}.accept_ra=0"], privileged=True)
        run_cmd(["sysctl", "-w", f"net.ipv6.conf.{sysctl_vif}.autoconf=0"], privileged=True)

        # SLAAC アドレスの適用 (カーネルルートを維持)
        if "SLAAC_DYN_VLANS" in types or "SLAAC_FIX_VLANS" in types:
            base = common.get("BASE_SUBNET", "").strip()
            base_value = slaac.get("SLAAC_BR_BASE", "1000")
            suffix = slaac.get("SLAAC_BR_SUFFIX", "::ff0a")
            if "SLAAC_FIX_VLANS" in types:
                base_value = slaac.get("SLAAC_FIX_BR_PREFIX", "3000")
                suffix = slaac.get("SLAAC_FIX_BR_SUFFIX", "::ff0d")
            try:
                address = f"{base}:{int(base_value) + vlan}::{suffix.lstrip(':')}/64"
                run_cmd(["ip", "-6", "addr", "replace", address, "dev", vif], privileged=True)
            except (TypeError, ValueError):
                logs.append(f"[WARN] SLAAC アドレスを計算できません: {vif}")

    # dummy0/dummy1 は生成設定を /etc に保存するだけでは既存 runtime に反映されない。
    # Apply 時に global address を直接同期し、networking.service の全体再起動を避ける。
    def ensure_dummy(name: str) -> None:
        ret_dummy, _, _ = run_cmd(["ip", "link", "show", name], privileged=True)
        if ret_dummy != 0:
            run_cmd(["ip", "link", "add", name, "type", "dummy"], privileged=True)
        run_cmd(["ip", "link", "set", "dev", name, "up"], privileged=True)

    try:
        base = common.get("BASE_SUBNET", "2001:db8").strip() or "2001:db8"
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
    """
    生成された system/interfaces および system/99-network-routing.conf を
    ホストの /etc/network/interfaces および /etc/sysctl.d/ に反映し、sysctl -p を実行する。
    また system/vrf-setup.sh が存在する場合は実行して VRF デバイスを作成・UP する。
    """
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

    # PPPoE VRF セットアップスクリプトの実行
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
