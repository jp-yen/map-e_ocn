use strict;
use warnings;
use File::Basename;
use FindBin qw($RealBin);
use lib $RealBin;
use MapeCommon qw(find_template load_conf_file render_template_file);

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
my %mode_to_template = (
    'mape_calc'                  => 'mape_calc.tmpl',
    'route-monitor'              => 'mape-route-monitor.tmpl',
    'provisioning-server'        => 'mape-provisioning-server.tmpl',
    'route-monitor-service'       => 'mape-route-monitor.service.tmpl',
    'provisioning-server-service' => 'mape-provisioning-server.service.tmpl',
);
if (!exists $mode_to_template{$mode}) {
    die "Usage: $0 [mape_calc|route-monitor|provisioning-server|route-monitor-service|provisioning-server-service]\n";
}

my $tmpl_file = $mode_to_template{$mode};
my $resolved_path = find_template($tmpl_file, $script_dir, 'mape-provisioning-server', 'bind');
if (!$resolved_path) {
    die "Error: Template file '$tmpl_file' not found in search paths.\n";
}

# ----------------------------------------------------------------------------
# 3. テンプレートの置換と出力
# ----------------------------------------------------------------------------
print render_template_file($resolved_path, \%ENV);

1;
