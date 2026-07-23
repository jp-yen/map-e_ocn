use strict;
use warnings;
use File::Basename;
use FindBin qw($RealBin);
use lib $RealBin;
use MapeCommon qw(find_template load_conf_file);

# 実行ディレクトリに応じてパスを探索
my $script_dir = dirname(__FILE__);
my $config_file = "$script_dir/../map-e.conf";

# ----------------------------------------------------------------------------
# 1. 設定ファイルの読み込みと変数展開
#    (BR_IPV6 / MAPE_FLAT_RULE_IPV6_PREFIX / MAPE_FLAT_RULE_IPV6_LEN
#     などの派生変数の算出も MapeCommon::load_conf_file 内で一元的に行う)
# ----------------------------------------------------------------------------
load_conf_file($config_file);

# ----------------------------------------------------------------------------
# 2. テンプレート処理の準備
# ----------------------------------------------------------------------------
my $mode = $ARGV[0] || '';
my %valid_modes = map { $_ => 1 } (
    'mape_calc', 'route-monitor', 'provisioning-server',
    'route-monitor-service', 'provisioning-server-service',
    'named_local', 'db_map', 'db_v6'
);
if (!$valid_modes{$mode}) {
    die "Usage: $0 [mape_calc|route-monitor|provisioning-server|route-monitor-service|provisioning-server-service|named_local|db_map|db_v6]\n";
}

my $tmpl_file = "";
if ($mode eq 'mape_calc') { $tmpl_file = "mape_calc.tmpl"; }
elsif ($mode eq 'route-monitor') { $tmpl_file = "mape-route-monitor.tmpl"; }
elsif ($mode eq 'provisioning-server') { $tmpl_file = "mape-provisioning-server.tmpl"; }
elsif ($mode eq 'route-monitor-service') { $tmpl_file = "mape-route-monitor.service.tmpl"; }
elsif ($mode eq 'provisioning-server-service') { $tmpl_file = "mape-provisioning-server.service.tmpl"; }
elsif ($mode eq 'named_local') { $tmpl_file = "named.conf.local.tmpl"; }
elsif ($mode eq 'db_map') { $tmpl_file = "db.map.ocn.ad.jp.tmpl"; }
elsif ($mode eq 'db_v6') { $tmpl_file = "db.v6connect.net.tmpl"; }

my $resolved_path = find_template($tmpl_file, $script_dir, "mape-provisioning-server", "bind");

if (!$resolved_path) {
    die "Error: Template file '$tmpl_file' not found in search paths.\n";
}

# ----------------------------------------------------------------------------
# 3. テンプレートの置換と出力
# ----------------------------------------------------------------------------
open(my $fh, '<', $resolved_path) or die "Cannot open template file '$resolved_path': $!";
while (my $line = <$fh>) {
    # ${VARIABLE} の形式を %ENV の値で置換する
    $line =~ s/\$\{(\w+)\}/defined $ENV{$1} ? $ENV{$1} : ''/eg;
    print $line;
}
close($fh);

1;
