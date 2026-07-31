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

# Step 1: Calculate target IPv4 (SLAAC dynamic formula)
pool_octets = ipaddress.IPv4Network(f'{BR_IPV4_POOL}/{BR_IPV4_MASK}', strict=False).network_address.exploded.split('.')
vlan_base = int(SLAAC_BR_BASE)
seg_hextet_val = vlan_base + VLAN  # 1901

octet2 = VLAN % 256  # 901 % 256 = 133
octet3 = int(mac.split(':')[-1], 16)  # 0xb1 = 177
target_v4 = f'{pool_octets[0]}.{pool_octets[1]}.{octet2}.{octet3}'
print(f'target_ipv4 = {target_v4}')

# Step 2: Calculate CE IPv6 address
clean_ip = f'{BASE_SUBNET}:{seg_hextet_val}::'
v6_addr = ipaddress.IPv6Address(clean_ip)
exploded = v6_addr.exploded.split(':')
seg = exploded[2]  # base_len=2
print(f'seg_hextet from exploded = {seg}')
psid = int(seg[:2], 16)
print(f'target_psid = {psid} (hex: {psid:02x})')

# Step 3: Build CE data IPv6
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

# Step 4: After setup_tunnel_route zeroes block7 (exploded[7])
exp = result.exploded.split(':')
exp[7] = '0000'
zeroed = ipaddress.IPv6Address(':'.join(exp))
print(f'After PSID zeroing = {zeroed.compressed}')
print(f'After PSID zeroing exploded = {zeroed.exploded}')

# Step 5: Compare with actual route
route_dst = ipaddress.IPv6Address('5f00:3aa:1901:0:c6:3385:b100:0')
print(f'Route dst = {route_dst.compressed}')
print(f'Route dst exploded = {route_dst.exploded}')
print(f'Match: {zeroed == route_dst}')

# Step 6: What about the CE router's actual IPv6?
# NDP shows: 5f00:3aa:1901:0:260:b9ff:fee5:a1b1 - this is EUI-64 of MAC 00:60:b9:e5:a1:b1
# But route goes to: 5f00:3aa:1901:0:c6:3385:b100:0 - this is the MAP-E computed CE address
# The CE needs to have the MAP-E address configured on its tunnel interface
print()
print('=== CE side analysis ===')
print(f'CE SLAAC address (NDP): 5f00:3aa:1901:0:260:b9ff:fee5:a1b1')
print(f'MAP-E CE tunnel addr:   {zeroed.compressed}')
print(f'These are DIFFERENT - CE must configure the MAP-E address on its tunnel interface')

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
    print(f'Loaded MAC DDNS map count: {len(mac_map)}')
    print(f'Loaded VLAN DDNS map count: {len(vlan_map)}')
    for k, v in mac_map.items():
        print(f'  MAC {k} -> IPv6: {v[0]}, IPv4: {v[1]}')
    for k, v in vlan_map.items():
        print(f'  VLAN {k} -> IPv6: {v[0]}, IPv4: {v[1]}')
except Exception as ex:
    print(f'DDNS test error: {ex}')

