#!/usr/bin/perl
# generator/gen_radvd_mod.pl
use strict;
use warnings;
use File::Basename;
use FindBin qw($RealBin);
use lib $RealBin;
use MapeCommon qw(calc_vlan_prefix build_vlan_segments check_config);

sub generate_radvd {
    my ($c) = @_;

    my $mape_if             = $c->{mape_if};
    my $base_subnet         = $c->{base_subnet};
    my $slaac_br_base       = $c->{slaac_br_base};
    my $slaac_fix_br_prefix = $c->{slaac_fix_br_prefix};
    my $slaac_dyn_vlans     = $c->{slaac_dyn_vlans};
    my $pd_dyn_vlans        = $c->{pd_dyn_vlans};
    my $slaac_fix_vlans     = $c->{slaac_fix_vlans};
    my $pd_fix_vlans        = $c->{pd_fix_vlans};
    my $mape_dns_ip         = $c->{mape_dns_ip};
    my $domain              = $c->{domain};

    print "# =============================================================================\n";
    print "# /etc/radvd.conf (Generated via gen_radvd_mod.pl)\n";
    print "# =============================================================================\n\n";

    foreach my $seg (build_vlan_segments([
        { vlans => $slaac_dyn_vlans, type => 'slaac', base => $slaac_br_base },
        { vlans => $pd_dyn_vlans,    type => 'pd',    base => undef },
        { vlans => $slaac_fix_vlans, type => 'slaac', base => $slaac_fix_br_prefix },
        { vlans => $pd_fix_vlans,    type => 'pd',    base => undef },
    ])) {
        my $vlan = $seg->{v};
        my $vif = "$mape_if.$vlan";
        my $prefix = ($seg->{t} eq 'slaac')
            ? calc_vlan_prefix($base_subnet, $seg->{b}, $vlan)
            : undef;
        _print_radvd_block($vif, $prefix, $mape_dns_ip, $domain);
    }
}

# --- 各VLANのRAブロック共通テキスト生成ヘルパー ---
sub _print_radvd_block {
    my ($vif, $prefix, $dns, $domain) = @_;

    print "interface $vif\n";
    print "{\n";
    print "    AdvSendAdvert on;\n";
    print "    MinRtrAdvInterval 3;\n";
    print "    MaxRtrAdvInterval 10;\n";
    if ($prefix) {
        print "    AdvManagedFlag off;\n";
        print "    AdvOtherConfigFlag off;\n\n";
        print "    # SLAAC用プレフィックス配布\n";
        print "    prefix ${prefix}::/64\n";
        print "    {\n";
        print "        AdvOnLink on;\n";
        print "        AdvAutonomous on;\n";
        print "        AdvRouterAddr on;\n";
        print "    };\n\n";
    } else {
        print "    AdvManagedFlag on;\n";
        print "    AdvOtherConfigFlag on;\n\n";
        print "    # プレフィックス非通知 (DHCPv6-PD拠点へのデフォルトルート広報)\n";
    }

    print "    RDNSS $dns\n";
    print "    {\n";
    print "        AdvRDNSSLifetime 600;\n";
    print "    };\n\n";
    print "    DNSSL $domain\n";
    print "    {\n";
    print "        AdvDNSSLLifetime 600;\n";
    print "    };\n";
    print "};\n\n";
}

# --- メイン処理 ---
my $mode = $ARGV[0] || '';
if ($mode ne 'radvd') {
    die "Usage: $0 radvd\n";
}

my $script_dir = dirname(__FILE__);
check_config("$script_dir/../map-e.conf");

# 設定変数をハッシュにマッピング
my %config = (
    mape_if             => $ENV{MAPE_IF},
    base_subnet         => $ENV{BASE_SUBNET},
    slaac_br_base       => $ENV{SLAAC_BR_BASE},
    slaac_fix_br_prefix => $ENV{SLAAC_FIX_BR_PREFIX},
    slaac_dyn_vlans     => $ENV{SLAAC_DYN_VLANS},
    pd_dyn_vlans        => $ENV{PD_DYN_VLANS},
    slaac_fix_vlans     => $ENV{SLAAC_FIX_VLANS},
    pd_fix_vlans        => $ENV{PD_FIX_VLANS},
    mape_dns_ip         => $ENV{MAPE_DNS_IP},
    domain              => $ENV{DOMAIN},
);

generate_radvd(\%config);
