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
    my $hgw_vlans           = $ENV{HGW_VLANS};
    my $hgw_br_base         = $ENV{HGW_BR_BASE};
    my $hgw_delegated_len   = $ENV{HGW_PD_DELEGATED_LEN} || 60;
    my $hgw_fix_vlans       = $ENV{HGW_FIX_VLANS};
    my $hgw_fix_br_base     = $ENV{HGW_FIX_BR_BASE};

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

    my $delegated_len = $ENV{MAPE_PD_DELEGATED_LEN} || 56;

    # ---------------------------------------------------------------
    # VLANセグメント一覧の組み立て (①②③④⑤⑥)
    # ---------------------------------------------------------------
    my @segs = build_vlan_segments([
        { vlans => $ENV{SLAAC_DYN_VLANS}, type => 'slaac',   base => $slaac_br_base },
        { vlans => $ENV{PD_DYN_VLANS},    type => 'pd',      base => $pd_pool },
        { vlans => $ENV{SLAAC_FIX_VLANS}, type => 'slaac',   base => $slaac_fix_br_prefix },
        { vlans => $ENV{PD_FIX_VLANS},    type => 'pd',      base => $pd_fix_pool },
        { vlans => $hgw_vlans,           type => 'hgw',     base => $hgw_br_base },
        { vlans => $hgw_fix_vlans,       type => 'hgw_fix', base => $hgw_fix_br_base },
    ]);

    my $ifaces = join(", ", map { "\"$mape_if.$_->{v}\"" } sort { $a->{v} <=> $b->{v} } @segs);

    my $static_conf_file = "$script_dir/../map-e-static-ip.conf";
    if (! -f $static_conf_file) {
        $static_conf_file = "/etc/map-e-static-ip.conf";
    }
    my %static_ips = load_static_ips($static_conf_file);

    my @blocks;
    my $id = 1;
    foreach my $s (sort { $a->{v} <=> $b->{v} } @segs) {
        my $prefix = calc_vlan_prefix($base_subnet, $s->{b}, $s->{v});   # 例: "5f00:3aa:1901"
        my $pd_part = "[ ]";
        if ($s->{t} eq "pd" && $s->{b} eq $pd_pool) {
            # DHCPv6-PD動的 (②): 商用 OCN 1回線=1VLAN 仕様。第4ヘクステット上位バイトに PSID を埋め込んだ /56 を委譲
            # これにより EA-bits (18bit: 第3ヘクステット下位10bit + 第4ヘクステット上位8bit) と通知 PSID が100%一致する
            my $psid = (($s->{v} - 1) % 63) + 1;
            my $hextet4_base = sprintf("%02x00", $psid);
            my $pd_pool_prefix = "${prefix}:${hextet4_base}::";
            $pd_part = "[\n                { \"prefix\": \"$pd_pool_prefix\", \"prefix-len\": 56, \"delegated-len\": $delegated_len }\n            ]";
        } elsif ($s->{t} eq "pd" && $s->{b} eq $pd_fix_pool) {
            # DHCPv6-PD固定 (④): 回線(VLAN)固有の /56 (${prefix}::/56) を委譲 (MAC予約不要)
            my $pd_pool_prefix = "${prefix}::";
            $pd_part = "[\n                { \"prefix\": \"$pd_pool_prefix\", \"prefix-len\": 56, \"delegated-len\": $delegated_len }\n            ]";
        } elsif ($s->{t} eq "hgw") {
            # HGW配下模擬動的: 商用 OCN 1,008 ポート仕様。第4ヘクステット上位バイトに PSID を埋め込んだ /60 を委譲
            my $psid = (($s->{v} - 1) % 63) + 1;
            my $hextet4_base = sprintf("%02x10", $psid);
            my $pd_pool_prefix = "${prefix}:${hextet4_base}::";
            $pd_part = "[\n                { \"prefix\": \"$pd_pool_prefix\", \"prefix-len\": 60, \"delegated-len\": $hgw_delegated_len }\n            ]";
        } elsif ($s->{t} eq "hgw_fix") {
            # HGW配下模擬固定: 実機HGW準拠。親 /56 (${prefix}::/56) から切り出した /60 (${prefix}:10::/60) を委譲
            my $pd_pool_prefix = "${prefix}:10::";
            $pd_part = "[\n                { \"prefix\": \"$pd_pool_prefix\", \"prefix-len\": 60, \"delegated-len\": $hgw_delegated_len }\n            ]";
        }

        my $opt_data_json = get_fixed_subnet_option_data_json(
            $s, $base_subnet, $br_ipv6, $slaac_fix_br_prefix, $pd_fix_pool,
            \%static_ips, $hgw_fix_br_base
        );

        my $block = <<~"SUBNET_BLOCK";
                {
                    "id": $id,
                    "subnet": "${prefix}::/64",
                    "interface": "$mape_if.$s->{v}",
                    "pools": [ ],
                    "pd-pools": $pd_part$opt_data_json
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
        KEA_FLEX_OPTIONS   => get_flex_options_json($br_ipv6, $base_subnet, $slaac_br_base, $slaac_fix_br_prefix, $pd_pool, $hgw_br_base, @segs),
        KEA_CLIENT_CLASSES => get_client_classes_json($slaac_br_base, $slaac_fix_br_prefix, $pd_pool, $pd_fix_pool, $hgw_br_base, $mape_if, @segs),
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
    
    # 商用 OCN 実機仕様準拠: 固定IP (SLAAC固定 / PD固定 / HGW固定) は接続方式を問わず一律 /56 (0x38)
    # かつ 1回線=1VLAN のため、Rule IPv6 Prefix はその VLAN の /56 (${base_subnet}:${base+vlan}::/56)
    my $v6_len = "38"; # 56bit = 0x38
    my $prefix = calc_vlan_prefix($args{base_subnet}, $args{base}, $args{vlan} || 0) . "::";
    my $v6_prefix_hex = substr(ipv6_to_hex($prefix), 0, 14); # 56bit = 7 bytes = 14 hex chars
    
    my $rule_payload = $flags . $ea_len . $v4_len . $v4_hex . $v6_len . $v6_prefix_hex;
    my $rule_len_hex = sprintf("%04x", length($rule_payload) / 2);
    my $rule_sub_option = "0059" . $rule_len_hex . $rule_payload;
    
    return $br_sub_option . $rule_sub_option;
}

sub get_fixed_subnet_option_data_json {
    my ($s, $base_subnet, $br_ipv6, $slaac_fix_br_prefix, $pd_fix_pool, $static_ips_ref, $hgw_fix_br_base) = @_;
    my %static_ips = %{$static_ips_ref};

    my $is_slaac_fix = ($s->{t} eq "slaac" && $s->{b} eq $slaac_fix_br_prefix);
    my $is_pd_fix    = ($s->{t} eq "pd" && $s->{b} eq $pd_fix_pool);
    my $is_hgw_fix   = ($s->{t} eq "hgw_fix");

    return "" unless $is_slaac_fix || $is_pd_fix || $is_hgw_fix;

    my $vlan = int($s->{v});
    my $ent = $static_ips{$vlan};
    return "" unless defined $ent;

    my ($ip, $mask) = ref($ent) eq 'HASH' ? ($ent->{ip}, $ent->{mask}) : ($ent, 32);
    my $opt94_hex = calculate_option94_fixed_hex(
        ip          => $ip,
        mask        => $mask,
        br_ipv6     => $br_ipv6,
        base_subnet => $base_subnet,
        vlan        => $vlan,
        base        => $s->{b}
    );

    # subnet6 レベルの option-data (always-send=false 既定のため、ORO Option 6 で Option 94 を要求した CE にのみ返却される)
    my $block = <<~"OPTBLOCK";
            "option-data": [
                {
                    "code": 94,
                    "csv-format": false,
                    "data": "$opt94_hex"
                }
            ]
OPTBLOCK
    $block =~ s/\n$//;
    return ",\n$block";
}

# ----------------------------------------------------------------------------
# _build_dynamic_flex_option
#   動的セグメント (SLAAC動的 ① / PD動的 ② / HGW動的 ⑤) 用の
#   Option 94 flex-option ブロック（JSON 断片文字列）を返す。
#
#   Option 90 (BR)    : 005a0010 + br_hex (20B)
#   Option 89 (Rule)  : 00590015 + 00(flags) 12(ea-len=18) 14(v4-len=20)
#                       + pool_v4_32_hex(4B) + 26(v6-len=38) + v6_pfx(5B)
#                       + Option 93 (005d00040606 + psid_hex, 8B)
# ----------------------------------------------------------------------------
sub _build_dynamic_flex_option {
    my (%args) = @_;
    my ($br_hex, $pool_upper_hex, $base_subnet, $base, $vlan, $class_name) =
        @args{qw(br_hex pool_upper_hex base_subnet base vlan class_name)};

    # 全体プール (/16) から各セグメント/VLAN 向けに /20 (4,096 IP空間) を切り出す (4 bytes: RFC 7598固定長)
    my $v4_sub_idx = (($vlan - 1) % 16);
    my $octet2 = ($v4_sub_idx * 16) & 0xf0;
    my $pool_v4_32_hex = sprintf("%s%02x00", $pool_upper_hex, $octet2);

    # 商用 OCN 1,008 ポート仕様: Rule IPv6 /38 (5 bytes)
    my $v6_prefix_hex = substr(ipv6_to_hex("${base_subnet}:${base}::"), 0, 10);
    my $last_byte = hex(substr($v6_prefix_hex, 8, 2)) & 0xfc;
    substr($v6_prefix_hex, 8, 2, sprintf("%02x", $last_byte));

    # 商用 OCN 1,008 ポート仕様: psIdOffset=6, psid-len=6, psid (16bit左詰め)
    my $psid = (($vlan - 1) % 63) + 1;
    my $psid_hex = sprintf("%04x", $psid << 10);
    my $opt93_hex = "005d00040606${psid_hex}";

    # RFC 7598 Section 4.1 / 4.4 / 4.5:
    # Option 89 payload: flags(1B) + ea-len(1B) + v4-len(1B) + ipv4-pfx(4B固定長) + v6-len(1B) + v6-pfx(5B) + sub-opt93(8B) = 21B (0x0015)
    my $opt89_hex = "00590015001214${pool_v4_32_hex}26${v6_prefix_hex}${opt93_hex}";
    my $opt94_hex = "005a0010${br_hex}${opt89_hex}";

    return <<~"BLOCK";
                    {
                        "code": 94,
                        "supersede": "0x${opt94_hex}",
                        "csv-format": false,
                        "client-class": "$class_name"
                    }
                    BLOCK
}

sub _get_dynamic_class_info {
    my ($s, $slaac_br_base, $pd_pool, $hgw_br_base) = @_;
    my $v = $s->{v};
    if ($s->{t} eq 'slaac' && $s->{b} eq $slaac_br_base) {
        return ("class-slaac-dyn-$v", $s->{b});
    } elsif ($s->{t} eq 'pd' && $s->{b} eq $pd_pool) {
        return ("class-pd-dyn-$v", $s->{b});
    } elsif ($s->{t} eq 'hgw' && (!defined $hgw_br_base || $s->{b} eq $hgw_br_base)) {
        return ("class-hgw-dyn-$v", $s->{pd_pool} // $s->{b});
    }
    return ();
}

sub get_flex_options_json {
    my ($br_ipv6, $base_subnet, $slaac_br_base, $slaac_fix_br_prefix, $pd_pool, $hgw_br_base, @segs) = @_;
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
        my ($class_name, $base_val) = _get_dynamic_class_info($s, $slaac_br_base, $pd_pool, $hgw_br_base);
        next unless $class_name;

        my $block = _build_dynamic_flex_option(
            br_hex         => $br_hex,
            pool_upper_hex => $pool_upper_hex,
            base_subnet    => $base_subnet,
            base           => $base_val,
            vlan           => $s->{v},
            class_name     => $class_name,
        );
        $block =~ s/\n$//;
        push @opt_blocks, $block;
    }

    return join(",\n", @opt_blocks);
}

sub get_client_classes_json {
    my ($slaac_br_base, $slaac_fix_br_prefix, $pd_pool, $pd_fix_pool, $hgw_br_base, $mape_if, @segs) = @_;
    my @class_blocks;

    # DHCPv6 Option 6 (ORO: Option Request Option) に Option 94 (0x005e) が含まれている場合のみ一致するクラス
    # 商用 OCN 回線と同様に、ORO で Option 94 を要求していない CE ルーターには Option 94 を返却しない
    my $oro_block = <<~"BLOCK";
                {
                    "name": "oro-opt94",
                    "test": "option[6].exists and (substring(option[6].hex, 0, 2) == 0x005e or substring(option[6].hex, 2, 2) == 0x005e or substring(option[6].hex, 4, 2) == 0x005e or substring(option[6].hex, 6, 2) == 0x005e or substring(option[6].hex, 8, 2) == 0x005e or substring(option[6].hex, 10, 2) == 0x005e or substring(option[6].hex, 12, 2) == 0x005e or substring(option[6].hex, 14, 2) == 0x005e)"
                }
                BLOCK
    $oro_block =~ s/\n$//;
    push @class_blocks, $oro_block;

    foreach my $s (@segs) {
        my ($name) = _get_dynamic_class_info($s, $slaac_br_base, $pd_pool, $hgw_br_base);
        next unless $name;
        my $v = $s->{v};
        my $block = <<~"BLOCK";
                {
                    "name": "$name",
                    "test": "pkt.iface == '$mape_if.$v' and member('oro-opt94')"
                }
                BLOCK
        $block =~ s/\n$//;
        push @class_blocks, $block;
    }

    return join(",\n", @class_blocks);
}
