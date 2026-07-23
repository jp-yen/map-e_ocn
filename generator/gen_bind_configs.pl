#!/usr/bin/perl
# generator/gen_bind_configs.pl
use strict;
use warnings;
use File::Basename;
use FindBin qw($RealBin);
use lib $RealBin;
use MapeCommon qw(find_template);

# 引数でどのBIND設定ファイルを生成するか制御
my $mode = $ARGV[0] || '';
if ($mode ne 'named_local' && $mode ne 'db_map' && $mode ne 'db_v6') {
    die "Usage: $0 [named_local|db_map|db_v6]\n";
}

# =============================================================================
# 1. map-e.conf からシェル経由で export された環境変数を取得
# =============================================================================
my $br_ipv4_addr = $ENV{BR_IPV4_ADDR}  || '100.127.255.255';
my $mape_prov_ip = $ENV{MAPE_PROV_IP}  || '5f00:3aa:9999::ff';
my $mape_dns_ip  = $ENV{MAPE_DNS_IP}   || '5f00:3aa:9999::53';

# =============================================================================
# 2. ターゲットに応じたテンプレートファイルの決定と探索
# =============================================================================
my $tmpl_file = "";
if ($mode eq 'named_local') {
    $tmpl_file = "named.conf.local.tmpl";
} elsif ($mode eq 'db_map') {
    $tmpl_file = "db.map.ocn.ad.jp.tmpl";
} elsif ($mode eq 'db_v6') {
    $tmpl_file = "db.v6connect.net.tmpl";
}

my $script_dir = dirname(__FILE__);
my $final_tmpl = find_template($tmpl_file, $script_dir, "bind");

if (!$final_tmpl) {
    die "Error: Template file '$tmpl_file' not found.\n";
}

# =============================================================================
# 3. テンプレートの読み込みと置換
# =============================================================================
open my $fh, '<', $final_tmpl or die "Cannot open $final_tmpl: $!";
my $content = do { local $/; <$fh> };
close $fh;

# map-e.conf の定義名に合わせて置換
$content =~ s/\$\{BR_IPV4_ADDR\}/$br_ipv4_addr/g;
$content =~ s/\$\{MAPE_PROV_IP\}/$mape_prov_ip/g;
$content =~ s/\$\{MAPE_DNS_IP\}/$mape_dns_ip/g;

print $content;
