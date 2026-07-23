# =============================================================================
# Makefile for MAP-E Emulation Environment (Decoupled Generation Version)
# =============================================================================

CONFIG_FILE = map-e.conf

REQUIRED_PACKAGES := python3 openssl radvd kea-dhcp6-server bind9 chrony syslog-ng logrotate

.DEFAULT_GOAL := all
.PHONY: all clean generate check install packages archive check-root check-user

SYSTEM_TARGETS = \
    system/interfaces \
    system/99-network-routing.conf

BIND_TARGETS = \
    bind/named.conf.local \
    bind/db.map.ocn.ad.jp \
    bind/db.v6connect.net

KEA_TARGETS = \
    kea-dhcp6/kea-dhcp6.conf \
    kea-dhcp6/kea-map-e-hook

MPE_TARGETS = \
    mape-provisioning-server/mape-provisioning-server \
    mape-provisioning-server/mape_calc \
    mape-provisioning-server/mape-provisioning-server.service \
    mape-provisioning-server/mape-route-monitor \
    mape-provisioning-server/mape-route-monitor.service \
    mape-provisioning-server/server.crt \
    mape-provisioning-server/server.key

TARGETS = \
    $(SYSTEM_TARGETS) \
    radvd/radvd.conf \
    $(KEA_TARGETS) \
    $(BIND_TARGETS) \
    chrony/chrony.conf \
    $(MPE_TARGETS)

PERL_TARGETS = \
	chrony/chrony.conf \
	bind/named.conf.local \
	bind/db.map.ocn.ad.jp \
	bind/db.v6connect.net \
	system/interfaces \
	system/99-network-routing.conf \
	radvd/radvd.conf \
	kea-dhcp6/kea-dhcp6.conf \
	kea-dhcp6/kea-map-e-hook \
	mape-provisioning-server/mape_calc \
	mape-provisioning-server/mape-provisioning-server \
	mape-provisioning-server/mape-provisioning-server.service \
	mape-provisioning-server/mape-route-monitor \
	mape-provisioning-server/mape-route-monitor.service

CUR_DIR = $(shell basename $(CURDIR))

all: generate

generate: $(TARGETS) check-user
	@echo "Successfully generated all configuration files."

$(PERL_TARGETS): $(CONFIG_FILE)
	@mkdir -p $(dir $@)
	$(if $(GEN_CHMOD_BEFORE),@chmod +x $(GEN_SCRIPT))
	@echo "Generating $@ via Perl"
	@set -a; . ./$(CONFIG_FILE); perl $(GEN_SCRIPT) $(GEN_MODE) > $@
	$(if $(GEN_CHMOD_AFTER),@chmod +x $@)

chrony/chrony.conf: GEN_SCRIPT = generator/gen_chrony_config.pl
chrony/chrony.conf: GEN_MODE = chrony

bind/named.conf.local: GEN_SCRIPT = generator/gen_bind_configs.pl
bind/named.conf.local: GEN_MODE = named_local
bind/db.map.ocn.ad.jp: GEN_SCRIPT = generator/gen_bind_configs.pl
bind/db.map.ocn.ad.jp: GEN_MODE = db_map
bind/db.v6connect.net: GEN_SCRIPT = generator/gen_bind_configs.pl
bind/db.v6connect.net: GEN_MODE = db_v6

system/interfaces: GEN_SCRIPT = generator/gen_system_mod.pl
system/interfaces: GEN_MODE = interfaces
system/interfaces: GEN_CHMOD_BEFORE = 1
system/99-network-routing.conf: GEN_SCRIPT = generator/gen_system_mod.pl
system/99-network-routing.conf: GEN_MODE = sysctl
system/99-network-routing.conf: GEN_CHMOD_BEFORE = 1

radvd/radvd.conf: GEN_SCRIPT = generator/gen_radvd_mod.pl
radvd/radvd.conf: GEN_MODE = radvd
radvd/radvd.conf: GEN_CHMOD_BEFORE = 1

kea-dhcp6/kea-dhcp6.conf: GEN_SCRIPT = generator/gen_kea_configs.pl
kea-dhcp6/kea-dhcp6.conf: GEN_MODE = dhcp6
kea-dhcp6/kea-map-e-hook: GEN_SCRIPT = generator/gen_kea_configs.pl
kea-dhcp6/kea-map-e-hook: GEN_MODE = hook
kea-dhcp6/kea-map-e-hook: GEN_CHMOD_AFTER = 1

mape-provisioning-server/mape_calc: GEN_SCRIPT = generator/gen_mape_mod.pl
mape-provisioning-server/mape_calc: GEN_MODE = mape_calc
mape-provisioning-server/mape_calc: mape-provisioning-server/mape_calc.tmpl
mape-provisioning-server/mape_calc: GEN_CHMOD_AFTER = 1

mape-provisioning-server/mape-provisioning-server: GEN_SCRIPT = generator/gen_mape_mod.pl
mape-provisioning-server/mape-provisioning-server: GEN_MODE = provisioning-server
mape-provisioning-server/mape-provisioning-server: mape-provisioning-server/mape-provisioning-server.tmpl
mape-provisioning-server/mape-provisioning-server: GEN_CHMOD_AFTER = 1

mape-provisioning-server/mape-provisioning-server.service: GEN_SCRIPT = generator/gen_mape_mod.pl
mape-provisioning-server/mape-provisioning-server.service: GEN_MODE = provisioning-server-service
mape-provisioning-server/mape-provisioning-server.service: mape-provisioning-server/mape-provisioning-server.service.tmpl

mape-provisioning-server/mape-route-monitor: GEN_SCRIPT = generator/gen_mape_mod.pl
mape-provisioning-server/mape-route-monitor: GEN_MODE = route-monitor
mape-provisioning-server/mape-route-monitor: mape-provisioning-server/mape-route-monitor.tmpl
mape-provisioning-server/mape-route-monitor: GEN_CHMOD_AFTER = 1

mape-provisioning-server/mape-route-monitor.service: GEN_SCRIPT = generator/gen_mape_mod.pl
mape-provisioning-server/mape-route-monitor.service: GEN_MODE = route-monitor-service
mape-provisioning-server/mape-route-monitor.service: mape-provisioning-server/mape-route-monitor.service.tmpl

mape-provisioning-server/server.crt mape-provisioning-server/server.key:
	@mkdir -p mape-provisioning-server
	@if [ ! -f mape-provisioning-server/server.key ] || [ ! -f mape-provisioning-server/server.crt ]; then \
	        echo "Generating self-signed certificate for *.ocn.ad.jp..."; \
	        openssl req -x509 -newkey rsa:2048 -keyout mape-provisioning-server/server.key -out mape-provisioning-server/server.crt \
	                -days 3650 -nodes \
	                -subj "/C=JP/ST=Tokyo/L=Tokyo/O=OCN/CN=*.ocn.ad.jp" \
	                -addext "subjectAltName=DNS:*.ocn.ad.jp,DNS:ocn.ad.jp"; \
	        chmod 600 mape-provisioning-server/server.key; \
	fi

clean:
	rm -f $(TARGETS) STATUS.TXT MAP-E.shar.* MAP-E.tar.xz

check: check-root
	@echo "--- Checking chronyd Syntax ---"
	@cp /usr/sbin/chronyd /tmp/chronyd_check
	@/tmp/chronyd_check -Q -u root -f chrony/chrony.conf; status=$$?; rm -f /tmp/chronyd_check; exit $$status
	@echo "--- Checking syslog-ng ---"
	@if [ -f syslog-ng/mape.conf ]; then syslog-ng --syntax-only --cfgfile=syslog-ng/mape.conf; fi
	@echo "--- Checking BIND Syntax ---"
	named-checkconf bind/named.conf.local
	@echo "--- Checking BIND Zone Syntax ---"
	named-checkzone map.ocn.ad.jp bind/db.map.ocn.ad.jp
	named-checkzone v6connect.net bind/db.v6connect.net
	@echo "--- Checking radvd Syntax ---"
	radvd -c -C $$(pwd)/radvd/radvd.conf
	@echo "--- Checking Kea DHCPv6 Config Syntax ---"
	@KEA_BIN=$$(which kea-dhcp6 2>/dev/null || for p in /usr/sbin/kea-dhcp6 /usr/local/sbin/kea-dhcp6; do [ -x "$$p" ] && echo "$$p" && break; done); \
	if [ -z "$$KEA_BIN" ]; then \
	        echo "Error: kea-dhcp6 binary not found." >&2; \
	        exit 1; \
	fi; \
	$$KEA_BIN -t $$(pwd)/kea-dhcp6/kea-dhcp6.conf

install: check-root
	@echo "Installing configurations to system directories..."
	@set -a; . ./$(CONFIG_FILE); \
	if [ ! -f /etc/network/interfaces ] || ! cmp -s system/interfaces /etc/network/interfaces; then \
	        install -o root -g root -m 644 system/interfaces /etc/network/interfaces; \
	        echo "Reloading network interfaces..."; \
	        if command -v ifdown >/dev/null 2>&1 && command -v ifup >/dev/null 2>&1; then \
	                ifdown -a --interfaces /etc/network/interfaces >/dev/null 2>&1 || true; \
	                ifup -a --interfaces /etc/network/interfaces >/dev/null 2>&1 || true; \
	        elif command -v systemctl >/dev/null 2>&1 && systemctl is-active -q networking; then \
	                systemctl restart networking || true; \
	        fi; \
	fi
	install -o root -g root -m 644 system/99-network-routing.conf /etc/sysctl.d/99-network-routing.conf
	sysctl -p /etc/sysctl.d/99-network-routing.conf || true
	install -o root -g root -m 644 radvd/radvd.conf /etc/radvd.conf
	install -d -o _kea -g _kea -m 755 /etc/kea
	install -o root -g _kea -m 644 kea-dhcp6/kea-dhcp6.conf /etc/kea/kea-dhcp6.conf
	install -o root -g root -m 755 kea-dhcp6/kea-map-e-hook /usr/local/bin/kea-map-e-hook
	if [ -f kea-dhcp6/kea.sudoers ]; then install -o root -g root -m 440 kea-dhcp6/kea.sudoers /etc/sudoers.d/kea; fi
	install -d -o _kea -g _kea -m 755 /var/log/kea
	if [ -d /etc/apparmor.d ] && [ -f apparmor/usr.sbin.kea-dhcp6 ]; then \
	        install -o root -g root -m 644 apparmor/usr.sbin.kea-dhcp6 /etc/apparmor.d/usr.sbin.kea-dhcp6 || true; \
	        install -o root -g root -m 644 apparmor/usr.sbin.kea-lfc /etc/apparmor.d/usr.sbin.kea-lfc || true; \
	        if command -v apparmor_parser >/dev/null 2>&1; then \
	                apparmor_parser -r /etc/apparmor.d/usr.sbin.kea-dhcp6 || true; \
	                apparmor_parser -r /etc/apparmor.d/usr.sbin.kea-lfc || true; \
	        fi; \
	fi
	install -o root -g bind -m 644 bind/named.conf.local /etc/bind/named.conf.local
	install -o root -g bind -m 644 bind/db.map.ocn.ad.jp /etc/bind/db.map.ocn.ad.jp
	install -o root -g bind -m 644 bind/db.v6connect.net /etc/bind/db.v6connect.net
	install -o root -g root -m 644 chrony/chrony.conf /etc/chrony/chrony.conf
	@mkdir -p /usr/local/bin
	install -o root -g root -m 644 mape-provisioning-server/mape_calc /usr/local/bin/mape_calc.py
	install -o _kea -g _kea -m 755 mape-provisioning-server/mape-provisioning-server /usr/local/bin/mape-provisioning-server
	install -o root -g root -m 644 mape-provisioning-server/mape-provisioning-server.service /etc/systemd/system/mape-provisioning-server.service
	install -o root -g root -m 755 mape-provisioning-server/mape-route-monitor /usr/local/bin/mape-route-monitor
	install -o root -g root -m 644 mape-provisioning-server/mape-route-monitor.service /etc/systemd/system/mape-route-monitor.service
	install -o _kea -g _kea -m 600 mape-provisioning-server/server.crt /usr/local/bin/server.crt
	install -o _kea -g _kea -m 600 mape-provisioning-server/server.key /usr/local/bin/server.key
	if [ -f map-e-static-ip.conf ]; then install -o root -g root -m 644 map-e-static-ip.conf /etc/map-e-static-ip.conf; fi
	if [ -d /etc/syslog-ng/conf.d ] && [ -f syslog-ng/mape.conf ]; then install -o root -g root -m 644 syslog-ng/mape.conf /etc/syslog-ng/conf.d/; fi
	if [ -d /etc/logrotate.d ] && [ -f syslog-ng/mape.logrotate ]; then install -o root -g root -m 644 syslog-ng/mape.logrotate /etc/logrotate.d/mape; fi

	@if systemctl list-unit-files | grep -q 'kea-dhcp6-server'; then \
	        echo "Reloading services..."; \
	        systemctl daemon-reload; \
	        rndc reload || true; \
	        systemctl enable radvd || true; \
	        systemctl stop radvd || true; \
	        systemctl start radvd || true; \
	        systemctl restart kea-dhcp6-server || true; \
	        systemctl restart chrony || true; \
	        systemctl enable mape-provisioning-server.service mape-route-monitor.service 2>/dev/null || true; \
	        systemctl restart mape-provisioning-server.service mape-route-monitor.service || true; \
	        if command -v syslog-ng-ctl >/dev/null 2>&1; then syslog-ng-ctl reload || true; fi; \
	fi

packages: check-root
	@echo "Ensuring required packages are installed..."
	@for pkg in $(REQUIRED_PACKAGES); do \
	        if ! dpkg -s "$$pkg" >/dev/null 2>&1; then \
	                DEBIAN_FRONTEND=noninteractive apt-get update >/dev/null && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$$pkg"; \
	        fi; \
	done

archive: check-user
	-sudo sysctl net.ipv6.conf | grep -e forwarding -e disable_ipv6 > STATUS.TXT
	printf '\n' >> STATUS.TXT 2>&1
	-sudo sysctl net.ipv4.ip_forward net.ipv4.conf.default.rp_filter net.ipv4.conf.all.rp_filter >> STATUS.TXT
	for srv in radvd kea-dhcp6-server mape-provisioning-server mape-route-monitor.service ufw; do \
	        printf "\n\$$ systemctl status $$srv\n" >> STATUS.TXT 2>&1; \
	        sudo systemctl status $$srv >> STATUS.TXT 2>&1 || true; \
	done

	printf '\n$$ iptables -L\n' >> STATUS.TXT 2>&1; sudo iptables -L >> STATUS.TXT 2>&1 || true
	-rm -f MAP-E.shar.0*
	( cd .. && find $(CUR_DIR) -type f \! -path '*/.vscode*' \! -name 'server.key' \! -name 'server.crt' \! -name 'MAP-E.shar*' \! -name 'STATUS.TXT' | xargs shar -T --whole-size-limit=50 --no-md5-digest -o $(CUR_DIR)/MAP-E.shar )
	( cd .. && find $(CUR_DIR) -type f \! -path '*/.vscode*' \! -name 'server.key' \! -name 'server.crt' \! -name 'MAP-E.shar*' \! -name 'STATUS.TXT' | xargs tar cJvf $(CUR_DIR)/MAP-E.tar.xz )

check-root:
	@if [ "$$(id -u)" -ne 0 ]; then echo "Error: root権限が必要です。"; exit 1; fi

check-user:
	@if [ "$$(id -u)" -eq 0 ]; then echo "Error: 一般ユーザーで実行してください。"; exit 1; fi
