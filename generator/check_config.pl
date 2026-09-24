#!/usr/bin/perl
# generator/check_config.pl
# map-e.conf の全必須パラメータが正しく宣言されているかを一括検証する。
# 未定義または空のパラメータが存在する場合はエラーメッセージを出力して終了コード 1 で停止する。
use strict;
use warnings;
use File::Basename;
use FindBin qw($RealBin);
use lib $RealBin;
use MapeCommon qw(check_config);

my $script_dir = dirname(__FILE__);
my $conf_file = shift @ARGV || "$script_dir/../map-e.conf";

check_config($conf_file);

print "[OK] map-e.conf configuration check passed.\n";
exit 0;
