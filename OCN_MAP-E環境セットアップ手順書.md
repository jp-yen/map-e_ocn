# OCN MAP-E エミュレーション環境 サーバーセットアップ手順書

本手順書は、Debian Linux 上に OCN MAP-E（OCNバーチャルコネクト）ISPプロビジョニング・BR（Border Relay）エミュレーション環境を新規に構築・セットアップするための管理者向けマニュアルです。

---

## 1. 前提条件と動作環境

### 動作OS・前提条件
- **OS**: Debian 13 上で動作確認済み
- **事前に必要なツール**: `make`, `sudo`（手順の実行に必須。Debian標準搭載）
- **依存パッケージ**: Step 3 の `sudo make packages` で Python 3、OpenSSL、Kea DHCPv6、BIND9、radvd、chrony、syslog-ng、logrotate 等が一括自動導入されます。
- **ネットワーク環境**:
  - **管理用 NIC (`MGT_IF`)**: サーバー管理・SSH接続用の独立したネットワークポート
  - **WAN用 NIC (`MAPE_IF`)**: CE群を収容するトランクポート（802.1Q タグVLAN 通信）
- **実行権限**:
  - 設定ファイル生成・アーカイブ作成: 一般ユーザー
  - パッケージ導入・設定導入・サービス起動: root 権限 (または sudo)

---

## 2. セットアップ手順

### Step 1: リポジトリの準備と中央設定 (`map-e.conf`) の編集

1. リポジトリを設置し、作業ディレクトリへ移動します。
2. 環境に合わせて `./map-e.conf` を編集します。

```bash
vi map-e.conf
```

**主な変更項目**:

```bash
# 1. 管理用ネットワーク設定
MGT_IF="ens3"                      # 管理用NIC名
MGT_IP="172.31.220.85/23"          # 管理用IP/プレフィックス長 (CIDR形式)
MGT_GW="172.31.220.1"

# 2. システム参照DNS/NTP/ドメイン設定
SYSTEM_DNS="172.31.100.21 172.31.100.100" # サーバー参照用外部DNS
SYSTEM_NTP="172.31.100.21 172.31.100.100" # サーバー参照用NTP
DOMAIN="my.corp"

# 3. MAP-E 物理トランクインターフェースと各方式のVLAN ID
MAPE_IF="ens8"                     # WAN側物理ポート名 (802.1Qトランク)
SLAAC_DYN_VLANS="841 842 843 844"   # ① SLAAC動的用VLAN一覧
PD_DYN_VLANS="845 846 947 948"      # ② DHCPv6-PD動的用VLAN一覧
SLAAC_FIX_VLANS="849"              # ③ SLAAC固定用VLAN一覧
PD_FIX_VLANS="850"                 # ④ DHCPv6-PD固定用VLAN一覧

# 4. ネットワークプレフィックス・アドレスプール
BASE_SUBNET="2400:4150"            # 基幹IPv6プレフィックス (ヘクステット2つ)
BR_IPV4_POOL="1.1.0.0"             # CEへ払い出す動的IPv4プール (/16固定)
BR_IPV4_ADDR="100.127.255.255"     # BR IPv4 アドレス
```

#### 📌 VLAN・CEの割り当てとIPv6アドレス設定ルール

##### 1. VLAN ID と CE の割当ルール
- 本環境では、**1つの VLAN ID につき 1台の CE** を接続・収容します。
- 接続するCEの台数分だけ VLAN ID を割当変数（`SLAAC_DYN_VLANS` 等）に空白区切りで指定します。
- **注意**: 各方式間で VLAN ID が重複しないように設定してください。また、プレフィックス自動計算のため VLAN ID は3桁の10進数（例: `841`, `845`）を推奨します。

##### 2. IPv6アドレス帯の自動割り当て規則
`map-e.conf` の基幹プレフィックス `BASE_SUBNET`（デフォルト: `2400:4150`）に対し、指定した VLAN ID から以下のように各VLANのIPv6アドレス帯が自動割り当てされます。

| 接続方式 | 設定変数名 | 自動割り当てされるIPv6アドレス帯 | 設定例（VLAN ID: `841`, `BASE_SUBNET="2400:4150"`） |
|---|---|---|---|
| **① SLAAC動的** | `SLAAC_DYN_VLANS` | `${BASE_SUBNET}:1<VLAN ID>::/64` | `2400:4150:1841::/64` （BR側: `::ff0a`） |
| **② DHCPv6-PD動的** | `PD_DYN_VLANS` | `${BASE_SUBNET}:2000::/40` （全PD VLAN共有、`/56`ずつ各CEへ委譲） | 共通委譲プール: `2400:4150:2000::/40` |
| **③ SLAAC固定** | `SLAAC_FIX_VLANS` | `${BASE_SUBNET}:3<VLAN ID>::/64` | `2400:4150:3849::/64` （BR側: `::ff0d`） |
| **④ DHCPv6-PD固定** | `PD_FIX_VLANS` | ②共通プールより委譲。<br>Option 94ダミーPrefix: `${BASE_SUBNET}:4000::/56` | 共通委譲プールより動的委譲 |

> [!NOTE]
> `BR_IPV4_POOL` のマスク長は Flex-Option のバイト境界仕様により **/16 固定** です。手動指定アドレス等の個別マッピングが必要な場合は Step 2 の固定IPv4マッピングを使用してください。

---

### Step 2: 固定IPv4マッピングの記述 (`map-e-static-ip.conf`) 【任意】

方式③（SLAAC固定）または方式④（PD固定）を使用する場合は、`map-e-static-ip.conf` に対象CEのMACアドレスと固定IPv4を記述します。

```bash
vi map-e-static-ip.conf
```

```text
# MACアドレス, 固定IPv4/プレフィックス長 (CIDR表記)
# ※ /32 の他、/27 や /29 等のサブネット/CIDRブロック表記もサポートされています
00:a0:de:11:22:33,203.0.113.32/27
00:a0:de:44:55:66,198.51.200.1/32
```

---

### Step 2.5: DDNS（Dynamic DNS）ホスト名マッピングの記述 (`ddns.conf`) 【任意】

CEのアドレス確定時に、指定したホスト名（IPv6 / IPv4 FQDN）へのDDNS自動登録を行わせたい場合は、`ddns.conf` を記述します。

```bash
vi ddns.conf
```

```text
# 識別キー (MACアドレス または VLAN ID), IPv6用FQDN, IPv4用FQDN
# MACアドレスで指定する場合
00:a0:de:11:22:33, cebox1.p-ns.flets-west.jp, cebox1.v4.flets-west.jp
00:a0:de:44:55:66, cebox2.aoi.flets-east.jp, cebox2.v4.flets-east.jp

# VLAN IDで指定する場合
841, cebox3.p-ns.flets-west.jp, cebox3.v4.flets-west.jp
845, cebox4.aoi.flets-east.jp, cebox4.v4.flets-east.jp
```

> [!TIP]
> **登録日時の確認（TXTレコード）**:
> DDNS登録時には A/AAAA レコードと同時に、更新タイムスタンプ（`updated=YYYY-MM-DDTHH:MM:SS+09:00`）が **TXT レコード** として自動記録されます。問い合わせ先のDNSサーバーには共通サービスIPv6（`${MAPE_DNS_IP}`）、管理用IPv4（`${MGT_IP}`）、または `127.0.0.1` を指定して確認できます：
> ```bash
> # 共通サービスIPv6アドレス (${MAPE_DNS_IP}) で確認する場合
> dig @2400:4150:9999::53 TXT cebox1.p-ns.flets-west.jp
>
> # 管理用IPv4アドレス (${MGT_IP}) または 127.0.0.1 で確認する場合
> dig @172.31.220.85 TXT cebox1.p-ns.flets-west.jp
> ```

---

### Step 3: 必要パッケージの導入

環境に必要なミドルウェアおよびパッケージを一括導入します（要 root 権限）。

```bash
sudo make packages
```

**導入される主なパッケージ**:
- `radvd` (Router Advertisement デーモン)
- `kea-dhcp6-server` (DHCPv6 サーバー)
- `bind9` (DNS サーバー)
- `chrony` (NTP サーバー)
- `python3`, `openssl`, `syslog-ng`, `sharutils` など

---

### Step 4: 設定ファイルの自動生成 (Generate)

`map-e.conf` の定義に基づき、各種サーバー設定ファイル・スクリプト・SSL自己署名証明書を自動生成します。

> [!IMPORTANT]
> このターゲットは **一般ユーザー権限** で実行してください（root 権限で実行するとエラーになります）。

```bash
make generate
```

**自動生成される主なファイル**:
- `system/interfaces`（VLANサブインターフェース・dummy0/1定義）
- `system/99-network-routing.conf`（カーネル転送・rp_filter無効化）
- `radvd/radvd.conf`（SLAAC/PDごとのRA通知設定）
- `kea-dhcp6/kea-dhcp6.conf`（Flex-Option・Host Reservation設定）
- `kea-dhcp6/kea-map-e-hook`（Kea用動的ルーティング注入フック）
- `bind/named.conf.local`, `bind/db.map.ocn.ad.jp`（プロビジョニングDNS）
- `chrony/chrony.conf`（NTP設定）
- `mape-provisioning-server/*`（HTTPSサーバー、NDP監視、計算ライブラリ、証明書）

---

### Step 5: 設定構文の検証 (Check)

生成された全設定ファイルの構文チェックを一括実行します（要 root 権限）。

> [!NOTE]
> 設定チェックツール（例: `named-checkconf` や `syslog-ng`）は `/etc` 配下のシステム既設ファイルを読み込む仕様があるため、初回構築時で `/etc` 側に設定ファイルが未配置の場合は `sudo make check` で警告が出る場合があります。初回は `sudo make install` 実行後にチェックを再実施してください。

```bash
sudo make check
```

**確認対象**:
- `chronyd -Q` 構文チェック
- `syslog-ng --syntax-only` チェック
- BIND9 設定およびゾーン（`map.ocn.ad.jp`, `v6connect.net`）チェック
- `radvd -c` 構文チェック
- `kea-dhcp6 -t` 設定ファイルテスト

すべて `OK` または正常終了することを確認してください。

---

### Step 6: システムへのインストールとサービス起動 (Install)

生成された設定ファイルをシステムディレクトリへ配置し、ネットワーク構成の再ロードおよび各種サービスを有効化・起動します（要 root 権限）。

```bash
sudo make install
```

**実行される処理**:
1. WAN側VLANインターフェースおよびBR用仮想インターフェースの構成ファイルを配置し、インターフェースを有効化・再ロード
2. IPパケット転送（IPv4/IPv6ルーティング）の有効化、およびトンネル通信用の逆経路フィルタ（`rp_filter`）無効化設定のカーネルへの即時反映 (`sysctl -p`)
3. 各種サーバー（radvd / Kea DHCPv6 / BIND DNS / chrony NTP）設定ファイルの `/etc/` 配下への配置
4. MAP-Eプロビジョニングサーバー用スクリプト・SSL証明書・計算ライブラリの `/usr/local/bin/` への配置と実行権限設定
5. Kea DHCPv6 フック用 AppArmor プロファイルのカーネルへの読み込み (`apparmor_parser`)
6. 各種 systemd サービス（プロビジョニング、NDP監視、DHCPv6、DNS、NTP等）の自動起動有効化およびサービスの再起動

---

## 3. セットアップ完了後の動作確認

### 1. サービスの動作状態確認

各サービスが正常に `active (running)` になっているか確認します。

```bash
sudo systemctl status radvd kea-dhcp6-server mape-provisioning-server mape-route-monitor bind9 chrony
```

### 2. ネットワークインターフェースの確認

`BR_IF`, `PROV_IF` および各VLANサブインターフェースが作成されているか確認します。

```bash
ip link show
```

### 3. トラブルシューティング時のログ確認先

動作に問題がある場合や、CE接続時の詳細ログを確認する場合は、`/var/log/` 配下の各専用ログを参照します。

```bash
# プロビジョニングリクエストのログ確認
tail -n 20 /var/log/mape-provisioning-server.log

# DHCPv6 リースおよびフック処理のログ確認
tail -n 20 /var/log/kea/kea-dhcp6.log

# NDP 監視・動的ルート注入のログ確認
tail -n 20 /var/log/mape-route-monitor.log
```

---

## 4. 運用・管理コマンド

### 設定変更時の再適用手順（既存パラメータ修正時）

`map-e.conf` または `map-e-static-ip.conf` で既存のパラメータ（IPアドレスやプレフィックス等）を編集した場合は、事前に構文チェック（`make check`）を行ってから反映します。

```bash
# 1. 一般ユーザーで設定ファイルを再生成
make generate

# 2. rootで生成ファイルの構文エラーを事前チェック
sudo make check

# 3. rootでシステムへインストール・サービス反映
sudo make install
```

### CE（VLAN）の追加手順（新規VLAN追加時）

新しいVLANを追加した場合は、OS側にサブインターフェースがまだ存在しないため `make check` の Kea 検証でエラーとなります。そのため、`generate` 後に直接 `install` を実行してインターフェース作成とサービス適用を行います。

1. **`map-e.conf` の対象方式変数に新しい VLAN ID を追記**
   ```bash
   # 例: SLAAC動的（841 842 843 844）に VLAN 845 を追加
   SLAAC_DYN_VLANS="841 842 843 844 845"
   ```
2. **設定の自動生成とシステムへの適用**
   ```bash
   make generate
   sudo make install # インターフェース作成・システム適用・サービス再起動
   ```

### 環境状態の保存とアーカイブ作成 (`archive`)

現在のネットワーク状態・サービス状態を `STATUS.TXT` に記録し、リソース一式を圧縮保存します。

```bash
make archive
# ➔ STATUS.TXT, MAP-E.tar.xz が生成されます
```

### クリーンアップ (`clean`)

生成された設定ファイル群や中間ファイルを削除します。

```bash
make clean
```
