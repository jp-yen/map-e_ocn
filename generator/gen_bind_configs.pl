#!/usr/bin/perl
# generator/gen_bind_configs.pl
use strict;
use warnings;
use File::Basename;
use FindBin qw($RealBin);
use lib $RealBin;
use MapeCommon qw(find_template render_template_file);

my $script_dir = dirname(__FILE__);

sub extract_domain {
    my ($fqdn) = @_;
    return undef unless defined $fqdn;
    $fqdn =~ s/^\s+|\s+$//g;
    $fqdn =~ s/\.$//;
    my @parts = split(/\./, $fqdn);
    return undef if scalar(@parts) < 2;
    shift @parts if scalar(@parts) >= 3; # 3要素以上の場合はホスト名を取り除いてドメイン部分を取得
    return join('.', @parts);
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
    return sort keys %domains;
}

my $mode = $ARGV[0] || '';

my $ddns_conf = "$script_dir/../ddns.conf";
$ddns_conf = "/etc/ddns.conf" unless -f $ddns_conf;
my @ddns_domains = load_ddns_domains($ddns_conf);

if ($mode eq 'named_local') {
    my $final_tmpl = find_template('named.conf.local.tmpl', $script_dir, 'bind');
    die "Error: Template file 'named.conf.local.tmpl' not found.\n" unless $final_tmpl;
    
    my $content = render_template_file($final_tmpl, {
        BR_IPV4_ADDR => $ENV{BR_IPV4_ADDR} || '100.127.255.255',
        MAPE_PROV_IP  => $ENV{MAPE_PROV_IP}  || '5f00:3aa:9999::ff',
        MAPE_DNS_IP   => $ENV{MAPE_DNS_IP}   || '5f00:3aa:9999::53',
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
        BR_IPV4_ADDR => $ENV{BR_IPV4_ADDR} || '100.127.255.255',
        MAPE_PROV_IP  => $ENV{MAPE_PROV_IP}  || '5f00:3aa:9999::ff',
        MAPE_DNS_IP   => $ENV{MAPE_DNS_IP}   || '5f00:3aa:9999::53',
    });

} elsif ($mode eq 'db_v6') {
    my $final_tmpl = find_template('db.v6connect.net.tmpl', $script_dir, 'bind');
    die "Error: Template file 'db.v6connect.net.tmpl' not found.\n" unless $final_tmpl;
    print render_template_file($final_tmpl, {
        BR_IPV4_ADDR => $ENV{BR_IPV4_ADDR} || '100.127.255.255',
        MAPE_PROV_IP  => $ENV{MAPE_PROV_IP}  || '5f00:3aa:9999::ff',
        MAPE_DNS_IP   => $ENV{MAPE_DNS_IP}   || '5f00:3aa:9999::53',
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

} elsif ($mode eq 'list_domains') {
    foreach my $d (@ddns_domains) {
        print "$d\n";
    }

} else {
    die "Usage: $0 [named_local|db_map|db_v6|ddns_zone <domain>|list_domains]\n";
}
