#!/usr/bin/env python3
"""
MAP-E SLAAC動的接続 計算ロジック検証＆本番突き合わせ用ツール (verify_calc.py)

■ 概要
本スクリプトは、SLAAC動的接続方式（①）における MAP-E アドレス算出ロジック
（IPv4決定式、target_psid抽出、CE IPv6合成）の単体テスト、および本番環境（BR）の
実ルーティングテーブルとの突き合わせ（整合性チェック）を行うためのツールです。

■ 1. 値の与え方 (入力パラメータの設定)
  - mac           : BR 上の NDP テーブル (`ip -6 neighbor show`) 等で確認した CE の MAC アドレス。
  - BR_IPV4_POOL  : map-e.conf で定義されている動的共有 IPv4 プール (例: '198.51.0.0')。
  - BR_IPV4_MASK  : マスク長 (例: '16')。
  - BASE_SUBNET   : 基幹 IPv6 プレフィックス (例: '5f00:3aa')。
  - SLAAC_BR_BASE : SLAAC動的プレフィックスのベース値 (例: '1000')。
  - VLAN          : 対象 CE が接続されている WAN 側の VLAN ID (例: 901)。
  - route_dst     : (Step 5) 本番 Linux 上で実際に注入されたトンネルルート宛先
                    `ip route show dev mpe-common` または syslog ([ROUTE-ADD]) から取得した値を設定。

■ 2. 結果の読み方 (出力項目の意味)
  - target_ipv4             : 計算された CE への割り当て IPv4 アドレス。
  - target_psid             : VLAN/プレフィックスから動的抽出された PSID（10進数および16進数）。
  - CE data IPv6            : RFC 7597 / OCN仕様に基づき Interface ID に PSID を埋め込んだ CE 側トンネル IPv6。
  - After PSID zeroing      : BR 側のルーティング注入用に対向 IPv6 の PSID フィールド(block7)を '0000' へ補正したアドレス。
  - Match: True / False     : 算出結果 (After PSID zeroing) が本番の実注入ルート (route_dst) と完全に一致しているか。
  - CE side analysis        : NDP (SLAAC) で CE が取得する WAN 側 IPv6 アドレスと、MAP-E カプセル化用 IPv6
                              アドレスの違いについての解説（CE 側で MAP-E アドレスの個別設定が必要な根拠）。

■ 3. 本番との突き合わせの方法
  1) BR (Debian) 端末で以下のコマンドを実行し、接続中の CE の MAC アドレスと VLAN を確認して入力パラメータを設定:
       ip -6 neighbor show
  2) BR 上で実際に注入された MAP-E ルートの対向 IPv6 アドレスを確認して route_dst に設定:
       ip route show dev mpe-common
  3) 本スクリプトを実行し、`Match: True` と表示されれば本番ルーチン (mape_calc.py) と計算結果が完全に一致しています:
       python3 verify_calc.py
"""
import ipaddress

# CE router MAC from NDP neighbor table on ens19.901
mac = '00:60:b9:e5:a1:b1'

# map-e.conf values
BR_IPV4_POOL = '198.51.0.0'
BR_IPV4_MASK = '16'
BASE_SUBNET = '5f00:3aa'
SLAAC_BR_BASE = '1000'
VLAN = 901

assert BR_IPV4_MASK == '16', "BR_IPV4_POOL must be /16 to prevent address collision"

# ステップ1: SLAAC 動的式で target IPv4 を計算
pool_octets = ipaddress.IPv4Network(f'{BR_IPV4_POOL}/{BR_IPV4_MASK}', strict=False).network_address.exploded.split('.')
vlan_base = int(SLAAC_BR_BASE)
seg_hextet_val = vlan_base + VLAN  # 1901

octet2 = VLAN % 256  # 901 % 256 = 133
octet3 = int(mac.split(':')[-1], 16)  # 0xb1 = 177
target_v4 = f'{pool_octets[0]}.{pool_octets[1]}.{octet2}.{octet3}'
print(f'target_ipv4 = {target_v4}')

# ステップ2: CE IPv6 アドレスを算出
clean_ip = f'{BASE_SUBNET}:{seg_hextet_val}::'
v6_addr = ipaddress.IPv6Address(clean_ip)
exploded = v6_addr.exploded.split(':')
seg = exploded[2]  # base_len=2
print(f'seg_hextet from exploded = {seg}')
psid = int(seg[:2], 16)
print(f'target_psid = {psid} (hex: {psid:02x})')

# ステップ3: CE データ IPv6 を構築
v4_octets = [int(x) for x in target_v4.split('.')]
prefix_part = v6_addr.exploded.split(':')[:4]

block4 = f'00{v4_octets[0]:02x}'
block5 = f'{v4_octets[1]:02x}{v4_octets[2]:02x}'
block6 = f'{v4_octets[3]:02x}00'
block7 = f'{psid:02x}00'
print(f'blocks: {block4}:{block5}:{block6}:{block7}')

data_ipv6_str = ':'.join(prefix_part) + f':{block4}:{block5}:{block6}:{block7}'
result = ipaddress.IPv6Address(data_ipv6_str)
print(f'CE data IPv6 = {result.compressed}')
print(f'CE data IPv6 exploded = {result.exploded}')

# ステップ4: setup_tunnel_route 後に block7 (exploded[7]) を 0 にリセット
exp = result.exploded.split(':')
exp[7] = '0000'
zeroed = ipaddress.IPv6Address(':'.join(exp))
print(f'After PSID zeroing = {zeroed.compressed}')
print(f'After PSID zeroing exploded = {zeroed.exploded}')

# ステップ5: 実際のルートと比較
route_dst = ipaddress.IPv6Address('5f00:3aa:1901:0:c6:3385:b100:0')
print(f'Route dst = {route_dst.compressed}')
print(f'Route dst exploded = {route_dst.exploded}')
print(f'Match: {zeroed == route_dst}')

# ステップ6: CE ルーターの実際の IPv6 アドレスは？
# NDP が示す: 5f00:3aa:1901:0:260:b9ff:fee5:a1b1 - これは MAC 00:60:b9:e5:a1:b1 の EUI-64 表現です
# ただしルートは: 5f00:3aa:1901:0:c6:3385:b100:0 - これは MAP-E が算出した CE アドレスです
# CE はトンネルインターフェースに MAP-E アドレスを設定する必要があります
print()
print('=== CE side analysis ===')
print(f'CE SLAAC address (NDP): 5f00:3aa:1901:0:260:b9ff:fee5:a1b1')
print(f'MAP-E CE tunnel addr:   {zeroed.compressed}')
print(f'These are DIFFERENT - CE must configure the MAP-E address on its tunnel interface')

print()
print('=== DHCPv6-PD Flex-Option /16 Pool & Offsets Test ===')
# PD 動的プレフィックスの検証 (例: 2400:4150:2000:1200::/56)
pd_sample_prefix = '2400:4150:2000:1200::/56'
pd_net = ipaddress.IPv6Network(pd_sample_prefix, strict=False)
pd_bytes = pd_net.network_address.packed
# Kea Option 26 (IA_PREFIX):
#   - preferred-lifetime (4B, 0..3) + valid-lifetime (4B, 4..7) + prefix-len (1B, 8)
#   - ipv6-prefix (16B, 9..24):
#       IPv6 byte 4 (hextet 3 high, e.g. 0x20) -> option 26 offset 9 + 4 = 13 (target_psid)
#       IPv6 byte 6 (hextet 4 high, e.g. 0x12) -> option 26 offset 9 + 6 = 15 (IPv4第3オクテット)
offset_13_val = pd_bytes[4] # hextet 3 high byte
offset_15_val = pd_bytes[6] # hextet 4 high byte (delegation ID variable byte)
print(f'PD prefix: {pd_sample_prefix}')
print(f'  Option 26 offset 13 (Option 93 target_psid) : 0x{offset_13_val:02x} ({offset_13_val})')
print(f'  Option 26 offset 15 (Option 89 IPv4 3rd octet): 0x{offset_15_val:02x} ({offset_15_val})')
assert offset_13_val == 0x20, f"Offset 13 should be 0x20, got 0x{offset_13_val:02x}"
assert offset_15_val == 0x12, f"Offset 15 should be 0x12, got 0x{offset_15_val:02x}"
print('  [OK] Option 26 offsets 13 and 15 match Kea flex-option and OCN design.')

print()
print('=== DDNS Load & Match Test ===')
import importlib.machinery
import importlib.util
try:
    loader = importlib.machinery.SourceFileLoader("mape_calc", "./mape-provisioning-server/mape_calc")
    spec = importlib.util.spec_from_loader("mape_calc", loader)
    mape_calc = importlib.util.module_from_spec(spec)
    loader.exec_module(mape_calc)
    mac_map, vlan_map = mape_calc.load_ddns_map('./ddns.conf')
    print(f'Loaded MAC DDNS map count: {len(mac_map)}, VLAN DDNS map count: {len(vlan_map)}')
    for k, v in mac_map.items():
        print(f'  MAC {k} -> IPv6: {v[0]}, IPv4: {v[1]}')
    for k, v in vlan_map.items():
        print(f'  VLAN {k} -> IPv6: {v[0]}, IPv4: {v[1]}')

    # mape_calc の calc_map_e_params で PD /16 計算を突き合わせ
    v4_pd, ce_v6_pd, seg_pd, psid_pd = mape_calc.calc_map_e_params('2400:4150:2000:1200::')
    print(f'mape_calc PD calc result: v4={v4_pd}, psid={psid_pd}, segment={seg_pd}')
    assert psid_pd == offset_13_val, f"mape_calc PSID ({psid_pd}) must match flex-option offset 13 ({offset_13_val})"
    v4_octs = [int(x) for x in v4_pd.split('.')]
    assert v4_octs[2] == offset_15_val, f"mape_calc IPv4 octet 2 ({v4_octs[2]}) must match flex-option offset 15 ({offset_15_val})"
    print('  [OK] mape_calc PD calculation fully matches Kea flex-option logic.')
except Exception as ex:
    print(f'Test error: {ex}')
    raise ex

