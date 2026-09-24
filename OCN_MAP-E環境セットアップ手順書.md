# OCN MAP-E & PPPoE エミュレーション環境 サーバーセットアップ手順書

本手順書は、Debian Linux 上に OCN MAP-E（OCNバーチャルコネクト）ISPプロビジョニング・BR（Border Relay）および PPPoE エミュレーション環境を、**Docker コンテナベースアーキテクチャ** で新規に構築・セットアップするための管理者向けマニュアルです。

---

## 0. 本手順書で使用する統一検証設定例（ダミー値）

> [!IMPORTANT]
> **本手順書におけるパラメータ表記について**
> 本手順書に記載されている IP アドレス、インターフェース名、VLAN ID、ドメイン名などの具体的な設定値は、**すべて解説用の仮定の統一設定例（ダミー値）** です。実機値や実稼働環境の値は一切含まれていません。
> 実際の構築にあたっては、管理環境や接続する物理ネットワークの要件に合わせて適切な値に読み替えて設定してください。

### 【仮定】統一検証設定例（ベースパラメータ一覧）

| 分類 | 変数名 | 仮定の設定例（ダミー値） | 説明 |
|---|---|---|---|
| **管理系** | `MGT_IF` | `eth0` | サーバー管理用物理インターフェース |
| | `MGT_IP` | `192.168.0.10/24` | サーバー管理用 IPv4 アドレス（CIDR形式） |
| | `MGT_GW` | `192.168.0.1` | 管理用デフォルトゲートウェイ |
| | `SYSTEM_DNS` | `192.168.0.1 1.1.1.1` | サーバー参照用外部DNS |
| | `SYSTEM_NTP` | `192.168.0.1 ntp.example.jp` | サーバー参照用NTP |
| | `DOMAIN` | `example.com` | ドメイン名 |
| | `WEB_DASHBOARD_PORT` | `1600` | Web 統合ダッシュボード待受ポート |
| | `NAT_ENABLED` | **`0`** | **外部接続 Central NAT のデフォルト設定（0: 無効）** |
| **WAN / MAP-E 基幹** | `MAPE_IF` | `eth1` | CE群を収容する WAN 物理トランクポート |
| | `BASE_SUBNET` | `2001:db8` | 基幹 IPv6 プレフィックス (RFC 3849 ドキュメント用) |
| | `BR_IPV4_POOL` | `198.51.0.0/16` | **CEへ払い出す動的 IPv4 プール（アドレス衝突防止のため /16 固定）** |
| | `BR_IPV4_ADDR` | `192.0.2.1` | BR IPv4 アドレス (RFC 5737 TEST-NET-1) |
| **方式別 VLAN** | `SLAAC_DYN_VLANS` | `101 102` | ① SLAAC動的用 VLAN（代表接続例: VLAN `101`） |
| | `PD_DYN_VLANS` | `103 104` | ② DHCPv6-PD動的用 VLAN（代表接続例: VLAN `103`） |
| | `SLAAC_FIX_VLANS` | `105` | ③ SLAAC固定用 VLAN（代表接続例: VLAN `105`） |
| | `PD_FIX_VLANS` | `106` | ④ DHCPv6-PD固定用 VLAN（代表接続例: VLAN `106`） |
| | `PPPOE_VLANS` | `107` | PPPoE接続用 VLAN（マルチサービス収容） |

---

## 1. 前提条件と動作環境

### 動作OS・前提条件
- **OS**: Debian 13 (Trixie) / Debian 12 (Bookworm) で動作確認済み
- **必須ツール**:
  - **Docker Engine & Docker Compose プラグイン**（`docker compose` コマンドが利用可能であること）
  - **`make`**, **`perl`**, **`sudo`**（Debian 標準搭載または `apt install -y make perl sudo`）
  *(※ 各サービスはすべて Docker コンテナとして自動構築・動作するため、ホストOSへの個別サーバーパッケージのインストールは不要です)*
- **ネットワーク環境**:
  - **管理用 NIC (`MGT_IF`)**: サーバー管理・SSH接続・Webダッシュボード用の独立したネットワークポート（例: `eth0`）
  - **WAN用 NIC (`MAPE_IF`)**: CE群を収容するトランクポート（802.1Q タグVLAN 通信、例: `eth1`）
- **実行権限**:
  - 設定ファイル生成・Compose定義生成: 一般ユーザー
  - ホストネットワーク/sysctl反映・コンテナ起動: 一般ユーザー（WebUI 経由または Docker グループ所属時）または root 権限 (`sudo`)

---

## 2. セットアップ手順

セットアップには、ブラウザから直感的に設定・適用できる **【方法 A】Web UI によるクイックセットアップ（推奨）** と、コンソールから設定ファイルを編集する **【方法 B】CUI による手動セットアップ** の 2 つの方法があります。

```text
【方法 A: Web UI によるクイックセットアップ（推奨）】
[Step A-1] map-e.conf で管理ポート（MGT_IF, MGT_IP）のみ最小限設定
   ↓
[Step A-2] make web-dashboard で Web ダッシュボードのみ先行起動 (一般ユーザー)
   ↓
[Step A-3] ブラウザで http://<サーバー管理IP>:1600/ にアクセスし、「⚙ 設定編集」から全設定を入力
   ↓
[Step A-4] 「設定を保存して再生成・適用（反映を実行）」をクリック
         ➔ ホストネットワーク設定・全サービスコンテナの起動が全自動で完了！

【方法 B: CUI による手動セットアップ（CLI手順）】
[Step B-1] map-e.conf を環境に合わせて全項目編集
   ↓
[Step B-2] make generate で各種設定 & docker-compose.yml を自動生成 (一般ユーザー)
   ↓
[Step B-3] sudo make install でホストネットワーク適用 & 全コンテナ一括起動 (要 sudo)
```

---

### 【方法 A】Web UI によるクイックセットアップ（推奨）

管理ポート情報のみを最小限設定して Web ダッシュボードコンテナ（`web-dashboard`）を先行起動し、WAN 構成や各サービスの設定・適用をブラウザ（WebUI）上からすべてグラフィカルに行う手順です。

#### Step A-1: 管理用ネットワークの最小限設定

リポジトリ直下の `map-e.conf` を開き、管理用 NIC・IP アドレス・ポート番号を確認・設定します。
（※ WAN ポート名や VLAN 等の他パラメータはデフォルトのままで問題ありません。後ほど WebUI から設定します）

```bash
vi map-e.conf
```

```bash
# 管理用ネットワーク設定（実環境の管理ポートに合わせて設定）
MGT_IF="eth0"                      # 管理用NIC名
MGT_IP="192.168.0.10/24"          # 管理用IP/プレフィックス長 (CIDR形式)
MGT_GW="192.168.0.1"              # 管理用デフォルトゲートウェイ
WEB_DASHBOARD_PORT="1600"          # ダッシュボード待受ポート (デフォルト: 1600)
```

#### Step A-2: Web ダッシュボードの先行起動

一般ユーザー権限で `make web-dashboard` を実行します（初回 compose 定義の生成とダッシュボードコンテナのビルド・起動が自動で行われます）。

```bash
make web-dashboard
# または: make generate && docker compose up -d --build web-dashboard
```

起動後、コンテナが `Up (healthy)` になっていることを確認します：
```bash
docker compose ps web-dashboard
```

#### Step A-3: Web UI にアクセスし環境パラメータを入力

ブラウザから以下の URL にアクセスします：
```text
http://<サーバー管理IP>:1600/config.html
（またはトップページ右上の「⚙ 設定編集」リンク）
```

Web 画面上で以下の各項目を設定します：

1. **WAN トランクポート & 各接続方式の VLAN ID**:
   - WAN インターフェース名 (`MAPE_IF`): 例 `eth1`
   - 各方式の VLAN ID 一覧（空白区切りで指定。1 VLAN につき 1台の CE を収容）:
     - SLAAC動的（例: `101 102`）
     - DHCPv6-PD動的（例: `103 104`）
     - SLAAC固定（例: `105`）
     - DHCPv6-PD固定（例: `106`）
     - PPPoE（例: `107`）
2. **基幹プレフィックス & IPv4 プール**:
   - 基幹 IPv6 プレフィックス (`BASE_SUBNET`): 例 `2001:db8`
   - 動的 IPv4 プール (`BR_IPV4_POOL`): 例 `198.51.0.0/16`（アドレス衝突防止のため `/16` 固定）
   - BR IPv4 アドレス (`BR_IPV4_ADDR`): 例 `192.0.2.1`
3. **PPPoE サービス & アカウント認証情報**:
   - サービス定義、ユーザー名・パスワード、割り当て IP アドレスをテーブル形式または CSV 一括貼り付けで入力
4. **個別固定 IP マッピング & DDNS マッピング 【任意】**:
   - 固定 IPv4（方式③・④用）: CE の MAC アドレスと固定 IPv4 の対応表
   - DDNS FQDN: CE 接続時に BIND へ自動動的登録するドメイン・ホスト名

#### Step A-4: 「反映を実行（Apply）」による全自動起動

画面下部の **「設定を保存して再生成・適用」** ボタンをクリックし、確認モーダルで **「反映を実行」** を押します。

- Web ダッシュボードコンテナ（特権 DooD）が自動的に：
  1. `map-e.conf`, `pppoe.conf`, `map-e-static-ip.conf`, `ddns.conf` を保存
  2. `make generate` を実行して全コンテナ設定および `docker-compose.yml` を再生成
  3. ホストネットワークインターフェース（VLAN サブIF / dummy）およびカーネルパラメータを即時適用
  4. `docker compose up -d` を実行し、全サービスコンテナ（bind9, chrony, kea-dhcp6, mape-provisioning-server, pppoe-server群, radvd, central-nat, syslog-ng 等）を一括ビルド・起動
- 実行進捗ログおよび全コンテナの稼働ステータスが画面上にリアルタイム表示されます。完了メッセージが表示されたらセットアップは完了です。

---

### 【方法 B】CUI による手動セットアップ（CLI 手順）

SSH やコンソール上で設定ファイルを直接編集してセットアップを行う手順です。

#### Step B-1: 中央設定 (`map-e.conf`) の編集

リポジトリ直下の `./map-e.conf` を環境に合わせて編集します。

```bash
vi map-e.conf
```

**主な設定項目**:

```bash
# 1. 管理用ネットワーク設定
MGT_IF="eth0"                      # 管理用NIC名
MGT_IP="192.168.0.10/24"          # 管理用IP/プレフィックス長 (CIDR形式)
MGT_GW="192.168.0.1"
WEB_DASHBOARD_PORT="1600"

# 2. システム参照DNS/NTP/ドメイン設定
SYSTEM_DNS="192.168.0.1 1.1.1.1"   # サーバー参照用外部DNS
SYSTEM_NTP="192.168.0.1 ntp.example.jp" # サーバー参照用NTP
DOMAIN="example.com"

# 3. MAP-E 物理トランクインターフェースと各方式のVLAN ID
MAPE_IF="eth1"                     # WAN側物理ポート名 (802.1Qトランク)
SLAAC_DYN_VLANS="101 102"          # ① SLAAC動的用VLAN一覧 (先頭101が接続宣言VLAN)
PD_DYN_VLANS="103 104"             # ② DHCPv6-PD動的用VLAN一覧 (先頭103が接続宣言VLAN)
SLAAC_FIX_VLANS="105"              # ③ SLAAC固定用VLAN一覧 (接続宣言: 105)
PD_FIX_VLANS="106"                 # ④ DHCPv6-PD固定用VLAN一覧 (接続宣言: 106)
PPPOE_VLANS="107"                  # PPPoE接続用VLAN一覧 (マルチサービス収容)

# 4. ネットワークプレフィックス・アドレスプール
BASE_SUBNET="2001:db8"             # 基幹IPv6プレフィックス (ヘクステット2つ)
BR_IPV4_POOL="198.51.0.0/16"       # CEへ払い出す動的IPv4プール (/16固定)
BR_IPV4_ADDR="192.0.2.1"           # BR IPv4 アドレス
NAT_ENABLED="0"                    # Central NAT (0: 無効, 1: 有効)
```

#### Step B-1.5: 個別マッピング定義 【任意】

* **固定IPv4マッピング (`map-e-static-ip.conf`)**:
  方式③（SLAAC固定）または方式④（PD固定）で顧客指定の固定IPv4を割り当てる場合、MACアドレスとIPv4の対応表を記述します。
  ```bash
  vi map-e-static-ip.conf
  ```
  ```text
  # MACアドレス, 固定IPv4/プレフィックス長
  00:a0:de:11:22:33,198.51.100.1/32
  00:a0:de:44:55:66,198.51.200.1/32
  ```

* **DDNSホスト名マッピング (`ddns.conf`)**:
  CE接続時に指定FQDNを自動登録させたい場合に記述します。
  ```bash
  vi ddns.conf
  ```
  ```text
  # MACアドレス, IPv6用FQDN, IPv4用FQDN (未登録時は ce-<IPv4アドレス>.map.ocn.ad.jp が自動適用)
  00:a0:de:11:22:33, cebox1.p-ns.flets-west.jp, cebox1.v4.flets-west.jp
  ```

#### Step B-2: 設定ファイル & Docker Compose の一括自動生成 (`make generate`)

`map-e.conf` の定義に基づき、各種サーバー設定ファイル・自己署名証明書・動的DDNSゾーン・および `docker-compose.yml` を一括自動生成します。

> [!IMPORTANT]
> このコマンドは **一般ユーザー権限** で実行してください。

```bash
make generate
```

**自動生成される主な成果物**:
- `system/interfaces`（VLANサブインターフェース・dummyインターフェース定義）
- `system/99-network-routing.conf`（パケット転送・rp_filter無効化カーネルパラメータ）
- `radvd/radvd.conf`（SLAAC/PD用 RA 広告設定）
- `kea-dhcp6/kea-dhcp6.conf`（Flex-Option および固定ホスト予約）
- `bind/named.conf.local`, `bind/dynamic/db.*`（プロビジョニングおよび DDNS ゾーン定義）
- `chrony/chrony.conf`（NTPサーバー設定）
- `pppoe/srv-*`（各PPPoEサービスのアカウント・ルーティング・起動設定）
- `mape-provisioning-server/server.crt / server.key`（TLS ECDSA 自己署名証明書）
- **`docker-compose.yml`**（全コンテナのオーケストレーション定義）

#### Step B-3: ホストネットワーク適用 & コンテナ一括起動 (`sudo make install`)

ホスト OS のネットワーク設定とカーネル転送パラメータを反映し、Docker コンテナを一括ビルド・起動します（要 root 権限）。

```bash
sudo make install
```

---

### 📌 設計仕様・自動算出ルール（共通リファレンス）

#### 1. VLAN ID と CE の割当ルール
- 本環境では、**1つの VLAN ID につき 1台の CE** を接続・収容します。
- 接続するCEの台数分だけ VLAN ID を割当変数（`SLAAC_DYN_VLANS` 等）に空白区切りで指定します。
- 各方式間で VLAN ID が重複しないように設定してください。

#### 2. IPv6アドレス帯の自動割り当て規則
`map-e.conf` の基幹プレフィックス `BASE_SUBNET`（統一例: `2001:db8`）に対し、指定した VLAN ID から以下のように各セグメントの IPv6 アドレス帯が自動算出・設定されます。

| 接続方式 | 設定変数名 | 自動割り当てされるIPv6アドレス帯 | 設定例（VLAN ID: `101`, `BASE_SUBNET="2001:db8"`） |
|---|---|---|---|
| **① SLAAC動的** | `SLAAC_DYN_VLANS` | `${BASE_SUBNET}:1<VLAN ID>::/64` | `2001:db8:1101::/64` （BR側: `::ff0a`） |
| **② DHCPv6-PD動的** | `PD_DYN_VLANS` | `${BASE_SUBNET}:2000::/40` （全PD VLAN共有、`/56`ずつ各CEへ委譲） | 共通委譲プール: `2001:db8:2000::/40` |
| **③ SLAAC固定** | `SLAAC_FIX_VLANS` | `${BASE_SUBNET}:3<VLAN ID>::/64` | `2001:db8:3105::/64` （BR側: `::ff0d`） |
| **④ DHCPv6-PD固定** | `PD_FIX_VLANS` | ②共通プールより動的委譲。<br>Option 94ダミーPrefix: `${BASE_SUBNET}:4000::/56` | 共通委譲プールより動的委譲 |

#### 3. Kea flex-option: /16 プール固定によるアドレス重複防止
- Kea DHCPv6 の flex-option モジュールによる Option 94 合成において、バイト単位抽出による安全な払い出しを担保するため、`BR_IPV4_POOL` は **/16 固定**（例: `198.51.0.0/16`）としています。
- 第3オクテットに委譲プレフィックス可変部、第4オクテットに DUID/MAC 末尾をマッピングすることで、CE 間の IPv4 重複を確実に防止します。

#### 4. BIND9 動的ゾーンと DDNS (RFC 2136) 自動登録
- `ddns.conf` や `bind/` 設定ファイルに定義されたドメイン（例: `p-ns.flets-west.jp` 等）のゾーンファイルは、`make generate` 時に `bind/dynamic/db.<domain>` として自動作成されます。
- CE 接続時にプロビジョニングサーバーや Kea フック経由で TSIG 認証を用いた DDNS（RFC 2136）動的更新が行われ、IPv6 / IPv4 レコードが自動登録されます。

---

## 3. セットアップ完了後の動作確認

### 1. Web 統合ダッシュボードでの確認（推奨）

ブラウザから以下の URL にアクセスします。

```text
http://<サーバー管理IP>:1600/
```

* **接続中クライアント一覧**:
  SLAAC, DHCPv6-PD, 固定IP, PPPoE の接続中 CE 一覧、払い出し IPv4/IPv6、DUID、VLAN、接続時刻をリアルタイム表示。
* **デーモンステータス**:
  DNS, DHCPv6, RADVD, NTP, MAP-E プロビジョニング, PPPoE サーバー群のヘルス状態（UP / DOWN）を一目で確認。
* **各種診断ツール**:
  ルーティングテーブル閲覧 (`/tool_routes.html`)、インターフェース一覧 (`/tool_interfaces.html`)、ログビューワー (`/tool_logs.html`) をブラウザ上で操作可能。

### 2. CLI によるコンテナ状態の確認

すべてのコンテナが `Up` または `running` になっていることを確認します。

```bash
docker compose ps
```

*出力例*:
```text
NAME                       IMAGE                                             STATUS
axosyslog                  map-e/axosyslog:latest                            Up (healthy)
bind9                      internetsystemsconsortium/bind9:9.21              Up (healthy)
central-nat                map-e/central-nat:latest                          Up
chrony                     map-e/chrony:latest                               Up (healthy)
kea-dhcp6                  docker.cloudsmith.io/isc/docker/kea-dhcp6:2.6.1   Up (healthy)
mape-provisioning-server   map-e/provisioning-server:latest                  Up (healthy)
pppoe-server-ocn           map-e/pppoe-server:latest                         Up
pppoe-server-vpn_group1    map-e/pppoe-server:latest                         Up
radvd                      map-e/radvd:latest                                Up
web-dashboard              map-e/web-dashboard:latest                        Up (healthy)
```

> [!NOTE]
> **`central-nat` コンテナの待機動作について**:
> `central-nat` コンテナは、`NAT_ENABLED="0"`（デフォルト無効）の設定であっても起動（`Up`）した状態で常駐します。
> 起動時（`nat/entrypoint.sh`）にホスト上の古い NAT ルールを自動クリーンアップした上で、「`Central NAT is disabled.`」と出力して **iptables による NAT 変換（MASQUERADE 等）は一切行わずに待機** します。
> コンテナが常駐していることで、WebUI（「⚙ 設定編集」）から Central NAT の有効/無効を切り替えた際にも、Compose 経由でシームレスに反映されます。

### 3. ネットワークインターフェースの確認

VLAN サブインターフェース（例: `eth1.101` 等）や MAP-E トンネル用仮想インターフェース（`dummy0`, `dummy1`）が作成されているか確認します。

```bash
ip link show
```

### 4. ログの確認方法

本環境では、各コンテナの出力が Syslog コンテナ（`axosyslog`）へ送られ `/var/log/` 配下に集約・分類されると同時に、Docker のデュアルロギング機能（Dual Logging Cache）により `docker compose logs` コマンドでも閲覧可能です。

* **方法1: Web ダッシュボードで確認（推奨）**:
  ブラウザで `http://<サーバー管理IP>:1600/tool_logs.html` を開くと、サービス別・ログレベル別にリアルタイム閲覧およびキーワード検索が可能です。

* **方法2: ホスト OS の syslog ファイルを直接確認 (`/var/log/`)**:
  `axosyslog` がサービス別に整理して書き出しているログファイルを直接 `tail -f` で追跡できます。
  ```bash
  # Kea DHCPv6 ログ
  tail -f /var/log/kea/kea-dhcp6.log

  # MAP-E プロビジョニングサーバーログ
  tail -f /var/log/mape-provisioning-server.log

  # RADVD / BIND9 / PPPoE ログ
  tail -f /var/log/radvd.log
  tail -f /var/log/bind9.log
  tail -f /var/log/pppoe.log
  ```

* **方法3: Docker コマンドで確認**:
  Docker Engine のデュアルロギング（キャッシュ機能）により、`syslog` ドライバ転送時でも `docker compose logs` でコンテナ出力を追跡できます。
  ```bash
  # 全サービスのログをリアルタイム追跡
  docker compose logs -f

  # 特定サービス（例: Kea DHCPv6, プロビジョニングサーバー）のログ追跡
  docker compose logs -f kea-dhcp6
  docker compose logs -f mape-provisioning-server
  ```

---

## 4. 運用・管理コマンド集

### 設定変更時の再適用手順（パラメータ修正時）

* **方法A: Web ダッシュボードから行う場合（推奨）**:
  ブラウザで `http://<サーバー管理IP>:1600/config.html` を開き、**「⚙ 設定編集」** からパラメータ（VLAN, IP, 固定IP, DDNS, PPPoE等）を編集後、画面下部の **「設定を保存して再生成・適用」** を実行するだけで反映されます。

* **方法B: CUI / CLI で設定ファイルを直接編集する場合**:
  `map-e.conf`, `map-e-static-ip.conf`, `ddns.conf`, `pppoe.conf` 等を編集した場合の反映手順：
  ```bash
  # 1. 一般ユーザーで設定ファイル & docker-compose.yml を再生成
  make generate

  # 2. ホストネットワークとコンテナへ一括反映 (要 sudo)
  sudo make install
  ```

> [!TIP]
> 特定のコンテナ設定のみを再読み込みさせたい場合は、`make generate` 後に該当コンテナを再起動するだけでも反映されます：
> ```bash
> docker compose restart bind9
> ```

### CE（新規VLAN）の追加手順

新しい VLAN ID を追加して収容 CE 数を増やす場合：

1. **Web ダッシュボード または `map-e.conf` の対象方式変数に新しい VLAN ID を追記**
   ```bash
   # 例: SLAAC動的（101 102）に VLAN 108 を追加
   SLAAC_DYN_VLANS="101 102 108"
   ```
2. **設定の適用**
   - WebUI の場合は「設定を保存して再生成・適用」をクリック。
   - CUI の場合は `make generate && sudo make install` を実行。
   *新 VLAN のサブインターフェース（例: `eth1.108`）が自動作成され、関連コンテナが更新されます。*

### コンテナの一括起動・停止

```bash
# Web ダッシュボードのみ先行起動
make web-dashboard

# 全コンテナの一括起動 (バックグラウンド)
make up
# または: docker compose up -d

# 全コンテナの一括停止
make down
# または: docker compose down
```

### 環境状態の保存とアーカイブ作成 (`archive`)

現在のネットワーク状態・コンテナ状態を `STATUS.TXT` に記録し、リソース一式を圧縮保存します。

```bash
make archive
# ➔ STATUS.TXT, MAP-E.tar.xz が生成されます
```

### クリーンアップ (`clean`)

生成された設定ファイル群や中間生成物を削除します。

```bash
make clean
```
