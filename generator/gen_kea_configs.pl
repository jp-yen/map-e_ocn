#!/usr/bin/perl
use strict;
use warnings;
use File::Basename;
use FindBin qw($RealBin);
use lib $RealBin;
use MapeCommon qw(ipv4_to_hex_hextets calc_br_ipv6 calc_vlan_prefix build_vlan_segments load_conf_file render_template_file);

# ----------------------------------------------------------------------------
# map-e.conf の読み込み (共通ロジックは MapeCommon::load_conf_file)
#   MAPE_RULE_IPV6_PREFIX / MAPE_RULE_IPV6_LEN は
#   mape-provisioning-server 側と必ず一致させる必要があるため、
#   両者とも MapeCommon::_derive_provisioning_vars() 経由の同じ値を使う。
#   (Makefile経由で既に%ENVに展開済みの場合は同じ内容で上書きされるだけ)
# ----------------------------------------------------------------------------
my $script_dir = dirname(__FILE__);
load_conf_file("$script_dir/../map-e.conf");

my $mode = shift @ARGV || 'dhcp6';

if ($mode eq 'hook') {
    my $template = 'kea-dhcp6/kea-map-e-hook.tmpl';
    open my $fh, '<', $template or die "Cannot open template $template: $!";
    while (<$fh>) {
        print $_;
    }
    close $fh;
    exit;

} elsif ($mode eq 'dhcp6') {
    # ---------------------------------------------------------------
    # 基本パラメータ
    # ---------------------------------------------------------------
    my $mape_if     = $ENV{MAPE_IF}            || "ens19";
    my $base_subnet = $ENV{BASE_SUBNET}        || "5f00:3aa";
    my $dns_ip      = $ENV{MAPE_DNS_IP}        || "5f00:3aa:9999::53";
    my $ntp_ip      = $ENV{MAPE_NTP_IP}        || "5f00:3aa:9999::123";
    my $domain      = $ENV{MAPE_DOMAIN_SEARCH} || "map.ocn.ad.jp";

    # ---------------------------------------------------------------
    # BR (Border Relay) IPv6アドレスの算出 (共通ロジックは MapeCommon::calc_br_ipv6)
    # ---------------------------------------------------------------
    my $br_prefix = $ENV{BR_PREFIX} || "$base_subnet:a001";
    my $br_ipv4   = $ENV{BR_IPV4_ADDR} || "100.127.255.255";
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
    my $pd_pool           = $ENV{PD_POOL} || "2000";
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
        { vlans => $ENV{SLAAC_DYN_VLANS}, type => 'slaac', base => $ENV{SLAAC_BR_BASE}       || '1000' },
        { vlans => $ENV{PD_DYN_VLANS},    type => 'pd',    base => $ENV{PD_POOL}             || '2000' },
        { vlans => $ENV{SLAAC_FIX_VLANS}, type => 'slaac', base => $ENV{SLAAC_FIX_BR_PREFIX} || '3000' },
        { vlans => $ENV{PD_FIX_VLANS},    type => 'pd',    base => $ENV{PD_FIX_POOL}         || '4000' },
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
        my $pd_part = ($s->{t} eq "pd")
            ? "[\n                { \"prefix\": \"$rule_ipv6_prefix\", \"prefix-len\": 40, \"delegated-len\": $delegated_len }\n            ]"
            : "[ ]";

        my $res_json = get_reservations_json($s, $base_subnet, $br_ipv6, \%static_ips);

        push @blocks, "        {\n" .
                      "            \"id\": $id,\n" .
                      "            \"subnet\": \"${prefix}::/64\",\n" .
                      "            \"interface\": \"$mape_if.$s->{v}\",\n" .
                      "            \"pools\": [ ],\n" .
                      "            \"pd-pools\": $pd_part$res_json\n" .
                      "        }";
        $id++;
    }

    # ---------------------------------------------------------------
    # テンプレートへの置換値一覧
    # ---------------------------------------------------------------
    my %v = (
        KEA_INTERFACES     => $ifaces,
        KEA_SUBNET6_BLOCKS => join(",\n", @blocks),
        MAPE_DNS_IP        => $dns_ip,
        MAPE_NTP_IP        => $ntp_ip,
        MAPE_DOMAIN_SEARCH => $domain,
        KEA_FLEX_OPTIONS   => get_flex_options_json($br_ipv6, $base_subnet, $ENV{SLAAC_BR_BASE} || "1000", $ENV{SLAAC_FIX_BR_PREFIX} || "3000", $pd_pool, @segs),
        KEA_CLIENT_CLASSES => get_client_classes_json($ENV{SLAAC_BR_BASE} || "1000", $ENV{SLAAC_FIX_BR_PREFIX} || "3000", $pd_pool, $ENV{PD_FIX_POOL} || "4000", $mape_if, @segs),
    );

    my $template = 'kea-dhcp6/kea-dhcp6.conf.tmpl';
    print render_template_file($template, { %ENV, %v });

} else {
    die "Usage: $0 [dhcp6|hook]\n";
}

# ----------------------------------------------------------------------------
# ヘルパー関数群 (IPv6/IPv4変換、Option 94生成、CE向け固定予約/分類)
# ----------------------------------------------------------------------------
sub trim {
    my ($s) = @_;
    return "" unless defined $s;
    $s =~ s/^\s+//;
    $s =~ s/\s+$//;
    return $s;
}

sub load_static_ips {
    my ($file) = @_;
    my %map;
    if (-f $file) {
        open my $fh, '<', $file or die "Cannot open $file: $!";
        while (<$fh>) {
            chomp;
            next if /^\s*#/ || /^\s*$/;
            my ($mac, $ip_mask) = split(/\s*,\s*/, $_);
            if ($mac && $ip_mask) {
                $mac = lc(trim($mac));
                my ($ip) = split(/\//, trim($ip_mask));
                $map{$mac} = $ip;
            }
        }
        close $fh;
    }
    return %map;
}

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
    # ちょうど8個のhextetがあることを確認
    while (scalar(@hextets) < 8) {
        push @hextets, 0;
    }
    @hextets = @hextets[0..7];
    return sprintf("%04x%04x%04x%04x%04x%04x%04x%04x", @hextets);
}

sub ipv4_to_hex {
    my ($ipv4) = @_;
    my @o = split(/\./, $ipv4);
    die "Invalid IPv4 address: $ipv4\n" unless scalar(@o) == 4;
    return sprintf("%02x%02x%02x%02x", @o);
}

sub calculate_option94_fixed_hex {
    my (%args) = @_;
    my $br_hex = ipv6_to_hex($args{br_ipv6});
    my $br_sub_option = "005a0010" . $br_hex;
    
    my $flags = "00";
    my $ea_len = "00";
    my $v4_len = "20";
    my $v4_hex = ipv4_to_hex($args{ip});
    
    my $v6_len;
    my $v6_prefix_hex;
    
    if ($args{is_slaac}) {
        $v6_len = "40";
        my $prefix = calc_vlan_prefix($args{base_subnet}, $args{base}, $args{vlan});
        $v6_prefix_hex = substr(ipv6_to_hex($prefix), 0, 16);
    } else {
        $v6_len = "38";
        my $pd_fix_pool = $ENV{PD_FIX_POOL} || "4000";
        my $dummy_prefix = "$args{base_subnet}:$pd_fix_pool" . "::";
        $v6_prefix_hex = substr(ipv6_to_hex($dummy_prefix), 0, 14);
    }
    
    my $rule_payload = $flags . $ea_len . $v4_len . $v4_hex . $v6_len . $v6_prefix_hex;
    my $rule_len_hex = sprintf("%04x", length($rule_payload) / 2);
    my $rule_sub_option = "0059" . $rule_len_hex . $rule_payload;
    
    return $br_sub_option . $rule_sub_option;
}

sub get_reservations_json {
    my ($s, $base_subnet, $br_ipv6, $static_ips_ref) = @_;
    my %static_ips = %{$static_ips_ref};
    my @res_blocks;
    
    my $is_slaac_fix = ($s->{t} eq "slaac" && $s->{b} eq ($ENV{SLAAC_FIX_BR_PREFIX} || "3000"));
    my $is_pd_fix    = ($s->{t} eq "pd" && $s->{b} eq ($ENV{PD_FIX_POOL} || "4000"));
    
    return "" unless $is_slaac_fix || $is_pd_fix;
    
    foreach my $mac (sort keys %static_ips) {
        my $ip = $static_ips{$mac};
        my $opt94_hex = calculate_option94_fixed_hex(
            ip          => $ip,
            br_ipv6     => $br_ipv6,
            is_slaac    => $is_slaac_fix,
            base_subnet => $base_subnet,
            vlan        => $s->{v},
            base        => $s->{b}
        );
        
        push @res_blocks, "                {\n" .
                          "                    \"hw-address\": \"$mac\",\n" .
                          "                    \"option-data\": [\n" .
                          "                        {\n" .
                          "                            \"code\": 94,\n" .
                          "                            \"csv-format\": false,\n" .
                          "                            \"data\": \"$opt94_hex\"\n" .
                          "                        }\n" .
                          "                    ]\n" .
                          "                }";
    }
    
    if (@res_blocks) {
        return ",\n            \"reservations\": [\n" . join(",\n", @res_blocks) . "\n            ]";
    }
    return "";
}

sub get_flex_options_json {
    my ($br_ipv6, $base_subnet, $slaac_br_base, $slaac_fix_br_prefix, $pd_pool, @segs) = @_;
    my @opt_blocks;
    
    my $br_hex = ipv6_to_hex($br_ipv6);
    
    my $pool_upper_hex = "c633";
    if ($ENV{BR_IPV4_POOL}) {
        my @o = split(/\./, $ENV{BR_IPV4_POOL});
        $pool_upper_hex = sprintf("%02x%02x", $o[0], $o[1]);
    }
    
    my $pd_rule_v6_prefix_hex = substr(ipv6_to_hex("$base_subnet:$pd_pool" . "::"), 0, 14);
    
    foreach my $s (@segs) {
        my $v = $s->{v};
        if ($s->{t} eq "slaac") {
            my $vlan_byte = sprintf("%02x", $v % 256);
            my $prefix = calc_vlan_prefix($base_subnet, $s->{b}, $v);
            my $v6_prefix_hex = substr(ipv6_to_hex($prefix), 0, 16);
            
            my $class_name = ($s->{b} eq $slaac_br_base)
                ? "class-slaac-dyn-$v"
                : "class-slaac-fix-$v-fallback";
                
            my $hextet = $s->{b} + $v;
            my $psid_hex = substr(sprintf("%04d", $hextet), 0, 2);
            my $expr = "concat(0x005a0010${br_hex}, concat(0x00590010000820${pool_upper_hex}${vlan_byte}, concat(substring(option[1].hex, -1, 1), concat(0x40${v6_prefix_hex}, 0x005d0004040800${psid_hex}))))";
            
            push @opt_blocks, "                    {\n" .
                              "                        \"code\": 94,\n" .
                              "                        \"supersede\": \"$expr\",\n" .
                              "                        \"csv-format\": false,\n" .
                              "                        \"client-class\": \"$class_name\"\n" .
                              "                    }";
        } elsif ($s->{t} eq "pd") {
            my $class_name = ($s->{b} eq $pd_pool)
                ? "class-pd-dyn-$v"
                : "class-pd-fix-$v-fallback";
                
            my $expr = "concat(0x005a0010${br_hex}, concat(0x0059000f000820${pool_upper_hex}, concat(substring(option[25].option[26].hex, 14, 2), concat(0x38${pd_rule_v6_prefix_hex}, concat(0x005d0004040800, substring(option[25].option[26].hex, 13, 1))))))";
            
            push @opt_blocks, "                    {\n" .
                              "                        \"code\": 94,\n" .
                              "                        \"supersede\": \"$expr\",\n" .
                              "                        \"csv-format\": false,\n" .
                              "                        \"client-class\": \"$class_name\"\n" .
                              "                    }";
        }
    }
    
    return join(",\n", @opt_blocks);
}

sub get_client_classes_json {
    my ($slaac_br_base, $slaac_fix_br_prefix, $pd_pool, $pd_fix_pool, $mape_if, @segs) = @_;
    my @class_blocks;
    
    foreach my $s (@segs) {
        my $v = $s->{v};
        if ($s->{t} eq "slaac") {
            if ($s->{b} eq $slaac_br_base) {
                push @class_blocks, "        {\n" .
                                    "            \"name\": \"class-slaac-dyn-$v\",\n" .
                                    "            \"test\": \"pkt.iface == '$mape_if.$v'\"\n" .
                                    "        }";
            } else {
                push @class_blocks, "        {\n" .
                                    "            \"name\": \"class-slaac-fix-$v-fallback\",\n" .
                                    "            \"test\": \"member('UNKNOWN') and (pkt.iface == '$mape_if.$v')\"\n" .
                                    "        }";
            }
        } elsif ($s->{t} eq "pd") {
            if ($s->{b} eq $pd_pool) {
                push @class_blocks, "        {\n" .
                                    "            \"name\": \"class-pd-dyn-$v\",\n" .
                                    "            \"test\": \"pkt.iface == '$mape_if.$v'\"\n" .
                                    "        }";
            } else {
                push @class_blocks, "        {\n" .
                                    "            \"name\": \"class-pd-fix-$v-fallback\",\n" .
                                    "            \"test\": \"member('UNKNOWN') and (pkt.iface == '$mape_if.$v')\"\n" .
                                    "        }";
            }
        }
    }
    
    return join(",\n", @class_blocks);
}
