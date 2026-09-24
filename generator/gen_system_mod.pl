#!/usr/bin/perl
# generator/gen_system_mod.pl
use strict;
use warnings;
use File::Basename;
use FindBin qw($RealBin);
use lib $RealBin;
use MapeCommon qw(calc_br_ipv6 calc_vlan_prefix build_vlan_segments load_conf_file check_config);

my $mode = $ARGV[0] || '';

# ----------------------------------------------------------------------------
# map-e.conf の読み込みと検証 (共通ロジックは MapeCommon::check_config)
# ----------------------------------------------------------------------------
my $script_dir = dirname(__FILE__);
check_config("$script_dir/../map-e.conf");

# 以降の generate_sysctl / generate_interfaces は小文字キーの
# ハッシュを前提にしているため、%ENV を小文字化して %config を作る。
my %config = map { lc($_) => $ENV{$_} } keys %ENV;

# --- 99-network-routing.conf の生成関数 ---
sub generate_sysctl {
    my ($c) = @_;
    my $mgt_if = $c->{mgt_if};

    print "# =============================================================================\n";
    print "# /etc/sysctl.d/99-network-routing.conf (Generated via gen_system_mod.pl)\n";
    print "# =============================================================================\n\n";
    print "# --- パケット転送設定 (Routing & Forwarding) ---\n";
    print "net.ipv6.conf.all.forwarding = 1\n";
    print "net.ipv6.conf.default.forwarding = 1\n";
    print "net.ipv4.ip_forward = 1\n\n";
    print "# --- 不要なIPv6無効化 (管理用インターフェースの隔離) ---\n";
    print "net.ipv6.conf.${mgt_if}.disable_ipv6 = 1\n\n";
    print "# --- 逆経路フィルタ (rp_filter) の最適化 ---\n";
    print "net.ipv4.conf.default.rp_filter = 0\n";
    print "net.ipv4.conf.all.rp_filter = 0\n\n";
    print "# --- VRF間サービス通信許可 (L3 Master Device) ---\n";
    print "net.ipv4.udp_l3mdev_accept = 1\n";
    print "net.ipv4.tcp_l3mdev_accept = 1\n";
}

# --- /etc/network/interfaces の生成関数 ---
sub generate_interfaces {
    my ($c) = @_;

    my $mgt_if            = $c->{mgt_if};
    my $mape_if           = $c->{mape_if};
    my $br_if             = $c->{br_if};
    my $prov_if           = $c->{prov_if};
    my $base_subnet       = $c->{base_subnet};
    my $slaac_br_base     = $c->{slaac_br_base};
    my $slaac_br_suffix   = $c->{slaac_br_suffix};
    my $slaac_fix_br_prefix = $c->{slaac_fix_br_prefix};
    my $slaac_fix_br_suffix = $c->{slaac_fix_br_suffix};
    my $slaac_dyn_vlans   = $c->{slaac_dyn_vlans};
    my $pd_dyn_vlans      = $c->{pd_dyn_vlans};
    my $slaac_fix_vlans   = $c->{slaac_fix_vlans};
    my $pd_fix_vlans      = $c->{pd_fix_vlans};
    my $pppoe_vlans       = $c->{pppoe_vlans};
    my $mgt_ip            = $c->{mgt_ip};
    my $mgt_mask          = $c->{mgt_mask};
    my $mgt_gw            = $c->{mgt_gw};
    my $br_ipv4_addr      = $c->{br_ipv4_addr};

    my $mgt_addr_cidr = $mgt_ip;
    if ($mgt_ip && $mgt_ip !~ /\// && defined $mgt_mask && $mgt_mask ne '') {
        $mgt_addr_cidr = "$mgt_ip/$mgt_mask";
    }

    print "# =============================================================================\n";
    print "# /etc/network/interfaces (Generated via gen_system_mod.pl)\n";
    print "# =============================================================================\n\n";

    print "auto lo\niface lo inet loopback\n\n";
    print "# --- 管理用インターフェース ---\n";
    print "auto $mgt_if\n";
    print "iface $mgt_if inet static\n";
    print "    address $mgt_addr_cidr\n";
    print "    gateway $mgt_gw\n\n";
    print "iface $mape_if inet manual\n\n";

    my %seen_vlan;
    foreach my $seg (build_vlan_segments([
        { vlans => $slaac_dyn_vlans, type => 'slaac', base => $slaac_br_base },
        { vlans => $pd_dyn_vlans,    type => 'pd',    base => undef },
        { vlans => $slaac_fix_vlans, type => 'slaac', base => $slaac_fix_br_prefix },
        { vlans => $pd_fix_vlans,    type => 'pd',    base => undef },
        { vlans => $pppoe_vlans,     type => 'pppoe', base => undef },
    ])) {
        my $vlan = $seg->{v};
        next if $seen_vlan{$vlan}++;
        my $vif = "$mape_if.$vlan";

        if ($seg->{t} eq 'slaac') {
            _iface_slaac($vif, $mape_if, $vlan, $base_subnet, $seg->{b},
                         $slaac_br_suffix, $slaac_fix_br_prefix, $slaac_fix_br_suffix);
        } elsif ($seg->{t} eq 'pppoe') {
            _iface_pppoe($vif, $mape_if, $vlan);
        } else {
            _iface_pd($vif, $mape_if, $vlan);
        }
    }

    my $mape_dns_ip  = $c->{mape_dns_ip};
    my $mape_prov_ip = $c->{mape_prov_ip};
    my $mape_ntp_ip  = $c->{mape_ntp_ip};
    my $br_ipv6      = calc_br_ipv6(
        br_ipv6      => $c->{br_ipv6},
        br_prefix    => $c->{br_prefix},
        br_ipv4_addr => $c->{br_ipv4_addr},
    );

    print "# =============================================================================\n";
    print "# Services & Provisioning Network\n";
    print "# =============================================================================\n";
    _iface_br($br_if, $br_ipv4_addr, $br_ipv6);
    _iface_prov($prov_if, $mape_dns_ip, $mape_prov_ip, $mape_ntp_ip);
}

# ---------------------------------------------------------------------------
# _iface_slaac: SLAAC セグメント用 VLAN インターフェース stanza を出力
# ---------------------------------------------------------------------------
sub _iface_slaac {
    my ($vif, $mape_if, $vlan, $base_subnet, $base,
        $slaac_br_suffix, $slaac_fix_br_prefix, $slaac_fix_br_suffix) = @_;
    my $prefix = calc_vlan_prefix($base_subnet, $base, $vlan);
    my $suffix = (defined $slaac_fix_br_prefix && $base eq $slaac_fix_br_prefix
                  && defined $slaac_fix_br_suffix && $slaac_fix_br_suffix ne '')
        ? $slaac_fix_br_suffix
        : $slaac_br_suffix;
    $suffix =~ s/^:+//;
    my $ip = "${prefix}::${suffix}";
    print <<~"STANZA";
        auto $vif
        iface $vif inet6 static
            address ${ip}/64
            accept_ra 0
            pre-up ip link add link $mape_if name $vif type vlan id $vlan || true

            post-down ip link del $vif || true
        STANZA
}

# ---------------------------------------------------------------------------
# _iface_pd: PD（DHCPv6-PD）セグメント用 VLAN インターフェース stanza を出力
# ---------------------------------------------------------------------------
sub _iface_pd {
    my ($vif, $mape_if, $vlan) = @_;
    print <<~"STANZA";
        auto $vif
        iface $vif inet6 manual
            pre-up ip link add link $mape_if name $vif type vlan id $vlan || true
            up ip link set dev $vif up
            up printf 0 > /proc/sys/net/ipv6/conf/${vif}/accept_ra
            up printf 0 > /proc/sys/net/ipv6/conf/${vif}/autoconf

        STANZA
}

# ---------------------------------------------------------------------------
# _iface_pppoe: PPPoE 専用 VLAN インターフェース stanza を出力（IP アドレスなし）
# ---------------------------------------------------------------------------
sub _iface_pppoe {
    my ($vif, $mape_if, $vlan) = @_;
    print <<~"STANZA";
        auto $vif
        iface $vif inet manual
            pre-up ip link add link $mape_if name $vif type vlan id $vlan || true
            up ip link set dev $vif up
            up printf 0 > /proc/sys/net/ipv6/conf/${vif}/disable_ipv6
            post-down ip link del $vif || true

        STANZA
}

# ---------------------------------------------------------------------------
# _iface_br: BR（dummy0）インターフェース stanza を出力
#   dummy0 は MAP-E トンネル終端。IPv4/IPv6 両スタンザで冪等に作成・削除する。
# ---------------------------------------------------------------------------
sub _iface_br {
    my ($br_if, $br_ipv4_addr, $br_ipv6) = @_;
    print "auto $br_if\n";
    print "iface $br_if inet manual\n";
    print "    pre-up ip link add $br_if type dummy || true\n";
    print "    post-up ip addr add $br_ipv4_addr/32 dev $br_if || true\n" if $br_ipv4_addr;
    print "    post-up sysctl -w net.ipv4.conf.$br_if.rp_filter=0 || true\n";
    print "    post-down ip link del $br_if || true\n\n";
    print "iface $br_if inet6 manual\n";
    print "    pre-up ip link add $br_if type dummy || true\n";
    print "    post-up ip -6 addr add $br_ipv6/64 dev $br_if || true\n" if $br_ipv6;
    print "    post-up sysctl -w net.ipv4.conf.$br_if.rp_filter=0 || true\n";
    print "    post-down ip link del $br_if || true\n\n";
}

# ---------------------------------------------------------------------------
# _iface_prov: プロビジョニング用 dummy インターフェース stanza を出力
# ---------------------------------------------------------------------------
sub _iface_prov {
    my ($prov_if, $mape_dns_ip, $mape_prov_ip, $mape_ntp_ip) = @_;
    print "auto $prov_if\n";
    print "iface $prov_if inet6 manual\n";
    print "    pre-up ip link add $prov_if type dummy || true\n";
    print "    post-up ip -6 addr add $mape_dns_ip/64 dev $prov_if || true\n"  if $mape_dns_ip;
    print "    post-up ip -6 addr add $mape_prov_ip/64 dev $prov_if || true\n" if $mape_prov_ip;
    print "    post-up ip -6 addr add $mape_ntp_ip/64 dev $prov_if || true\n"  if $mape_ntp_ip;
    print "    post-down ip link del $prov_if || true\n";
}

# --- メイン処理 ---
if ($mode eq 'sysctl') {
    generate_sysctl(\%config);
} elsif ($mode eq 'interfaces') {
    generate_interfaces(\%config);
} else {
    die "Usage: $0 [sysctl|interfaces]\n";
}
1;