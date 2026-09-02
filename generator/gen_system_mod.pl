#!/usr/bin/perl
# generator/gen_system_mod.pl
use strict;
use warnings;
use File::Basename;
use FindBin qw($RealBin);
use lib $RealBin;
use MapeCommon qw(calc_br_ipv6 calc_vlan_prefix build_vlan_segments load_conf_file);

my $mode = $ARGV[0] || '';

# ----------------------------------------------------------------------------
# map-e.conf の読み込み (共通ロジックは MapeCommon::load_conf_file)
#   Makefile経由 (set -a; . ./map-e.conf) で既に%ENVに展開済みの場合は
#   同じ内容で上書きされるだけなので無害。単体実行時のフォールバックとして働く。
# ----------------------------------------------------------------------------
my $script_dir = dirname(__FILE__);
load_conf_file("$script_dir/../map-e.conf");

# 以降の generate_sysctl / generate_interfaces は小文字キーの
# ハッシュを前提にしているため、%ENV を小文字化して %config を作る。
my %config = map { lc($_) => $ENV{$_} } keys %ENV;

# --- 99-network-routing.conf の生成関数 ---
sub generate_sysctl {
    my ($c) = @_;
    my $mgt_if = $c->{mgt_if} || 'eth0';

    print "# =============================================================================\n";
    print "# /etc/sysctl.d/99-network-routing.conf (Generated via gen_configs.pl)\n";
    print "# =============================================================================\n\n";
    print "# --- パケット転送設定 (Routing & Forwarding) ---\n";
    print "net.ipv6.conf.all.forwarding = 1\n";
    print "net.ipv6.conf.default.forwarding = 1\n";
    print "net.ipv4.ip_forward = 1\n\n";
    print "# --- 不要なIPv6無効化 (管理用インターフェースの隔離) ---\n";
    print "net.ipv6.conf.${mgt_if}.disable_ipv6 = 1\n\n";
    print "# --- 逆経路フィルタ (rp_filter) の最適化 ---\n";
    print "net.ipv4.conf.default.rp_filter = 0\n";
    print "net.ipv4.conf.all.rp_filter = 0\n";
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
    my $mgt_ip            = $c->{mgt_ip};
    my $mgt_mask          = $c->{mgt_mask};
    my $mgt_gw            = $c->{mgt_gw};
    my $br_ipv4_addr      = $c->{br_ipv4_addr};

    my $mgt_addr_cidr = $mgt_ip;
    if ($mgt_ip && $mgt_ip !~ /\// && defined $mgt_mask && $mgt_mask ne '') {
        $mgt_addr_cidr = "$mgt_ip/$mgt_mask";
    }

    print "# =============================================================================\n";
    print "# /etc/network/interfaces (Generated via gen_configs.pl)\n";
    print "# =============================================================================\n\n";

    print "auto lo\niface lo inet loopback\n\n";
    print "# --- 管理用インターフェース ---\n";
    print "auto $mgt_if\n";
    print "iface $mgt_if inet static\n";
    print "    address $mgt_addr_cidr\n";
    print "    gateway $mgt_gw\n\n";
    print "iface $mape_if inet manual\n\n";

    # ①～④ セグメント定義
    foreach my $seg (build_vlan_segments([
        { vlans => $slaac_dyn_vlans, type => 'slaac', base => $slaac_br_base },
        { vlans => $pd_dyn_vlans,    type => 'pd',    base => undef },
        { vlans => $slaac_fix_vlans, type => 'slaac', base => $slaac_fix_br_prefix },
        { vlans => $pd_fix_vlans,    type => 'pd',    base => undef },
    ])) {
        my $vlan = $seg->{v};
        my $vif  = "$mape_if.$vlan";

        if ($seg->{t} eq 'slaac') {
            my $prefix = calc_vlan_prefix($base_subnet, $seg->{b}, $vlan);
            my $suffix = $slaac_br_suffix;
            $suffix =~ s/^:+//;
            my $ip = "${prefix}::${suffix}";

            # 動的セグメントと同じ理由(旧アドレス/旧経路の確実な削除)で post-down を付与
            print "auto $vif\niface $vif inet6 static\n";
            print "    address ${ip}/64\n";
            print "    accept_ra 0\n";
            print "    pre-up ip link add link $mape_if name $vif type vlan id $vlan || true\n\n";
            print "    post-down ip link del $vif || true\n";
        } else {
            print "auto $vif\niface $vif inet6 manual\n";
            print "    pre-up ip link add link $mape_if name $vif type vlan id $vlan || true\n";
            print "    up ip link set dev $vif up\n";
            print "    up sysctl -w net.ipv6.conf.${vif}.accept_ra=0\n";
            print "    up sysctl -w net.ipv6.conf.${vif}.autoconf=0\n\n";
        }
    }

    # サービスIP
    my $mape_dns_ip         = $c->{mape_dns_ip};
    my $mape_prov_ip        = $c->{mape_prov_ip};
    my $mape_ntp_ip         = $c->{mape_ntp_ip};

    # --- BR_IPV6 の算出 (共通ロジックは MapeCommon::calc_br_ipv6) ---
    my $br_ipv6 = calc_br_ipv6(
        br_ipv6      => $c->{br_ipv6},
        br_prefix    => $c->{br_prefix},
        br_ipv4_addr => $c->{br_ipv4_addr},
    );
    # ----------------------------------------
    print "# =============================================================================\n";
    print "# Services & Provisioning Network\n";
    print "# =============================================================================\n";

    # dummy0 は MAP-Eトンネル終端 (BRのIPv6/IPv4両エンドポイント)。
    # IPv4側 (BR_IPV4_ADDR) はCEから見たGW/BR_IPV6合成元のBR自身の
    # アドレスであり、サブネットを持たない単一ホストアドレスのため /32 で
    # 付与する。link自体の作成/削除はinet/inet6どちらのスタンザからでも
    # 実行され得るため、両方に (冪等な) pre-up/post-down を持たせる。
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

    print "auto $prov_if\n";
    print "iface $prov_if inet6 manual\n";
    print "    pre-up ip link add $prov_if type dummy || true\n";
    print "    post-up ip -6 addr add $mape_dns_ip/64 dev $prov_if || true\n" if $mape_dns_ip;
    print "    post-up ip -6 addr add $mape_prov_ip/64 dev $prov_if || true\n" if $mape_prov_ip;
    print "    post-up ip -6 addr add $mape_ntp_ip/64 dev $prov_if || true\n" if $mape_ntp_ip;
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