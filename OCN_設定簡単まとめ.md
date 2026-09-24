# OCN MAP-E 設定・動作まとめ

このドキュメントは、本システムで OCN バーチャルコネクト（MAP-E）接続を行う CE（ルーター）設定者向けに、**ルーターの設定例** と **設定後に接続が確立する仕組み（動作の流れ）** をまとめたものです。

---

## 1. CE ルーター側 (NEC IX) の設定例

CE ルーター側の設定コマンド例（SLAAC動的）です。

```text
! 1. DNS・ドメイン名などの情報要求プロファイル
ipv6 dhcp client-profile dhcpv6-cl
  information-request
  option-request dns-servers
  option-request domain-search-list
  option-request ntp-servers

! 2. WAN側で IPv6 自動設定 (SLAAC) と DHCPv6 クライアントを有効化
interface GigaEthernet0.0
  no ip address
  ipv6 enable
  ipv6 address autoconfig receive-default
  ipv6 dhcp client dhcpv6-cl
  no shutdown

! 3. MAP-E トンネルの定義（OCNプロビジョニングを指定し、ルール取得・パラメータ算出を自動化）
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

## 2. 設定後の接続確立フロー

上記の設定を投入したルーターを回線に接続すると、以下の 4 ステップで自動的に MAP-E トンネルが開通します。

```text
  [ CE ルーター ]                                 [ プロビジョニング / BR ]
        |                                                    |
        |--- ① RA (SLAAC) / DHCPv6-PD 受信 & DNS 取得 ----->|
        |                                                    |
        |--- ② HTTPS でプロビジョニング要求 --------------->|
        |<--    MAP-E ルール (JSON) を返却 ------------------|
        |       ※ BR 側は CE 宛てトンネルルートを自動登録   |
        |                                                    |
        |--- ③ ルールから IPv4・ポート範囲・BR アドレスを算出|
        |                                                    |
        |=== ④ MAP-E トンネル開通 (IPv4 over IPv6) =========|
```

1. **IPv6 / DNS の取得**
   回線接続により、RA (SLAAC) または DHCPv6-PD（ひかり電話環境）で IPv6 プレフィックスを受信し、DHCPv6 でプロビジョニングサーバーの名前解決に必要な DNS 情報を取得します。
2. **ルール (JSON) の自動取得**
   ルーターがプロビジョニングサーバーへ HTTPS でアクセスし、MAP-E 接続に必要なパラメータ（配布ルール）を取得します。
   （※ このとき BR 側も CE 宛てのトンネルルートを自動登録します）
3. **トンネルパラメータの自動算出**
   ルーターは受信したルールに基づき、自身の「送信元 IPv4 アドレス」「利用可能な NAPT ポート範囲」「カプセル化対向の BR IPv6 アドレス」を自動計算します。
4. **トンネル開通**
   計算結果がトンネルインターフェース（`Tunnel0.0`）に適用され、デフォルトルート経由で IPv4 over IPv6 通信が可能になります。

---

## 3. プロビジョニングで配布されるルール（JSON 例）

ステップ ② でルーターが取得する JSON の例です。
ルーターはこの情報を受け取ることで、自身の IPv4 アドレスや利用可能ポート、対向 BR アドレスを認識します。

```json
{
  "hostName": "ce-10.248.34.177.map.ocn.ad.jp",
  "basicMapRules": [
    {
      "brIpv6Address": "2400:4150:3620::647f:ffff",
      "ipv6Prefix": "2400:4150::",
      "ipv6PrefixLength": 32,
      "ipv4Prefix": "10.248.34.177",
      "ipv4PrefixLength": 32,
      "eaBitLength": 8,
      "psIdOffset": 4,
      "psId": 24,
      "hostName": "ce-10.248.34.177.map.ocn.ad.jp"
    }
  ]
}
```

- **`brIpv6Address`**: トンネル対向先（BR）の IPv6 アドレス (`2400:4150:3620::647f:ffff`)
- **`ipv4Prefix`**: CE に割り当てられる IPv4 アドレス (`10.248.34.177`)
- **`eaBitLength` / `psId`**: 利用可能な NAPT ポート範囲の算出情報
  - 上記の例 (`psId: 24`) では、`4480〜4495`, `8576〜8591`, …, `61824〜61839` の計 240 ポートが自動割り当てされます

> **BR 側ルートの自動登録**:
> BR（終端装置）側もこれらの値（`ipv4Prefix`、`brIpv6Address`）を用いて、CE 宛てのトンネルルートをカーネルへ自動登録します。
> ```bash
> # BR に自動登録されるルート例:
> ip route add 10.248.34.177/32 dev mpe-common encap ip6 src 2400:4150:3620::647f:ffff dst <CE_IPv6>
> ```

---

## 4. 動的 IP と 固定 IP の違い

CE に割り当てられる契約種別によって、配布ルールおよび動作が以下のように異なります。

| 項目 | 動的 IP 接続 (SLAAC動的 / PD動的) | 固定 IP 接続 (SLAAC固定 / PD固定) |
|---|---|---|
| **IPv4 割り当て** | プールから自動採番 | 指定の固定 IPv4 |
| **ポート制限 (NAPT)** | **あり**（約240ポート / `eaBitLength: 8`） | **なし**（全ポート約65,535個 / `eaBitLength: 0`） |
| **トンネル対向 (CE IPv6)** | 末尾に PSID が反映される | 末尾は `:0`（`0000`）固定 |
