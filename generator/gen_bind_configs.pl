#!/usr/bin/perl
# generator/gen_bind_configs.pl
use strict;
use warnings;
use File::Basename;
use FindBin qw($RealBin);
use lib $RealBin;
use MapeCommon qw(find_template render_template_file check_config);

my $script_dir = dirname(__FILE__);
check_config("$script_dir/../map-e.conf");

sub extract_domain {
    my ($fqdn) = @_;
    return undef unless defined $fqdn;
    $fqdn =~ s/^\s+|\s+$//g;
    $fqdn =~ s/\.$//;
    return undef if $fqdn eq '' || $fqdn eq '-';
    # @ やアンダースコア、空白など不正文字が含まれているものは除外
    return undef if index($fqdn, '@') >= 0 || index($fqdn, '_') >= 0 || $fqdn =~ /\s/;

    # ce-192.168.2.1.map.ocn.ad.jp のようにホスト名部分にIPアドレスが含まれる場合
    if ($fqdn =~ /^ce-(?:[0-9]{1,3}\.){3}[0-9]{1,3}\.(.+)$/i) {
        my $sub = lc($1);
        return undef if index($sub, '@') >= 0 || index($sub, '_') >= 0 || $sub =~ /\s/;
        return $sub;
    }
    my @parts = split(/\./, $fqdn);
    return undef if scalar(@parts) < 2;

    # 各ラベルの妥当性検証 (RFC 1123: 英数字とハイフン、1〜63文字、先頭末尾ハイフン禁止)
    foreach my $p (@parts) {
        return undef unless $p =~ /^[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?$/;
    }

    # 3要素以上の場合はホスト名を取り除いてドメイン（ゾーン）部分を取得
    shift @parts if scalar(@parts) >= 3;
    return lc(join('.', @parts));
}

sub load_ddns_domains {
    my ($file) = @_;
    my %domains;
    if (-f $file) {
        open my $fh, '<', $file or return ();
        while (<$fh>) {
            chomp;
            next if /^\s*#/ || /^\s*$/;
            my @cols = split(/\s*,\s*/, $_);
            if (scalar(@cols) >= 3) {
                my $v6_domain = extract_domain($cols[1]);
                my $v4_domain = extract_domain($cols[2]);
                $domains{$v6_domain} = 1 if $v6_domain;
                $domains{$v4_domain} = 1 if $v4_domain;
            }
        }
        close $fh;
    }
    # 既存の静的ゾーン（map.ocn.ad.jp, ocn.ad.jp, v6connect.net）は動的ゾーン重複を避けるため除外
    delete $domains{'map.ocn.ad.jp'};
    delete $domains{'ocn.ad.jp'};
    delete $domains{'v6connect.net'};
    return sort keys %domains;
}

my $mode = $ARGV[0] || '';

my $ddns_conf = "$script_dir/../ddns.conf";
$ddns_conf = "/etc/ddns.conf" unless -f $ddns_conf;
my @ddns_domains = load_ddns_domains($ddns_conf);

sub get_rndc_secret {
    my $key_file = "$script_dir/../bind/.rndc.key.secret";
    if (-f $key_file) {
        open my $fh, '<', $key_file or die "Cannot read $key_file: $!";
        my $secret = <$fh>;
        close $fh;
        chomp $secret if $secret;
        return $secret if $secret;
    }
    require MIME::Base64;
    my $random_bytes = join('', map { chr(int(rand(256))) } 1..32);
    my $secret = MIME::Base64::encode_base64($random_bytes, '');
    open my $fh, '>', $key_file or die "Cannot write $key_file: $!";
    print $fh "$secret\n";
    close $fh;
    chmod 0600, $key_file;
    return $secret;
}

if ($mode eq 'named_local') {
    my $final_tmpl = find_template('named.conf.local.tmpl', $script_dir, 'bind');
    die "Error: Template file 'named.conf.local.tmpl' not found.\n" unless $final_tmpl;
    
    my $content = render_template_file($final_tmpl, {
        BR_IPV4_ADDR => $ENV{BR_IPV4_ADDR},
        MAPE_PROV_IP  => $ENV{MAPE_PROV_IP},
        MAPE_DNS_IP   => $ENV{MAPE_DNS_IP},
    });
    
    print $content;
    print "\n// --- DDNS (Dynamic DNS) Dynamic Zones ---\n";
    foreach my $domain (@ddns_domains) {
        print "zone \"$domain\" {\n";
        print "    type master;\n";
        print "    file \"/var/lib/bind/db.$domain\";\n";
        print "    allow-update { any; };\n";
        print "};\n\n";
    }

} elsif ($mode eq 'db_map') {
    my $final_tmpl = find_template('db.map.ocn.ad.jp.tmpl', $script_dir, 'bind');
    die "Error: Template file 'db.map.ocn.ad.jp.tmpl' not found.\n" unless $final_tmpl;
    print render_template_file($final_tmpl, {
        BR_IPV4_ADDR => $ENV{BR_IPV4_ADDR},
        MAPE_PROV_IP  => $ENV{MAPE_PROV_IP},
        MAPE_DNS_IP   => $ENV{MAPE_DNS_IP},
    });

} elsif ($mode eq 'db_v6') {
    my $final_tmpl = find_template('db.v6connect.net.tmpl', $script_dir, 'bind');
    die "Error: Template file 'db.v6connect.net.tmpl' not found.\n" unless $final_tmpl;
    print render_template_file($final_tmpl, {
        BR_IPV4_ADDR => $ENV{BR_IPV4_ADDR},
        MAPE_PROV_IP  => $ENV{MAPE_PROV_IP},
        MAPE_DNS_IP   => $ENV{MAPE_DNS_IP},
    });

} elsif ($mode eq 'ddns_zone') {
    my $domain = $ARGV[1];
    die "Usage: $0 ddns_zone <domain>\n" unless $domain;
    print "\$TTL 300\n";
    print "@ IN SOA ns1.$domain. hostmaster.$domain. (\n";
    print "    1          ; serial\n";
    print "    600        ; refresh\n";
    print "    1800       ; retry\n";
    print "    604800     ; expire\n";
    print "    300        ; minimum\n";
    print ")\n";
    print "@ IN NS ns1.$domain.\n";
    print "ns1 IN A 127.0.0.1\n";

} elsif ($mode eq 'named_conf') {
    my $rndc_secret = get_rndc_secret();
    my $system_dns = $ENV{SYSTEM_DNS} || '';
    my @forwarders = grep { length($_) } split(/\s+/, $system_dns);
    my $forwarders_block = "";
    if (@forwarders) {
        $forwarders_block = "    forwarders {\n" . join("", map { "        $_;\n" } @forwarders) . "    };\n    forward first;\n";
    }
    print <<"EOF";
// /etc/bind/named.conf (Generated for BIND 9.21 container)
key "rndc-key" {
    algorithm hmac-sha256;
    secret "$rndc_secret";
};

controls {
    inet 127.0.0.1 port 953
    allow { 127.0.0.1; } keys { "rndc-key"; };
};

options {
    directory "/var/cache/bind";
    listen-on { any; };
    listen-on-v6 { any; };
    allow-query { any; };
    allow-recursion { any; };
    recursion yes;
    dnssec-validation no;
$forwarders_block};

// 外部への IPv6 問い合わせを停止（外部 IPv6 到達性がない環境向け）
server ::/0 {
    bogus yes;
};

logging {
    channel mape_stderr {
        stderr;
        severity info;
        print-time yes;
        print-category yes;
        print-severity yes;
    };
    category default { mape_stderr; };
    category general { mape_stderr; };
    category config { mape_stderr; };
    category client { mape_stderr; };
};

include "/etc/bind/named.conf.local";
EOF
    exit 0;

} elsif ($mode eq 'rndc_conf') {
    my $rndc_secret = get_rndc_secret();
    print <<"EOF";
// /etc/bind/rndc.conf (Generated for BIND 9.21 container)
key "rndc-key" {
    algorithm hmac-sha256;
    secret "$rndc_secret";
};

options {
    default-key "rndc-key";
    default-server 127.0.0.1;
    default-port 953;
};
EOF
    exit 0;

} elsif ($mode eq 'list_domains') {
    foreach my $d (@ddns_domains) {
        print "$d\n";
    }

} else {
    die "Usage: $0 [named_local|named_conf|db_map|db_v6|ddns_zone <domain>|list_domains]\n";
}
