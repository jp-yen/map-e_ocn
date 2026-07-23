# OCN MAP-E接続に必要なプロビジョニング方法とルーティング設定について

OCN MAP-E（OCNバーチャルコネクト）接続におけるプロビジョニングの流れ、CE/BR設定、および動的IPと固定IPのプロビジョニング仕様の違いについてまとめています。

---

## 1. プロビジョニング（ルール取得）の流れ

CE（顧客ルーター）が起動すると、以下の手順で自動的に MAP-E のパラメータを取得・算出します。

1. **IPv6 アドレス取得と DNS 解決**
   SLAAC または DHCPv6-PD を介して IPv6 接続を確立し、通知された DNS サーバーを利用して OCN のプロビジョニングサーバー（例: `rule.map.ocn.ad.jp`）の IPv6 アドレスを解決します。
2. **ルール (JSON) の HTTPS 取得**
   ルーターはプロビジョニングサーバーへ HTTPS リクエストを送り、以下のような MAP-E ルールを受け取ります。
   ```json
   {
     "brIpv6Address": "5f00:3aa:a001::647f:ffff", // BR(終端装置)のIPv6アドレス
     "ipv6Prefix": "5f00:3aa::",                  // IPv6ルールプレフィックス
     "ipv6PrefixLength": 32,
     "ipv4Prefix": "198.51.133.177",              // 割り当てられたIPv4アドレス
     "ipv4PrefixLength": 32,
     "eaBitLength": 8,
     "psIdOffset": 4,                             // ポート計算用のオフセット長
     "psId": 25                                   // ポートセットID (PSID)
   }
   ```
3. **パラメータの自動算出**
   ルーターは受信したルールから、利用可能な「送信元 IPv4 アドレス（および NAPT ポート範囲）」と「カプセル化送信元となる CE IPv6 アドレス」を自動計算し、トンネルを確立します。

---

## 2. CE ルーター側 (NEC IX) の設定コマンド例

動的 IPv4 MAP-E 接続を行う際の、IXルーター側の主要な設定コマンドです。（詳細は [CPE-RA-dynamic.txt](file:///home/abc123/map-e_ocn/CPE-setting/CPE-RA-dynamic.txt) を参照）

```text
! 1. DHCPv6 プロファイルでDNS、NTP、ドメイン名の情報要求を設定
ipv6 dhcp client-profile dhcpv6-cl
  information-request
  option-request dns-servers
  option-request domain-search-list
  option-request ntp-servers

! 2. WAN側物理インターフェースで IPv6 自動設定と DHCPv6 クライアントを有効化
interface GigaEthernet0.0
  no ip address
  ipv6 enable
  ipv6 address autoconfig receive-default
  ipv6 dhcp client dhcpv6-cl
  no shutdown

! 3. MAP-E トンネルの作成（OCN用プロビジョニングの指定とNAPTの有効化）
interface Tunnel0.0
  tunnel mode map-e ocn
  ip address map-e
  ip tcp adjust-mss auto
  ip napt enable
  ip napt hairpinning
  no shutdown

! 4. デフォルトルートを MAP-E トンネルに向ける
ip route default Tunnel0.0
```

---

## 3. BR（Debian / 終端サーバー）側のルーティング設定コマンド

BR（Debian）側は、複数 CE からのトンネルを1つの共通デバイスで効率良く受け止めるため、`ip6tnl` の `external` モードを使用します。

### ① トンネルインターフェースの作成
カプセル化対向を固定せず、ルーティングテーブル側のパラメータで制御できる `external` トンネルを作成して起動します。
```bash
# トンネルデバイスの作成と起動
sudo ip link add mpe-common type ip6tnl mode any external
sudo ip link set dev mpe-common up
```

### ② トンネル用 IPv4 ルートの注入 (`encap ip6` を指定)
CE へ割り当てた `CE IPv4` 宛ての通信が、カプセル化されて対象の `CE IPv6` へ送信されるように、ルートを追加します。
- `src`: BR IPv6 アドレス (`dummy0` に付与した `BR_PREFIX` 派生アドレス)
- `dst`: CE の CE IPv6 アドレス (プレフィックスと IPv4/PSID から算出されたアドレス)

```bash
# 例: IPv4 198.51.133.177 宛を CE IPv6 (5f00:3aa:1901:0:c6:3385:b100:1900) 宛にカプセル化して送出
sudo ip route add 198.51.133.177/32 dev mpe-common \
     encap ip6 src 5f00:3aa:a001::647f:ffff dst 5f00:3aa:1901:0:c6:3385:b100:1900
```

### ③ 戻りパケットを受信するための rp_filter 無効化
デカプセル化されたパケットは `mpe-common` から出てきて `dummy0` に届きます。このとき、戻り経路チェック (`rp_filter`) でドロップされないように設定します。
```bash
sudo sysctl -w net.ipv4.conf.dummy0.rp_filter=0
sudo sysctl -w net.ipv4.conf.mpe-common.rp_filter=0
```

---

## 4. プロビジョニングサーバーにおける動的IPと固定IPの違い

OCNバーチャルコネクトの仕様上、プロビジョニングサーバー（および DHCPv6 Option 94）から CE に通知されるルールパラメータは、動的IP契約と固定IP契約で以下のように異なります。

| パラメータ名 | 動的IP接続 (SLAAC動的 / PD動的) | 固定IP接続 (SLAAC固定 / PD固定) |
|---|---|---|
| **IPv4アドレス決定** | 共有プールから自動決定 (MACやプレフィックス依存) | `map-e-static-ip.conf` から引き当てた固定IP |
| **`eaBitLength`** | **`8`** (商用OCN仕様) | **`0`** (1台1IP専用占有) |
| **`psIdOffset`** | **`4`** (商用OCN仕様) | **`0`** (ポート制限なし) |
| **`psId` (Port Set ID)** | **`1〜255 (算出値)`** | **`0`** (または未指定・全ポート利用可能) |

### 動作上の相違点

#### ① ポートセット（NAPTポート数）の制限有無
*   **動的IP:** `eaBitLength: 8`, `psIdOffset: 4` が通知されるため、CEは 8ビットの `psId` に基づいて算出された **240個のNAPTポート** のみ使用できます。
*   **固定IP:** `eaBitLength: 0`, `psIdOffset: 0` が通知され、CEはポート制限を受けず、**全ポート (約65,535個) が利用可能** になります。

#### ② CE IPv6 アドレスの末尾構造 (Interface ID)
CE が MAP-E トンネルの送信元として構成する CE IPv6 アドレスの末尾 (Interface ID の後半) には、以下のように PSID が反映されます。
*   **動的IP:** PSIDの値が Interface ID に埋め込まれます。
    *(例: `target_psid = 25 (0x19)` の場合、末尾は `::00c6:3385:b100:1900` のようになります)*
*   **固定IP:** ポート制限がないため、PSID部分は `0` となり、Interface ID の末尾も `0000` に固定されます。
    *(例: 末尾は `::00c6:3385:b100:0000`)*
