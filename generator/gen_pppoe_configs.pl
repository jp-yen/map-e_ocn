#!/usr/bin/perl
# generator/gen_pppoe_configs.pl
use strict;
use warnings;
use File::Basename;
use FindBin qw($RealBin);
use lib $RealBin;
use MapeCommon qw(load_conf_file parse_vlan_list check_config);

my $script_dir = dirname(__FILE__);
my $config_file = "$script_dir/../map-e.conf";
check_config($config_file);

my $mode = shift @ARGV || '';
my $target_vlan = shift @ARGV || '';
my $target_service = shift @ARGV || '';

my $mape_if            = $ENV{MAPE_IF};
my $pppoe_vlans_str    = $ENV{PPPOE_VLANS} || '';

# MAP-E (IPoE) 用の VLAN を自動マージ
my @raw_mape_vlans = (
    parse_vlan_list($ENV{SLAAC_DYN_VLANS} || ''),
    parse_vlan_list($ENV{PD_DYN_VLANS} || ''),
    parse_vlan_list($ENV{SLAAC_FIX_VLANS} || ''),
    parse_vlan_list($ENV{PD_FIX_VLANS} || ''),
);

my %all_pppoe_vlan_set;
for my $v (parse_vlan_list($pppoe_vlans_str)) {
    $all_pppoe_vlan_set{$v} = 1;
}
for my $v (@raw_mape_vlans) {
    $all_pppoe_vlan_set{$v} = 1;
}
my @pppoe_vlans        = sort { $a <=> $b } keys %all_pppoe_vlan_set;
my $base_server_ip     = $ENV{PPPOE_SERVER_BASE_IP};
my $pppoe_ip_pool      = $ENV{PPPOE_IP_POOL} || '';
my $web_dashboard_port = $ENV{WEB_DASHBOARD_PORT};
my $mgt_if             = $ENV{MGT_IF} || 'ens3';
my $nat_enabled        = defined $ENV{NAT_ENABLED} ? $ENV{NAT_ENABLED} : '0';
my $br_ipv4_addr       = $ENV{BR_IPV4_ADDR} || '';

sub get_all_services {
    my %svcs;
    if (my $str = $ENV{PPPOE_SERVICES}) {
        $svcs{$_} = 1 for grep { length($_) } split(/\s+/, $str);
    }
    for my $s (keys %svcs) {
        if (length($s) > 11) {
            die "\n" .
                "=" x 80 . "\n" .
                "[ERROR] PPPoE サービス名長さ制限エラー\n" .
                "サービス名 '$s' が " . length($s) . " 文字です。最大 11 文字以下にしてください。\n" .
                "Linux VRF デバイス名 'vrf-<サービス名>' がカーネル上限 15 文字以内に収まる必要があるためです。\n" .
                "=" x 80 . "\n";
        }
    }
    return sort keys %svcs;
}

sub get_auth_conf_path {
    return "$script_dir/../pppoe.conf";
}

# Helper: calculate server IP for index
sub calc_server_ip {
    my ($base_ip, $idx) = @_;
    my @octets = split(/\./, $base_ip);
    die "Invalid base IP: $base_ip" unless @octets == 4;
    my $ip_num = ($octets[0] << 24) + ($octets[1] << 16) + ($octets[2] << 8) + $octets[3];
    $ip_num += $idx;
    return sprintf("%d.%d.%d.%d",
        ($ip_num >> 24) & 0xFF,
        ($ip_num >> 16) & 0xFF,
        ($ip_num >> 8)  & 0xFF,
        $ip_num & 0xFF
    );
}

# Helper: calculate first host IP for a given CIDR
# e.g. 192.168.20.0/24 -> 192.168.20.1
#      192.168.10.5/32 -> 192.168.10.5
sub parse_client_ip_and_route {
    my ($cidr) = @_;
    $cidr =~ s/^\s+|\s+$//g;
    
    if ($cidr =~ m|^(\d+\.\d+\.\d+\.\d+)/(\d+)$|) {
        my ($ip, $mask) = ($1, $2);
        if ($mask == 32) {
            return ($ip, undef);
        }
        my @octets = split(/\./, $ip);
        my $ip_num = ($octets[0] << 24) + ($octets[1] << 16) + ($octets[2] << 8) + $octets[3];
        my $mask_num = (0xFFFFFFFF << (32 - $mask)) & 0xFFFFFFFF;
        my $net_num = $ip_num & $mask_num;
        my $client_ip_num = ($ip_num != $net_num) ? $ip_num : ($net_num + 1); # Use specified IP if host, or .1 if network
        my $client_ip = sprintf("%d.%d.%d.%d",
            ($client_ip_num >> 24) & 0xFF,
            ($client_ip_num >> 16) & 0xFF,
            ($client_ip_num >> 8)  & 0xFF,
            $client_ip_num & 0xFF
        );
        my $net_ip = sprintf("%d.%d.%d.%d",
            ($net_num >> 24) & 0xFF,
            ($net_num >> 16) & 0xFF,
            ($net_num >> 8)  & 0xFF,
            $net_num & 0xFF
        );
        return ($client_ip, "$net_ip/$mask");
    } elsif ($cidr =~ m|^\d+\.\d+\.\d+\.\d+$|) {
        return ($cidr, undef);
    }
    return ($cidr, undef);
}

sub matches_service_name {
    my ($srv_spec, $server_srv) = @_;
    return 1 if !$srv_spec || $srv_spec eq '*'; # '*' ならすべてに追加
    
    if ($server_srv && lc($srv_spec) eq lc($server_srv)) {
        return 1;
    }
    return 0;
}

# Helper: parse pppoe.conf for a target service
sub _gen_pppoe_service_yaml {
    my (%o) = @_;
    my $vlan_ifs_str      = $o{vlan_ifs_str}      // '';
    my $server_ip         = $o{server_ip}          // '';
    my $pppoe_ip_pool     = $o{pppoe_ip_pool}      // '';
    my $service_names_str = $o{service_names_str};   # undef = サービス未定義
    my $comment           = $o{comment}            // '';
    my $br_ipv4           = $o{br_ipv4_addr}       // '';

    # SERVICE_NAMES 環境変数行（サービス未定義時は出力しない）
    my $svc_env_line = defined($service_names_str)
        ? "      - SERVICE_NAMES=$service_names_str\n"
        : '';
    my $comment_line = $comment ? "  $comment\n" : '';

    return <<"YAML";
${comment_line}  pppoe-server:
    image: map-e/pppoe-server:latest
    container_name: pppoe-server
    build:
      context: ./pppoe
      dockerfile: Dockerfile
    network_mode: host
    privileged: true
    restart: always
    depends_on:
      - axosyslog
    logging: *docker-logging
    volumes:
      - /etc/localtime:/etc/localtime:ro
      - /dev/ppp:/dev/ppp
      - ./pppoe/srv-unified/chap-secrets:/etc/ppp/chap-secrets:ro
      - ./pppoe/srv-unified/pap-secrets:/etc/ppp/pap-secrets:ro
      - ./pppoe/srv-unified/routes.conf:/etc/ppp/routes.conf:ro
      - ./pppoe/srv-unified/pppoe-server-options:/etc/ppp/pppoe-server-options:ro
      - ./pppoe/srv-unified/user-services.conf:/etc/ppp/user-services.conf:ro
      - ./pppoe/entrypoint.sh:/entrypoint.sh:ro
      - ./pppoe/ip-up:/etc/ppp/ip-up:ro
      - ./pppoe/ip-down:/etc/ppp/ip-down:ro
      - ./ddns.conf:/etc/ddns.conf:ro
      - syslog_socket:/run/syslog:rw
    environment:
      - TZ=JST-9
      - VLAN_IFS=$vlan_ifs_str
${svc_env_line}      - SERVER_IP=$server_ip
      - INCR_LOCAL_IP=1
      - PPPOE_IP_POOL=$pppoe_ip_pool
      - BR_IPV4_ADDR=$br_ipv4

YAML
}

sub parse_auth_conf {
    my ($conf_file, $target_srv) = @_;
    my @entries;
    return @entries unless -f $conf_file;

    open(my $fh, '<', $conf_file) or die "Cannot open $conf_file: $!";
    while (<$fh>) {
        chomp;
        next if /^\s*#/ || /^\s*$/;
        # 1: User, 2: Pass, 3: IP_Spec, 4: Service_Name (* for all), 5: Note
        my ($user, $pass, $ip_spec, $srv_spec, $note) = split(/\s+/, $_, 5);
        $srv_spec //= '*';
        $note //= '';
        next unless $user && $pass && $ip_spec;
        if ($srv_spec ne '*' && length($srv_spec) > 11) {
            die "\n" .
                "=" x 80 . "\n" .
                "[ERROR] pppoe.conf サービス名長さ制限エラー\n" .
                "ユーザー '$user' のサービス名 '$srv_spec' が " . length($srv_spec) . " 文字です。最大 11 文字以下にしてください。\n" .
                "=" x 80 . "\n";
        }
        next unless matches_service_name($srv_spec, $target_srv);

        my ($client_ip, $delegated_route) = parse_client_ip_and_route($ip_spec);
        # IP欄が * の場合はプール払い出しモード (client_ip を空にする)
        if ($ip_spec eq '*') {
            $client_ip = '';
            $delegated_route = undef;
        }
        push @entries, {
            user            => $user,
            pass            => $pass,
            client_ip       => $client_ip,
            delegated_route => $delegated_route,
        };
    }
    close($fh);
    return @entries;
}

if ($mode eq 'list_vlans') {
    print join(" ", @pppoe_vlans) . "\n";
    exit 0;
}

if ($mode eq 'list_all_services') {
    my @srvs = get_all_services();
    print join(" ", @srvs) . "\n";
    exit 0;
}

# --- 統合モード: 全サービスのシークレットを一括出力 (重複除去) ---
if ($mode eq 'all_secrets') {
    my $conf_file = get_auth_conf_path();
    my @all_services = get_all_services();
    my %seen;
    print "# Unified chap-secrets/pap-secrets (Generated via gen_pppoe_configs.pl)\n";
    print "# client server secret IP-addresses\n";
    # まず全サービスに所属するユーザーを出力
    for my $srv (@all_services) {
        my @entries = parse_auth_conf($conf_file, $srv);
        for my $e (@entries) {
            next if $seen{$e->{user}}++;
            # client_ip が空の場合はプール払い出し -> chap-secrets に * を出力
            my $ip_field = $e->{client_ip} ne '' ? $e->{client_ip} : '*';
            printf("%-30s * %-20s %s\n", "\"$e->{user}\"", "\"$e->{pass}\"", $ip_field);
        }
    }
    # 次にサービス '*' (全対象) のユーザーを追加
    my @entries_all = parse_auth_conf($conf_file, undef);
    for my $e (@entries_all) {
        next if $seen{$e->{user}}++;
        my $ip_field = $e->{client_ip} ne '' ? $e->{client_ip} : '*';
        printf("%-30s * %-20s %s\n", "\"$e->{user}\"", "\"$e->{pass}\"", $ip_field);
    }
    exit 0;
}

# --- 統合モード: 全サービスのルートを一括出力 ---
if ($mode eq 'all_routes') {
    my $conf_file = get_auth_conf_path();
    my @all_services = get_all_services();
    my %seen;
    print "# Unified delegated routes (Generated via gen_pppoe_configs.pl)\n";
    for my $srv (@all_services) {
        my @entries = parse_auth_conf($conf_file, $srv);
        for my $e (@entries) {
            next unless $e->{delegated_route};
            next if $seen{$e->{user}}++;
            printf("%s %s\n", $e->{user}, $e->{delegated_route});
            printf("%s %s\n", $e->{client_ip}, $e->{delegated_route});
        }
    }
    exit 0;
}

# --- 統合モード: user-services.conf 生成 (ユーザー名 -> サービス名 + VRF設定) ---
if ($mode eq 'user_services') {
    my $conf_file = get_auth_conf_path();
    my @all_services = get_all_services();
    my %seen;
    print "# user-services.conf (Generated via gen_pppoe_configs.pl)\n";
    print "# Format: <username> <service_name> <vrf_enabled:0|1> <server_ip>\n";
    for my $srv (@all_services) {
        my $safe_srv = $srv; $safe_srv =~ s/[^A-Za-z0-9_]/_/g;
        my $vrf_enabled = (($ENV{"PPPOE_VRF_ENABLED_${srv}"} // $ENV{"PPPOE_VRF_ENABLED_${safe_srv}"}) // '0') eq '1' ? '1' : '0';
        my $server_ip = $ENV{"PPPOE_SERVER_IP_${srv}"} || $ENV{"PPPOE_SERVER_IP_${safe_srv}"} || '';
        my @entries = parse_auth_conf($conf_file, $srv);
        for my $e (@entries) {
            next if $seen{$e->{user}}++;
            printf("%-30s %-20s %s %s\n", $e->{user}, $srv, $vrf_enabled, $server_ip);
        }
    }
    exit 0;
}

if ($mode eq 'options' || $mode eq 'srv_options') {
    my $srv = ($mode eq 'srv_options') ? $target_vlan : ($target_service || $target_vlan);
    my $br_ipv4 = $ENV{BR_IPV4_ADDR} || '';
    unless ($br_ipv4) {
        my $conf = load_conf_file($config_file);
        $br_ipv4 = $conf->{BR_IPV4_ADDR} || '';
    }
    my $dns_opt = $br_ipv4 ? "ms-dns $br_ipv4\n" : "";
    print <<"EOF";
# /etc/ppp/pppoe-server-options-$srv
auth
require-chap
require-pap
lcp-echo-interval 10
lcp-echo-failure 3
mtu 1452
mru 1452
nodefaultroute
proxyarp
debug
${dns_opt}# Note: rp-pppoe.so plugin is automatically loaded by pppoe-server via command-line arguments.
# Do not specify "plugin rp-pppoe.so" here to avoid duplication errors.
EOF
    exit 0;
}

if ($mode eq 'syslog_update') {
    my $syslog_conf_path = "$script_dir/../syslog-ng/syslog-ng.conf";
    unless (-f $syslog_conf_path) {
        warn "syslog-ng.conf not found at $syslog_conf_path\n";
        exit 0;
    }

    # 統合型: 単一の pppoe-server ソケットに切り替え
    my $sources_block = "# === PPPoE PER-SERVICE SOURCES START (AUTO-GENERATED) ===\n";
    $sources_block .= "source s_pppoe_unified { unix-dgram(\"/run/syslog/pppoe-unified.sock\" perm(0666)); };\n";
    $sources_block .= "# === PPPoE PER-SERVICE SOURCES END ===";

    my $rewrites_block = "# === PPPoE PER-SERVICE REWRITES & DESTINATIONS START (AUTO-GENERATED) ===\n";
    $rewrites_block .= "destination d_pppoe_unified { file(\"/var/log/pppoe/pppoe-server.log\" perm(0666) dir-perm(0777) create-dirs(yes)); };\n";
    $rewrites_block .= "# === PPPoE PER-SERVICE REWRITES & DESTINATIONS END ===";

    my $paths_block = "# === PPPoE PER-SERVICE LOG PATHS START (AUTO-GENERATED) ===\n";
    $paths_block .= "log { source(s_pppoe_unified); destination(d_pppoe); destination(d_pppoe_unified); };\n";
    $paths_block .= "# === PPPoE PER-SERVICE LOG PATHS END ===";

    open(my $in, '<', $syslog_conf_path) or die "Cannot read $syslog_conf_path: $!";
    my $content = do { local $/; <$in> };
    close($in);

    $content =~ s/# === PPPoE PER-SERVICE SOURCES START \(AUTO-GENERATED\) ===.*?# === PPPoE PER-SERVICE SOURCES END ===/$sources_block/s;
    $content =~ s/# === PPPoE PER-SERVICE REWRITES & DESTINATIONS START \(AUTO-GENERATED\) ===.*?# === PPPoE PER-SERVICE REWRITES & DESTINATIONS END ===/$rewrites_block/s;
    $content =~ s/# === PPPoE PER-SERVICE LOG PATHS START \(AUTO-GENERATED\) ===.*?# === PPPoE PER-SERVICE LOG PATHS END ===/$paths_block/s;

    open(my $out, '>', $syslog_conf_path) or die "Cannot write $syslog_conf_path: $!";
    print $out $content;
    close($out);

    print "Updated syslog-ng.conf for unified PPPoE server\n";
    exit 0;
}

if ($mode eq 'compose') {

    print <<"EOF";
# docker-compose.yml (Generated via gen_pppoe_configs.pl)
# Manages MAP-E Router services and PPPoE server containers
x-docker-logging: &docker-logging
  driver: syslog
  options:
    syslog-address: "udp://127.0.0.1:5514"
    tag: "{{.Name}}"

services:
  # 1. Logging Daemon (AxoSyslog)
  axosyslog:
    image: map-e/axosyslog:latest
    container_name: axosyslog
    build:
      context: ./syslog-ng
      dockerfile: Dockerfile
    network_mode: host
    restart: always
    logging: *docker-logging
    environment:
      - TZ=JST-9
    volumes:
      - /etc/localtime:/etc/localtime:ro
      - syslog_socket:/run/syslog:rw
      - ./syslog-ng/syslog-ng.conf:/etc/syslog-ng/syslog-ng.conf:ro
      - ./syslog-ng/logrotate.conf:/etc/logrotate.d/syslog-ng:ro
      - /var/log:/var/log:rw

  # 2. NTP Server (Chrony)
  chrony:
    image: map-e/chrony:latest
    container_name: chrony
    build:
      context: ./chrony
      dockerfile: Dockerfile
    network_mode: host
    cap_add:
      - SYS_TIME
    restart: always
    logging: *docker-logging
    environment:
      - TZ=JST-9
    healthcheck:
      test: ["CMD-SHELL", "chronyc tracking >/dev/null && chronyc sources >/dev/null"]
      interval: 10s
      timeout: 3s
      retries: 3
    volumes:
      - /etc/localtime:/etc/localtime:ro
      - ./chrony/chrony.conf:/etc/chrony/chrony.conf:ro
      - /var/log/chrony:/var/log/chrony:rw
      - syslog_socket:/run/syslog:rw

  # 3. DNS Server (BIND 9.21 Official)
  bind9:
    image: internetsystemsconsortium/bind9:9.21
    container_name: bind9
    command: ["-f", "-c", "/etc/bind/named.conf"]
    network_mode: host
    restart: always
    logging: *docker-logging
    environment:
      - TZ=JST-9
    healthcheck:
      test: ["CMD", "rndc", "-c", "/etc/bind/rndc.conf", "status"]
      interval: 10s
      timeout: 3s
      retries: 3
    volumes:
      - /etc/localtime:/etc/localtime:ro
      - ./bind/named.conf:/etc/bind/named.conf:ro
      - ./bind/rndc.conf:/etc/bind/rndc.conf:ro
      - ./bind/named.conf.local:/etc/bind/named.conf.local:ro
      - ./bind/db.map.ocn.ad.jp:/etc/bind/db.map.ocn.ad.jp:ro
      - ./bind/db.v6connect.net:/etc/bind/db.v6connect.net:ro
      - ./bind/dynamic:/var/lib/bind:rw
      - bind9_cache:/var/cache/bind:rw
      - syslog_socket:/run/syslog:rw

  # 4. IPv6 Router Advertisement (radvd)
  radvd:
    image: map-e/radvd:latest
    container_name: radvd
    build:
      context: ./radvd
      dockerfile: Dockerfile
    network_mode: host
    privileged: true
    restart: always
    logging: *docker-logging
    environment:
      - TZ=JST-9
    volumes:
      - /etc/localtime:/etc/localtime:ro
      - ./radvd/radvd.conf:/etc/radvd.conf:ro
      - syslog_socket:/run/syslog:rw

  # 5. DHCPv6 Server (ISC Kea 2.6.1 Official)
  kea-dhcp6:
    image: docker.cloudsmith.io/isc/docker/kea-dhcp6:2.6.1
    container_name: kea-dhcp6
    command: ["/usr/sbin/kea-dhcp6", "-c", "/etc/kea/kea-dhcp6.conf"]
    network_mode: host
    privileged: true
    restart: always
    logging: *docker-logging
    environment:
      - TZ=JST-9
    healthcheck:
      test: ["CMD", "python3", "-c", "import socket, json; s=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.connect('/run/kea/kea-dhcp6-ctrl.sock'); s.sendall(json.dumps({'command': 'status-get'}).encode()); res=json.loads(s.recv(4096).decode()); exit(res.get('result', 1))"]
      interval: 10s
      timeout: 3s
      retries: 3
    volumes:
      - /etc/localtime:/etc/localtime:ro
      - ./kea-dhcp6/kea-dhcp6.conf:/etc/kea/kea-dhcp6.conf:ro
      - ./kea-dhcp6/kea-map-e-hook:/usr/local/bin/kea-map-e-hook:ro
      - kea_leases:/var/lib/kea:rw
      - syslog_socket:/run/syslog:rw

  # 6. Provisioning server service
  mape-provisioning-server:
    image: map-e/provisioning-server:latest
    container_name: mape-provisioning-server
    build:
      context: ./mape-provisioning-server
      dockerfile: Dockerfile
    network_mode: host
    privileged: true
    restart: always
    logging: *docker-logging
    environment:
      - TZ=JST-9
    volumes:
      - /etc/localtime:/etc/localtime:ro
      - ./mape-provisioning-server/mape-provisioning-server:/app/mape-provisioning-server:ro
      - ./mape-provisioning-server/mape_calc:/usr/local/bin/mape_calc.py:ro
      - ./mape-provisioning-server/server.crt:/usr/local/bin/server.crt:ro
      - ./mape-provisioning-server/server.key:/usr/local/bin/server.key:ro
      - ./map-e-static-ip.conf:/etc/map-e-static-ip.conf:ro
      - ./ddns.conf:/etc/ddns.conf:ro
      - kea_leases:/var/lib/kea:ro
      - /var/log:/var/log:rw
      - syslog_socket:/run/syslog:rw

EOF

    # 7. PPPoE services - 統合型単一サーバー (ユーザー名で動的 VRF 振分け)
    my @all_services       = get_all_services();
    my $vlan_ifs_str       = join(" ", map { "$mape_if.$_" } @pppoe_vlans);
    my $service_names_str  = join(" ", @all_services);
    my $server_start_ip    = $base_server_ip ? calc_server_ip($base_server_ip, 1) : '100.126.241.1';

    # サービスあり/なしで共通の YAML を生成するヘルパーを呼び出す
    print _gen_pppoe_service_yaml(
        vlan_ifs_str      => $vlan_ifs_str,
        server_ip         => $server_start_ip,
        pppoe_ip_pool     => $pppoe_ip_pool,
        service_names_str => @all_services ? $service_names_str : undef,
        br_ipv4_addr      => $br_ipv4_addr,
        comment           => @all_services
            ? "# 7. PPPoE Server (統合型: 全サービスを単一コンテナで受け付け、ユーザー名で動的VRF振分け)"
            : undef,
    );

    print <<"EOF";
  # 8. Web Dashboard Service (Client monitoring & management)
  web-dashboard:
    image: map-e/web-dashboard:latest
    container_name: web-dashboard
    build:
      context: ./web-dashboard
      dockerfile: Dockerfile
    network_mode: host
    pid: host
    privileged: true
    user: "1000:1000"
    group_add:
      - "989"
      - "101"
    restart: always
    logging: *docker-logging
    environment:
      - TZ=JST-9
      - PORT=$web_dashboard_port
    volumes:
      - /etc/localtime:/etc/localtime:ro
      - ./web-dashboard:/app
      - .:/app/workspace:rw
      - /var/run/docker.sock:/var/run/docker.sock:rw
      - /usr/bin/docker:/usr/bin/docker:ro
      - /usr/libexec/docker/cli-plugins:/usr/libexec/docker/cli-plugins:ro
      - /etc/network:/etc/network:rw
      - /etc/sysctl.d:/etc/sysctl.d:rw
      - /var/log:/var/log:ro
      - kea_leases:/var/lib/kea:ro

  # 9. Central NAT Service (Outbound NAT for MAP-E and PPPoE)
  central-nat:
    image: map-e/central-nat:latest
    container_name: central-nat
    build:
      context: ./nat
      dockerfile: Dockerfile
    network_mode: host
    privileged: true
    restart: always
    logging: *docker-logging
    environment:
      - TZ=JST-9
      - MGT_IF=$mgt_if
      - NAT_ENABLED=$nat_enabled
    volumes:
      - /etc/localtime:/etc/localtime:ro

volumes:
  bind9_cache:
  kea_leases:
  syslog_socket:

EOF
    exit 0;
}

die "Usage: $0 [list_vlans|list_all_services|all_secrets|all_routes|user_services|srv_options [service]|syslog_update|compose]\n";

