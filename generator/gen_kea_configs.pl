#!/usr/bin/perl
use strict;
use warnings;
use File::Basename;
use FindBin qw($RealBin);
use lib $RealBin;
use MapeCommon qw(
    ipv4_to_hex
    ipv6_to_hex
    calc_br_ipv6
    calc_vlan_prefix
    build_vlan_segments
    build_psid_hex
    load_static_ips
    render_template_file
    find_template
    check_config
);

# ----------------------------------------------------------------------------
# map-e.conf の読み込みと検証 (共通ロジックは MapeCommon::check_config)
# ----------------------------------------------------------------------------
my $script_dir = dirname(__FILE__);
check_config("$script_dir/../map-e.conf");

my $mode = shift @ARGV || 'dhcp6';

if ($mode eq 'hook') {
    my $template = find_template('kea-map-e-hook.tmpl', $script_dir, 'kea-dhcp6');
    die "Error: Template file 'kea-map-e-hook.tmpl' not found.\n" unless $template;
    open my $fh, '<', $template or die "Cannot open template $template: $!";
    while (<$fh>) { print $_ }
    close $fh;
    exit;

} elsif ($mode eq 'dhcp6') {
    my $mape_if     = $ENV{MAPE_IF};
    my $base_subnet = $ENV{BASE_SUBNET};
    my $dns_ip      = $ENV{MAPE_DNS_IP};
    my $ntp_ip      = $ENV{MAPE_NTP_IP};
    my $domain      = $ENV{MAPE_DOMAIN_SEARCH};
    my $slaac_br_base       = $ENV{SLAAC_BR_BASE};
    my $slaac_fix_br_prefix = $ENV{SLAAC_FIX_BR_PREFIX};
    my $pd_pool             = $ENV{PD_POOL};
    my $pd_fix_pool         = $ENV{PD_FIX_POOL};

    # ---------------------------------------------------------------
    # BR (Border Relay) IPv6アドレスの算出 (共通ロジックは MapeCommon::calc_br_ipv6)
    # ---------------------------------------------------------------
    my $br_prefix = $ENV{BR_PREFIX};
    my $br_ipv4   = $ENV{BR_IPV4_ADDR};
    my $br_ipv6   = calc_br_ipv6(
        br_ipv6      => $ENV{BR_IPV6},
        br_prefix    => $br_prefix,
        br_ipv4_addr => $br_ipv4,
    );

    # ---------------------------------------------------------------
    # MAP-E ルール (s46-rule) のパラメータ
    # 既定値は実商用OCNバーチャルコネクトと同じ比率
    # (ipv6PrefixLength - ipv4PrefixLength = 18, eaBitLength + ipv4PrefixLength = 38,
    #  ipv6PrefixLength + eaBitLength = MAPE_PD_DELEGATED_LEN)
    # HTTPプロビジョニングサーバ側 (mape-provisioning-server) が返す値とも一致させること。
    # ---------------------------------------------------------------
    my $rule_ipv6_prefix = $ENV{MAPE_RULE_IPV6_PREFIX} || "$base_subnet:$pd_pool" . "::";
    my $rule_ipv6_len     = $ENV{MAPE_RULE_IPV6_LEN}     || 38;
    my $rule_ipv4_prefix  = $ENV{MAPE_RULE_IPV4_PREFIX}  || "100.126.0.0";
    my $rule_ipv4_len     = $ENV{MAPE_RULE_IPV4_LEN}     || 20;
    my $delegated_len     = $ENV{MAPE_PD_DELEGATED_LEN}  || 56;
    my $ea_len            = $delegated_len - $rule_ipv6_len;
    my $rule_data = "128, $ea_len, $rule_ipv4_len, $rule_ipv4_prefix, $rule_ipv6_prefix/$rule_ipv6_len";

    # ---------------------------------------------------------------
    # VLANセグメント一覧の組み立て (①②③④)
    # ---------------------------------------------------------------
    my @segs = build_vlan_segments([
        { vlans => $ENV{SLAAC_DYN_VLANS}, type => 'slaac', base => $slaac_br_base },
        { vlans => $ENV{PD_DYN_VLANS},    type => 'pd',    base => $pd_pool },
        { vlans => $ENV{SLAAC_FIX_VLANS}, type => 'slaac', base => $slaac_fix_br_prefix },
        { vlans => $ENV{PD_FIX_VLANS},    type => 'pd',    base => $pd_fix_pool },
    ]);

    my $ifaces = join(", ", map { "\"$mape_if.$_->{v}\"" } sort { $a->{v} <=> $b->{v} } @segs);

    my $static_conf_file = "$script_dir/../map-e-static-ip.conf";
    if (! -f $static_conf_file) {
        $static_conf_file = "/etc/map-e-static-ip.conf";
    }
    my %static_ips = load_static_ips($static_conf_file);

    my @blocks;
    my $id = 1;
    my $dummy_pd_reservation_added = 0;
    foreach my $s (sort { $a->{v} <=> $b->{v} } @segs) {
        my $prefix = calc_vlan_prefix($base_subnet, $s->{b}, $s->{v});   # 例: "5f00:3aa:1901"
        my $pd_pool_prefix = "$base_subnet:$s->{b}::";
        my $pd_part = ($s->{t} eq "pd")
            ? "[\n                { \"prefix\": \"$pd_pool_prefix\", \"prefix-len\": 40, \"delegated-len\": $delegated_len }\n            ]"
            : "[ ]";

        my $is_fixed = ($s->{b} ne $slaac_br_base && $s->{b} ne $pd_pool);
        my $client_class_line = $is_fixed ? ",\n                    \"client-class\": \"KNOWN\"" : "";

        my $add_dummy_pd_reservation = ($s->{t} eq "pd" && !$dummy_pd_reservation_added);
        my $res_json = get_reservations_json(
            $s, $base_subnet, $br_ipv6, $slaac_fix_br_prefix, $pd_pool, $pd_fix_pool,
            \%static_ips, $add_dummy_pd_reservation
        );
        $dummy_pd_reservation_added = 1 if $add_dummy_pd_reservation;

        my $block = <<~"SUBNET_BLOCK";
                {
                    "id": $id,
                    "subnet": "${prefix}::/64",
                    "interface": "$mape_if.$s->{v}",
                    "pools": [ ],
                    "pd-pools": $pd_part$client_class_line$res_json
                }
                SUBNET_BLOCK
        $block =~ s/\n$//;
        push @blocks, $block;
        $id++;
    }

    # ---------------------------------------------------------------
    # テンプレートへの置換値一覧
    # ---------------------------------------------------------------
    # Kea公式コンテナでは /usr/lib/kea/hooks、ホストDebianでは /usr/lib/x86_64-linux-gnu/kea/hooks
    my $kea_hooks_dir = $ENV{KEA_HOOKS_DIR} || "/usr/lib/kea/hooks";

    my %v = (
        KEA_HOOKS_DIR      => $kea_hooks_dir,
        KEA_INTERFACES     => $ifaces,
        KEA_SUBNET6_BLOCKS => join(",\n", @blocks),
        MAPE_DNS_IP        => $dns_ip,
        MAPE_NTP_IP        => $ntp_ip,
        MAPE_DOMAIN_SEARCH => $domain,
        KEA_FLEX_OPTIONS   => get_flex_options_json($br_ipv6, $base_subnet, $slaac_br_base, $slaac_fix_br_prefix, $pd_pool, @segs),
        KEA_CLIENT_CLASSES => get_client_classes_json($slaac_br_base, $slaac_fix_br_prefix, $pd_pool, $pd_fix_pool, $mape_if, @segs),
    );

    my $template = find_template('kea-dhcp6.conf.tmpl', $script_dir, 'kea-dhcp6');
    die "Error: Template file 'kea-dhcp6.conf.tmpl' not found.\n" unless $template;
    print render_template_file($template, { %ENV, %v });

} else {
    die "Usage: $0 [dhcp6|hook]\n";
}

# ----------------------------------------------------------------------------
# ヘルパー関数群 (Option 94生成、CE向け固定予約/分類)
# (ipv4_to_hex, ipv6_to_hex は MapeCommon からインポート)
# ----------------------------------------------------------------------------

sub calculate_option94_fixed_hex {
    my (%args) = @_;
    my $br_hex = ipv6_to_hex($args{br_ipv6});
    my $br_sub_option = "005a0010" . $br_hex;
    
    my $flags = "00";
    my $ea_len = "00";
    my $v4_len = sprintf("%02x", $args{mask} || 32);
    my $v4_hex = ipv4_to_hex($args{ip});
    
    # 商用 OCN 実機仕様準拠: 固定IP (SLAAC固定 / PD固定) は接続方式を問わず一律 /56 (0x38)
    my $v6_len = "38"; # 56bit = 0x38
    my $prefix;
    if ($args{is_slaac}) {
        my $slaac_fix_pfx = $ENV{SLAAC_FIX_BR_PREFIX} || "3000";
        my $hextet = hex($slaac_fix_pfx) + ($args{vlan} || 0);
        $prefix = sprintf("%s:%x::", $args{base_subnet}, $hextet);
    } else {
        my $pd_fix_pool = $ENV{PD_FIX_POOL} || "4000";
        $prefix = "$args{base_subnet}:$pd_fix_pool" . "::";
    }
    my $v6_prefix_hex = substr(ipv6_to_hex($prefix), 0, 14); # 56bit = 7 bytes = 14 hex chars
    
    my $rule_payload = $flags . $ea_len . $v4_len . $v4_hex . $v6_len . $v6_prefix_hex;
    my $rule_len_hex = sprintf("%04x", length($rule_payload) / 2);
    my $rule_sub_option = "0059" . $rule_len_hex . $rule_payload;
    
    return $br_sub_option . $rule_sub_option;
}

sub get_reservations_json {
    my ($s, $base_subnet, $br_ipv6, $slaac_fix_br_prefix, $pd_pool, $pd_fix_pool, $static_ips_ref, $add_dummy_pd_reservation) = @_;
    my %static_ips = %{$static_ips_ref};
    my @res_blocks;
    
    my $is_slaac_fix = ($s->{t} eq "slaac" && $s->{b} eq $slaac_fix_br_prefix);
    my $is_pd_fix    = ($s->{t} eq "pd" && $s->{b} eq $pd_fix_pool);
    
    return "" unless $is_slaac_fix || $is_pd_fix || $add_dummy_pd_reservation;

        if ($add_dummy_pd_reservation) {
            push @res_blocks, <<~"RESBLOCK";
                {
                    "hw-address": "02:00:00:00:00:01",
                    "prefixes": ["${base_subnet}:${pd_pool}::/56"]
                }
                RESBLOCK
            $res_blocks[-1] =~ s/\n$//;  # 末尾改行を除去
        }
    
    foreach my $mac (sort keys %static_ips) {
        my $ent = $static_ips{$mac};
        my ($ip, $mask) = ref($ent) eq 'HASH' ? ($ent->{ip}, $ent->{mask}) : ($ent, 32);
        my $opt94_hex = calculate_option94_fixed_hex(
            ip          => $ip,
            mask        => $mask,
            br_ipv6     => $br_ipv6,
            is_slaac    => $is_slaac_fix,
            base_subnet => $base_subnet,
            vlan        => $s->{v},
            base        => $s->{b}
        );
        
        push @res_blocks, <<~"RESBLOCK";
                {
                    "hw-address": "$mac",
                    "option-data": [
                        {
                            "code": 94,
                            "csv-format": false,
                            "data": "$opt94_hex"
                        }
                    ]
                }
                RESBLOCK
        $res_blocks[-1] =~ s/\n$//;
    }
    
    if (@res_blocks) {
        return ",\n            \"reservations\": [\n" . join(",\n", @res_blocks) . "\n            ]";
    }
    return "";
}

# ----------------------------------------------------------------------------
# _build_slaac_flex_option
#   SLAAC 動的（①）および SLAAC 固定（③）の UNKNOWN フォールバック用
#   Option 94 flex-option ブロック（JSON 断片文字列）を返す。
#
#   Option 90 (BR)    : 005a0010 + br_hex (20B)
#   Option 89 (Rule)  : 0059000b + 00(flags) 10(ea-len=16) 10(v4-len=16)
#                       + pool_upper_hex(2B) + 28(v6-len=40) + v6_pfx(5B)
#   Option 93 (Ports) : 005d000400000000 (offset=0, psid-len=0, psid=0)
# ----------------------------------------------------------------------------
sub _build_slaac_flex_option {
    my (%args) = @_;
    my ($br_hex, $pool_upper_hex, $base_subnet, $base, $vlan, $class_name) =
        @args{qw(br_hex pool_upper_hex base_subnet base vlan class_name)};

    my $v6_prefix_hex = substr(ipv6_to_hex("${base_subnet}:${base}::"), 0, 10);
    my $opt94_hex    = "005a0010${br_hex}0059000b001010${pool_upper_hex}28${v6_prefix_hex}005d000400000000";

    return <<~"BLOCK";
                    {
                        "code": 94,
                        "supersede": "0x${opt94_hex}",
                        "csv-format": false,
                        "client-class": "$class_name"
                    }
                    BLOCK
}

# ----------------------------------------------------------------------------
# _build_pd_flex_option
#   PD 動的（②）および PD 固定（④）の UNKNOWN フォールバック用
#   Option 94 flex-option ブロック（JSON 断片文字列）を返す。
#
#   Option 90 (BR)    : 005a0010 + br_hex (20B)
#   Option 89 (Rule)  : 0059000b + 00(flags) 10(ea-len=16) 10(v4-len=16)
#                       + pool_upper_hex(2B) + 28(v6-len=40) + v6_pfx(5B)
#   Option 93 (Ports) : 005d000400000000 (offset=0, psid-len=0, psid=0)
# ----------------------------------------------------------------------------
sub _build_pd_flex_option {
    my (%args) = @_;
    my ($br_hex, $pool_upper_hex, $base_subnet, $base, $class_name) =
        @args{qw(br_hex pool_upper_hex base_subnet base class_name)};

    # 委譲プレフィックス /56 から EA-bits (16bit) を抽出するため Rule Prefix は /40 (10 hex文字=5B)
    my $pd_rule_hex = substr(ipv6_to_hex("${base_subnet}:${base}::"), 0, 10);
    my $opt94_hex   = "005a0010${br_hex}0059000b001010${pool_upper_hex}28${pd_rule_hex}005d000400000000";

    return <<~"BLOCK";
                    {
                        "code": 94,
                        "supersede": "0x${opt94_hex}",
                        "csv-format": false,
                        "client-class": "$class_name"
                    }
                    BLOCK
}

sub get_flex_options_json {
    my ($br_ipv6, $base_subnet, $slaac_br_base, $slaac_fix_br_prefix, $pd_pool, @segs) = @_;
    my @opt_blocks;

    my $br_hex = ipv6_to_hex($br_ipv6);

    # BR_IPV4_POOL のパース (/16 固定: アドレス衝突防止のため)
    my $pool_raw = $ENV{BR_IPV4_POOL};
    my ($pool_ip, $pool_mask) = split(/\//, $pool_raw);
    $pool_mask ||= $ENV{BR_IPV4_MASK} || 16;
    my @o = split(/\./, $pool_ip);
    if ($pool_mask != 16 || scalar(@o) != 4) {
        die "[ERROR] BR_IPV4_POOL は /16 固定です (指定値: $pool_raw)\n";
    }
    my $pool_upper_hex = sprintf("%02x%02x", $o[0], $o[1]);

    foreach my $s (@segs) {
        my $v = $s->{v};
        if ($s->{t} eq 'slaac') {
            next if $s->{b} ne $slaac_br_base;
            my $class_name = "class-slaac-dyn-$v";
            my $block = _build_slaac_flex_option(
                br_hex         => $br_hex,
                pool_upper_hex => $pool_upper_hex,
                base_subnet    => $base_subnet,
                base           => $s->{b},
                vlan           => $v,
                class_name     => $class_name,
            );
            $block =~ s/\n$//;
            push @opt_blocks, $block;
        } elsif ($s->{t} eq 'pd') {
            next if $s->{b} ne $pd_pool;
            my $class_name = "class-pd-dyn-$v";
            my $block = _build_pd_flex_option(
                br_hex         => $br_hex,
                pool_upper_hex => $pool_upper_hex,
                base_subnet    => $base_subnet,
                base           => $s->{b},
                class_name     => $class_name,
            );
            $block =~ s/\n$//;
            push @opt_blocks, $block;
        }
    }

    return join(",\n", @opt_blocks);
}

sub get_client_classes_json {
    my ($slaac_br_base, $slaac_fix_br_prefix, $pd_pool, $pd_fix_pool, $mape_if, @segs) = @_;
    my @class_blocks;

    foreach my $s (@segs) {
        my $v = $s->{v};
        my ($name, $test);
        if ($s->{t} eq 'slaac') {
            next if $s->{b} ne $slaac_br_base;
            $name = "class-slaac-dyn-$v";
            $test = "pkt.iface == '$mape_if.$v'";
        } elsif ($s->{t} eq 'pd') {
            next if $s->{b} ne $pd_pool;
            $name = "class-pd-dyn-$v";
            $test = "pkt.iface == '$mape_if.$v'";
        } else {
            next;
        }
        my $block = <<~"BLOCK";
                {
                    "name": "$name",
                    "test": "$test"
                }
                BLOCK
        $block =~ s/\n$//;
        push @class_blocks, $block;
    }

    return join(",\n", @class_blocks);
}
