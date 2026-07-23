#!/usr/bin/perl
# generator/gen_chrony_config.pl
use strict;
use warnings;
use File::Basename;
use FindBin qw($RealBin);
use lib $RealBin;
use MapeCommon qw(find_template);

# =============================================================================
# 1. map-e.conf からシェル経由で export された環境変数を取得
# =============================================================================
my $system_ntp = $ENV{SYSTEM_NTP} || '192.168.0.252, 192.168.0.253, 192.168.0.254';

# =============================================================================
# 2. テンプレートファイルの探索
# =============================================================================
my $tmpl_file = "chrony.conf.tmpl";
my $script_dir = dirname(__FILE__);
my $final_tmpl = find_template($tmpl_file, $script_dir, "chrony");

if (!$final_tmpl) {
    die "Error: Template file '$tmpl_file' not found.\n";
}

# =============================================================================
# 3. カンマ区切りの上位NTPサーバーを Chronyの複数行設定へ変換
# =============================================================================
my @ntp_list = split(/[\s,]+/, $system_ntp);
my @lines;
foreach my $ntp (@ntp_list) {
    next if $ntp eq ''; # 空要素を除外
    push @lines, "server $ntp iburst";
}
my $ntp_servers_lines = join("\n", @lines);

# =============================================================================
# 4. テンプレートの読み込みと置換
# =============================================================================
open my $fh, '<', $final_tmpl or die "Cannot open $final_tmpl: $!";
my $content = do { local $/; <$fh> };
close $fh;

# map-e.conf の変数名表記に合わせたプレースホルダーを置換
$content =~ s/\$\{SYSTEM_NTP\}/$ntp_servers_lines/g;

print $content;
