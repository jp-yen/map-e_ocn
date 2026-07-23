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
    ipv4_to_hex_hextets
    calc_br_ipv6
    calc_vlan_prefix
    parse_vlan_list
    build_vlan_segments
    find_template
    load_conf_file
    render_template_file
);

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
# IPv4ドット表記 -> "xxxx:xxxx" 形式のhextetペアに変換 (BR_IPV6計算などで使用)
# ---------------------------------------------------------------------------
sub ipv4_to_hex_hextets {
    my ($ipv4) = @_;
    my @o = split(/\./, $ipv4);
    die "Invalid IPv4 address: $ipv4\n" unless scalar(@o) == 4;
    return sprintf("%02x%02x:%02x%02x", @o);
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
            $val =~ s/\s*#.*$//;   # コメント削除
            $val =~ s/\s+$//;      # 末尾空白削除
            $val =~ s/^["'](.*)["']$/$1/; # クォート削除
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
# ---------------------------------------------------------------------------
sub _derive_provisioning_vars {
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

1;
