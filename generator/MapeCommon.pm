package MapeCommon;
# generator/MapeCommon.pm
#
# gen_kea_configs.pl / gen_mape_mod.pl / gen_system_mod.pl / gen_bind_configs.pl /
# gen_chrony_config.pl の間で重複していた共通処理をまとめたモジュール。
#
#   - ipv4_to_hex_hextets : IPv4ドット表記 -> 16進hextetペア変換
#   - calc_br_ipv6        : BR_IPV6 の算出 (BR_PREFIX + BR_IPV4_ADDR の16進埋め込み)
#   - find_template       : テンプレートファイルの探索 (generator/ 起点の典型的な配置パターン)
#   - load_conf_file      : map-e.conf の読み込み + ${VAR} 参照の再帰展開 (-> %ENV)
#
# 注意: 従来 gen_mape_mod.pl と gen_system_mod.pl は BR_IPV6 を
#   "$prefix:$hex1:$hex2" (コロン1個) という形式で個別に組み立てており、
#   gen_kea_configs.pl の "$prefix" . "::" . "$hex1:$hex2" (コロン2個/省略記法込み)
#   と食い違っていた。方針.md が示す正しい値は後者
#   (例: 5f00:3aa:a001::647f:ffff) であるため、本モジュールへの一本化に
#   あわせて calc_br_ipv6() 側の実装に統一した。

use strict;
use warnings;
use Exporter 'import';

our @EXPORT_OK = qw(
    ipv4_to_hex
    ipv4_to_hex_hextets
    ipv6_to_hex
    calc_br_ipv6
    calc_vlan_prefix
    parse_vlan_list
    build_vlan_segments
    build_psid_hex
    find_template
    load_conf_file
    load_static_ips
    render_template_file
    check_required_vars
    check_config
    @REQUIRED_CONFIG_VARS
);

our @REQUIRED_CONFIG_VARS = qw(
    MGT_IF
    MGT_IP
    MGT_GW
    SYSTEM_DNS
    SYSTEM_NTP
    DOMAIN
    MAPE_IF
    PPPOE_SERVER_BASE_IP
    WEB_DASHBOARD_PORT
    BR_IF
    PROV_IF
    BASE_SUBNET
    BR_PREFIX
    BR_IPV4_ADDR
    BR_IPV4_POOL
    MAPE_PROV_PREFIX
    MAPE_DNS_IP
    MAPE_NTP_IP
    MAPE_PROV_IP
    MAPE_DOMAIN_SEARCH
    SLAAC_BR_BASE
    SLAAC_BR_SUFFIX
    PD_POOL
    SLAAC_FIX_BR_PREFIX
    SLAAC_FIX_BR_SUFFIX
    PD_FIX_POOL
);

sub check_config {
    my ($conf_path) = @_;
    if (defined $conf_path && length $conf_path) {
        if (! -f $conf_path) {
            die "\n" .
                "=" x 80 . "\n" .
                "[ERROR] 設定ファイルが見つかりません: $conf_path\n" .
                "=" x 80 . "\n";
        }
        load_conf_file($conf_path);
    }
    check_required_vars(@REQUIRED_CONFIG_VARS);

    # 少なくとも 1 つの VLAN (MAP-E または PPPoE) が設定されていることを検証
    my @all_configured_vlans = (
        parse_vlan_list($ENV{SLAAC_DYN_VLANS} || ''),
        parse_vlan_list($ENV{PD_DYN_VLANS} || ''),
        parse_vlan_list($ENV{SLAAC_FIX_VLANS} || ''),
        parse_vlan_list($ENV{PD_FIX_VLANS} || ''),
        parse_vlan_list($ENV{PPPOE_VLANS} || ''),
    );
    if (!@all_configured_vlans) {
        my $msg = "\n" .
            "=" x 80 . "\n" .
            "[ERROR] map-e.conf VLAN未設定エラー\n" .
            "MAP-E または PPPoE の収容 VLAN が 1 つも設定されていません。\n" .
            "SLAAC_DYN_VLANS, PD_DYN_VLANS, SLAAC_FIX_VLANS, PD_FIX_VLANS, PPPOE_VLANS のいずれかに VLAN 番号を設定してください。\n" .
            "=" x 80 . "\n";
        die $msg;
    }

    # PPPoE サービス名の検証 (統合型 PPPOE_SERVICES)
    my @service_errors;
    my @svc_checks;
    if (defined $ENV{PPPOE_SERVICES} && $ENV{PPPOE_SERVICES} =~ /\S/) {
        push @svc_checks, ["PPPOE_SERVICES", split(/\s+/, $ENV{PPPOE_SERVICES})];
    }

    foreach my $item (@svc_checks) {
        my ($var_name, @svcs) = @$item;
        my %seen_in_var;
        foreach my $s (@svcs) {
            next unless length($s);
            if (length($s) > 11) {
                push @service_errors, "$var_name: サービス名 '$s' が " . length($s) . " 文字です。最大 11 文字以下にしてください（Linux VRF デバイス名 'vrf-<サービス名>' の 15 文字制限のため）。";
            }
            if ($s !~ /^[a-zA-Z0-9_-]+$/) {
                push @service_errors, "$var_name: サービス名 '$s' に不正な文字が含まれています。スペースは使用できません（半角英数字、_、- を使用してください）。";
            }
            if ($seen_in_var{lc($s)}++) {
                push @service_errors, "$var_name: サービス名 '$s' が重複しています（大文字小文字を区別せず重複不可）。";
            }
        }
    }
    if (@service_errors) {
        my $msg = "\n" .
            "=" x 80 . "\n" .
            "[ERROR] map-e.conf PPPoE サービス定義エラー\n" .
            join("", map { "  - $_\n" } @service_errors) . "\n" .
            "Linux VRF デバイス名制限 (IFNAMSIZ=15文字) および Docker 起動の衝突を防ぐため、処理を中断しました。\n" .
            "サービス名は 11 文字以内で設定してください（例: VPN_Group1, OCN）。\n" .
            "=" x 80 . "\n";
        die $msg;
    }

    # BR_IPV4_POOL の /16 固定検証 (アドレス衝突防止のため)
    my $pool_raw = $ENV{BR_IPV4_POOL} || '';
    my ($pool_ip, $pool_mask) = split(/\//, $pool_raw);
    $pool_mask ||= $ENV{BR_IPV4_MASK} || '';
    
    my @pool_octets = split(/\./, $pool_ip || '');
    if ($pool_mask ne '16' || scalar(@pool_octets) != 4) {
        my $msg = "\n" .
            "=" x 80 . "\n" .
            "[ERROR] map-e.conf BR_IPV4_POOL 設定エラー\n" .
            "BR_IPV4_POOL はアドレス衝突防止のため /16 固定である必要があります (現在の設定: '$pool_raw')。\n" .
            "Kea flex-option の式言語制約により、/16 以外のプレフィックス長 (/24, /17 等) では\n" .
            "クライアント間のアドレス衝突やプール逸脱が発生するため使用できません。\n" .
            "map-e.conf で BR_IPV4_POOL=\"x.x.0.0/16\" (例: \"10.248.0.0/16\") のように /16 で設定してください。\n" .
            "=" x 80 . "\n";
        die $msg;
    }

    return 1;
}

sub check_required_vars {
    my (@vars) = @_;
    my @missing;
    foreach my $v (@vars) {
        if (!exists $ENV{$v} || !defined $ENV{$v} || $ENV{$v} =~ /^\s*$/) {
            push @missing, $v;
        }
    }
    if (@missing) {
        my $conf_hint = $ENV{MAPE_CONF_PATH} || './map-e.conf';
        my $msg = "\n" .
            "=" x 80 . "\n" .
            "[ERROR] map-e.conf 設定パラメータ不足エラー\n" .
            "=" x 80 . "\n" .
            "以下の必須パラメータが map-e.conf で未定義、または空です:\n" .
            join("", map { "  - $_\n" } @missing) . "\n" .
            ">>> 対処方法 <<<\n" .
            "  $conf_hint を開き、上記の各パラメータを宣言してください。\n" .
            "  例: echo 'PARAM_NAME=\"値\"' >> $conf_hint\n" .
            "\n" .
            "作業者の意図しない不整合や動作を防ぐため、処理を中断しました。\n" .
            "=" x 80 . "\n";
        die $msg;
    }
    return 1;
}

sub render_template_text {
    my ($content, $vars_ref) = @_;
    my %vars = %{ $vars_ref || {} };

    $content =~ s/\$\{(\w+)\}/exists $vars{$1} ? (defined $vars{$1} ? $vars{$1} : '') : $&/ge;
    return $content;
}

sub render_template_file {
    my ($path, $vars_ref) = @_;
    open(my $fh, '<', $path) or die "Cannot open template file '$path': $!";
    my $content = do { local $/; <$fh> };
    close($fh);

    return render_template_text($content, $vars_ref);
}

# ---------------------------------------------------------------------------
# IPv4ドット表記 -> 8文字16進文字列 ("0a000101" 等)
# ---------------------------------------------------------------------------
sub ipv4_to_hex {
    my ($ipv4) = @_;
    my @o = split(/\./, $ipv4);
    die "Invalid IPv4 address: $ipv4\n" unless scalar(@o) == 4;
    return sprintf("%02x%02x%02x%02x", @o);
}

# ---------------------------------------------------------------------------
# IPv4ドット表記 -> "xxxx:xxxx" 形式のhextetペアに変換 (BR_IPV6計算などで使用)
# ---------------------------------------------------------------------------
sub ipv4_to_hex_hextets {
    my ($ipv4) = @_;
    my @o = split(/\./, $ipv4);
    die "Invalid IPv4 address: $ipv4\n" unless scalar(@o) == 4;
    return sprintf("%02x%02x:%02x%02x", @o);
}

# ---------------------------------------------------------------------------
# IPv6アドレス -> 32文字16進文字列 (コロンなし、完全展開)
# ---------------------------------------------------------------------------
sub ipv6_to_hex {
    my ($ipv6) = @_;
    if ($ipv6 !~ /::/ && $ipv6 =~ tr/:/:/ < 7) {
        $ipv6 .= '::';
    }
    my @parts = split(/:/, $ipv6, -1);
    my @hextets;
    my $double_colon_idx = -1;
    for (my $i = 0; $i < @parts; $i++) {
        if ($parts[$i] eq '') {
            $double_colon_idx = $i if $double_colon_idx == -1;
        } else {
            push @hextets, hex($parts[$i]);
        }
    }
    if ($double_colon_idx != -1) {
        my $num_missing = 8 - scalar(@hextets);
        my @missing = (0) x $num_missing;
        my @first;
        for (my $i = 0; $i < $double_colon_idx; $i++) {
            if ($parts[$i] ne '') {
                push @first, hex($parts[$i]);
            }
        }
        my @last;
        for (my $i = $double_colon_idx + 1; $i < @parts; $i++) {
            if ($parts[$i] ne '') {
                push @last, hex($parts[$i]);
            }
        }
        @hextets = (@first, @missing, @last);
    }
    while (scalar(@hextets) < 8) {
        push @hextets, 0;
    }
    @hextets = @hextets[0..7];
    return sprintf("%04x%04x%04x%04x%04x%04x%04x%04x", @hextets);
}

# ---------------------------------------------------------------------------
# BR_IPV6 の算出
#   OCN仕様: BR_PREFIX (96bit) の末尾に BR_IPV4_ADDR (32bit, 16進変換) を埋め込む
#   例: BR_PREFIX=5f00:3aa:a001, BR_IPV4_ADDR=100.127.255.255
#       -> BR_IPV6 = 5f00:3aa:a001::647f:ffff
#
#   引数はハッシュで渡す:
#     br_ipv6      => 明示的に指定されたBR_IPV6 (指定があれば最優先で返す)
#     br_prefix    => BRプレフィックス (例: "5f00:3aa:a001")
#     br_ipv4_addr => BRのIPv4アドレス (例: "100.127.255.255")
#
#   br_prefix / br_ipv4_addr が揃わない場合は undef を返す。
# ---------------------------------------------------------------------------
sub calc_br_ipv6 {
    my (%args) = @_;
    return $args{br_ipv6} if $args{br_ipv6};
    return undef unless $args{br_prefix} && $args{br_ipv4_addr};
    return $args{br_prefix} . '::' . ipv4_to_hex_hextets($args{br_ipv4_addr});
}

# ---------------------------------------------------------------------------
# テンプレートファイルの探索
#   $tmpl_file  : 探すファイル名 (例: "chrony.conf.tmpl")
#   $script_dir : 呼び出し元スクリプトのディレクトリ (dirname(__FILE__))
#   @subdirs    : カレント/親ディレクトリ双方で試すサブディレクトリ名
#                 (例: "bind", "mape-provisioning-server")
#
#   カレントディレクトリ、$script_dir の親ディレクトリの双方について、
#   直下 および 指定サブディレクトリ配下、の順に存在確認を行う。
#   見つかればそのパスを、見つからなければ undef を返す。
# ---------------------------------------------------------------------------
sub find_template {
    my ($tmpl_file, $script_dir, @subdirs) = @_;

    my @search_paths = ($tmpl_file);
    push @search_paths, "$_/$tmpl_file" foreach @subdirs;
    push @search_paths, "$script_dir/../$tmpl_file";
    push @search_paths, "$script_dir/../$_/$tmpl_file" foreach @subdirs;

    foreach my $path (@search_paths) {
        return $path if -f $path;
    }
    return undef;
}

# ---------------------------------------------------------------------------
# map-e-static-ip.conf の読み込み
#   書式: <MAC>,<IP>[/<mask>] [<コメント>]
#   戻り値: MAC アドレス (小文字) -> IPv4 アドレス のハッシュ
# ---------------------------------------------------------------------------
sub load_static_ips {
    my ($file) = @_;
    my %map;
    if (-f $file) {
        open my $fh, '<', $file or die "Cannot open $file: $!";
        while (<$fh>) {
            chomp;
            next if /^\s*#/ || /^\s*$/;
            my ($mac, $ip_mask) = split(/\s*,\s*/, $_);
            if ($mac && $ip_mask && $ip_mask =~ /^\d+\.\d+\.\d+\.\d+/) {
                $mac =~ s/^\s+|\s+$//g;
                $mac = lc($mac);
                my ($ip, $mask) = split(/\//, $ip_mask);
                $ip =~ s/^\s+|\s+$//g;
                $mask = defined($mask) ? int($mask) : 32;
                $map{$mac} = { ip => $ip, mask => $mask };
            }
        }
        close $fh;
    }
    return %map;
}

# ---------------------------------------------------------------------------
# map-e.conf の読み込み + ${VAR} 参照の再帰展開
#   読み込んだ値はそのまま %ENV へ格納する (呼び出し元が export 済みの
#   環境変数と同じ扱いで使えるようにするため)。
#   Makefile 経由 (set -a; . ./map-e.conf) で既に %ENV に展開済みの場合は
#   単に上書きされるだけなので、スクリプト単体実行時のフォールバックとして
#   安全に使える。
# ---------------------------------------------------------------------------
sub load_conf_file {
    my ($config_file) = @_;
    return unless -f $config_file;

    open(my $conf, '<', $config_file) or die "Cannot open $config_file: $!";
    while (<$conf>) {
        chomp;
        next if /^\s*#/ || /^\s*$/;
        if (/^\s*(?:export\s+)?(\w+)\s*=\s*(.*)/) {
            my ($key, $val) = ($1, $2);
            if ($val =~ /^"([^"]*)"(?:\s*#.*)?$/ || $val =~ /^'([^']*)'(?:\s*#.*)?$/) {
                $val = $1;
            } else {
                $val =~ s/\s*#.*$//;   # コメント削除
                $val =~ s/\s+$//;      # 末尾空白削除
            }
            $ENV{$key} = $val;
        }
    }
    close($conf);

    # ${VAR} 参照を再帰的に展開する (最大5パス)
    for (1..5) {
        my $changed = 0;
        foreach my $k (keys %ENV) {
            if ($ENV{$k} =~ s/\$\{(\w+)\}/defined $ENV{$1} ? $ENV{$1} : $&/eg) {
                $changed = 1;
            }
        }
        last unless $changed;
    }

    # map-e.conf に明示的な定義を持たない派生変数を、読み込んだ値から算出する
    # (mape-provisioning-server.tmpl 等、複数のテンプレートが参照するため
    #  ここで一元的に計算し %ENV に格納しておく)
    _derive_provisioning_vars();

    return 1;
}

# ---------------------------------------------------------------------------
# mape-provisioning-server.tmpl が参照する派生変数の算出
#   map-e.conf 自体には定義がなく、既存の変数から計算で求める必要がある値を
#   ここでまとめて %ENV に補完する。既に(map-e.confなどで)値が設定されている
#   場合はそれを優先し、上書きしない。
#
#   - MAPE_FLAT_RULE_IPV6_PREFIX /
#     MAPE_FLAT_RULE_IPV6_LEN    : ASUS/Flat Mode 用の Rule IPv6 Prefix。
#                             VLANごとの個別プレフィックスではなく、
#                             事業者が払い出す全アドレス空間を表す
#                             BASE_SUBNET を単一のフラットな
#                             Rule IPv6 Prefix として使う
#                             (例: BASE_SUBNET=5f00:3aa (32bit)
#                              -> MAPE_RULE_IPV6_PREFIX=5f00:3aa::
#                                 MAPE_RULE_IPV6_LEN=32)
sub _derive_provisioning_vars {
    # BR_IPV4_POOL が "192.168.0.0/16" 等の CIDR 形式で指定されている場合のパース
    if ($ENV{'BR_IPV4_POOL'} && $ENV{'BR_IPV4_POOL'} =~ m{^([^/]+)/(\d+)$}) {
        $ENV{'BR_IPV4_POOL'} = $1;
        $ENV{'BR_IPV4_MASK'} = $2;
    } else {
        $ENV{'BR_IPV4_MASK'} ||= 16;
    }

    # BR_IPV6 が未算出ならここでも算出しておく (calc_br_ipv6 は
    # br_prefix/br_ipv4_addr が揃わない場合 undef を返すのでその場合は何もしない)
    $ENV{'BR_IPV6'} ||= calc_br_ipv6(
        br_ipv6      => $ENV{'BR_IPV6'},
        br_prefix    => $ENV{'BR_PREFIX'},
        br_ipv4_addr => $ENV{'BR_IPV4_ADDR'},
    );

    # MAPE_FLAT_RULE_IPV6_PREFIX / MAPE_FLAT_RULE_IPV6_LEN : BASE_SUBNET から算出
    #   ※ gen_kea_configs.pl が使う MAPE_RULE_IPV6_PREFIX / MAPE_RULE_IPV6_LEN
    #      (PD_POOL 由来の /40 委譲プレフィックス) とは全く別物であり、
    #      同名にすると gen_kea_configs.pl 側の "|| デフォルト値" が
    #      効かなくなってしまうため、必ず別名にすること。
    if ($ENV{'BASE_SUBNET'} && !defined $ENV{'MAPE_FLAT_RULE_IPV6_PREFIX'}) {
        $ENV{'MAPE_FLAT_RULE_IPV6_PREFIX'} = $ENV{'BASE_SUBNET'} . '::';
    }
    if ($ENV{'BASE_SUBNET'} && !defined $ENV{'MAPE_FLAT_RULE_IPV6_LEN'}) {
        my @hextets = split(/:/, $ENV{'BASE_SUBNET'});
        $ENV{'MAPE_FLAT_RULE_IPV6_LEN'} = scalar(@hextets) * 16;
    }

    # MAPE_PD_PREFIX : DHCPv6-PD動的セグメント用プール(PD_POOL)の接続文字列表現。
    #   mape_calc.tmpl の calc_ipv4_from_dhcp_prefix() が、CEへ委譲された
    #   /56プレフィックスとこのプール基点(/40)との差分からIPv4第4オクテットを
    #   算出する際に参照する。未定義のまま(空文字列)だと IPv6Network() が
    #   例外を送出し、常にフォールバック値(*.PD_V4_OCTET3.0)を返してしまう
    #   (=全CEが同一IPv4になる)ため、他の派生変数と同様にここで補完する。
    #   例: BASE_SUBNET=5f00:3aa, PD_POOL=2000 -> MAPE_PD_PREFIX=5f00:3aa:2000:
    if ($ENV{'BASE_SUBNET'} && $ENV{'PD_POOL'} && !defined $ENV{'MAPE_PD_PREFIX'}) {
        $ENV{'MAPE_PD_PREFIX'} = "$ENV{'BASE_SUBNET'}:$ENV{'PD_POOL'}:";
    }

    # MAPE_PD_FIX_PREFIX : ④DHCPv6-PD固定用プール(PD_FIX_POOL)の接続文字列表現。
    #   MAPE_PD_PREFIX(②用)と同じ要領で、mape_calc.pyのclassify_segment()が
    #   受信したIPv6プレフィックスを④(PD固定)と判定するために参照する。
    #   例: BASE_SUBNET=5f00:3aa, PD_FIX_POOL=4000 -> MAPE_PD_FIX_PREFIX=5f00:3aa:4000:
    if ($ENV{'BASE_SUBNET'} && $ENV{'PD_FIX_POOL'} && !defined $ENV{'MAPE_PD_FIX_PREFIX'}) {
        $ENV{'MAPE_PD_FIX_PREFIX'} = "$ENV{'BASE_SUBNET'}:$ENV{'PD_FIX_POOL'}:";
    }
}

# ---------------------------------------------------------------------------
# VLANごとのSLAACプレフィックス(4番目hextet)算出 (方針.md 3.2節)
#   4番目のhextet = <BASE>(SLAAC_BR_BASE等) + VLAN ID の10進加算。
#   例: calc_vlan_prefix("5f00:3aa", 1000, 901) -> "5f00:3aa:1901"
#
#   注意: BASE / VLAN はともに「0-9の数字のみ」で運用するルール
#   (方針.md 1章)のため、16進変換は行わず単純な数値加算でよい。
#   16進変換(hex())を挟むと別の値になってしまうため、必ずこの関数を経由すること。
#   (過去にgen_kea_configs.plがhex()を使い、radvd/interfacesと異なる
#    subnetを生成してしまっていた)
# ---------------------------------------------------------------------------
sub calc_vlan_prefix {
    my ($base_subnet, $base, $vlan) = @_;
    my $hextet = $base + $vlan;
    return "${base_subnet}:${hextet}";
}

# ---------------------------------------------------------------------------
# 空白区切りのVLANリスト文字列を、数値VLAN IDの配列にパースする。
#   数字以外のトークン（空要素・書式崩れ等）は黙って除外する。
# ---------------------------------------------------------------------------
sub parse_vlan_list {
    my ($str) = @_;
    return () unless defined $str;
    return grep { /^\d+$/ } split(/\s+/, $str);
}

# ---------------------------------------------------------------------------
# VLANセグメント定義を共通形式にまとめる
#   specs は [{ vlans => '1 2 3', type => 'slaac', base => '1000' }, ...]
#   の配列参照を想定し、各VLANを { v => VLAN ID, t => type, b => base }
#   へ展開して返す。
# ---------------------------------------------------------------------------
sub build_vlan_segments {
    my ($specs_ref) = @_;
    my @segments;

    foreach my $spec (@{ $specs_ref || [] }) {
        next unless $spec && defined $spec->{type};
        my $vlans = $spec->{vlans};
        my $type  = $spec->{type};
        my $base  = $spec->{base};

        foreach my $vlan (parse_vlan_list($vlans)) {
            push @segments, {
                v => $vlan,
                t => $type,
                b => $base,
            };
        }
    }

    return @segments;
}

# ---------------------------------------------------------------------------
# PSID 値の計算と hex 変換 (1 バイト)
#
#   SLAAC 動的 / PD 動的ともに Option 93 (PortParams) の PSID フィールドに
#   設定する 1 バイトのプレースホルダー値を生成する。
#
#   SLAAC: $base + $vlan の 10 進数値（hextet 値）の上位 1 桁を 16 進変換。
#          例: base=1000, vlan=101 -> hextet=1101 -> 上位2桁="11" -> 0x11
#   PD   : $base（文字列）の先頭 2 桁を数値として 16 進変換。
#          例: base="2000" -> 先頭2桁=20 -> 0x14
#
#   この値は Kea の flex-option 式に静的に埋め込まれる参考 PSID であり、
#   CE 側が EA-bits から自己計算した実 PSID とは別物である点に注意。
#   (Kea 式内では concat で DUID 末尾とペアにして完全な 1byte を構成する)
# ---------------------------------------------------------------------------
sub build_psid_hex {
    my (%args) = @_;
    my $type = $args{type} // '';
    my $base = $args{base} // 0;
    my $vlan = $args{vlan} // 0;

    if ($type eq 'slaac') {
        # hextet 値（10 進）の上位 2 桁を 16 進に変換
        my $hextet = $base + $vlan;
        return substr(sprintf("%04d", $hextet), 0, 2);
    } else {
        # PD: base 文字列先頭 2 桁を数値として解釈し 16 進変換
        my $psid_val = int(substr(sprintf("%04s", $base), 0, 2)) || 0;
        return sprintf("%02x", $psid_val);
    }
}

1;
