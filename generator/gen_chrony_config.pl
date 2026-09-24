#!/usr/bin/perl
# generator/gen_chrony_config.pl
use strict;
use warnings;
use File::Basename;
use FindBin qw($RealBin);
use lib $RealBin;
use MapeCommon qw(find_template render_template_file check_config);

my $script_dir = dirname(__FILE__);
check_config("$script_dir/../map-e.conf");

# =============================================================================
# 1. map-e.conf から環境変数を取得
# =============================================================================
my $mode = $ARGV[0] // '';
die "Usage: $0 chrony\n" if $mode && $mode ne 'chrony';

my $system_ntp = $ENV{SYSTEM_NTP};

# =============================================================================
# 2. テンプレートファイルの探索
# =============================================================================
my $tmpl_file = "chrony.conf.tmpl";
my $final_tmpl = find_template($tmpl_file, $script_dir, "chrony");

if (!$final_tmpl) {
    die "Error: Template file '$tmpl_file' not found.\n";
}

my $ntp_servers_lines = join("\n", map { "server $_ iburst" } grep { length $_ } split(/[\s,]+/, $system_ntp));
print render_template_file($final_tmpl, { SYSTEM_NTP => $ntp_servers_lines });
