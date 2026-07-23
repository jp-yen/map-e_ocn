#!/usr/bin/perl
# generator/gen_bind_configs.pl
use strict;
use warnings;
use File::Basename;
use FindBin qw($RealBin);
use lib $RealBin;
use MapeCommon qw(find_template render_template_file);

my %mode_to_template = (
    named_local => 'named.conf.local.tmpl',
    db_map      => 'db.map.ocn.ad.jp.tmpl',
    db_v6       => 'db.v6connect.net.tmpl',
);

# 引数でどのBIND設定ファイルを生成するか制御
my $mode = $ARGV[0] || '';
if (!exists $mode_to_template{$mode}) {
    die "Usage: $0 [named_local|db_map|db_v6]\n";
}

my $script_dir = dirname(__FILE__);
my $final_tmpl = find_template($mode_to_template{$mode}, $script_dir, 'bind');
if (!$final_tmpl) {
    die "Error: Template file '$mode_to_template{$mode}' not found.\n";
}

print render_template_file($final_tmpl, {
    BR_IPV4_ADDR => $ENV{BR_IPV4_ADDR} || '100.127.255.255',
    MAPE_PROV_IP  => $ENV{MAPE_PROV_IP}  || '5f00:3aa:9999::ff',
    MAPE_DNS_IP   => $ENV{MAPE_DNS_IP}   || '5f00:3aa:9999::53',
});
