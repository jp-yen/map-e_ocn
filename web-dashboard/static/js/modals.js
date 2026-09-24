// =============================================================================
// modals.js — モーダル・ダイアログ管理
//   Detail Modal (PPPoE/MAP-E 詳細)
//   Disconnect Modal (切断確認)
//   FortiGate Certificate Modal
//   ESC キーリスナー
//   依存: showToast(), loadData() (main.js), formatDateTime() (tables.js)
// =============================================================================
// Detail Modal Handlers
function openPPPoEDetail(idx) {
  const c = currentFilteredPPPoE[idx];
  if (!c) return;
  document.getElementById('detailModalIcon').textContent = '⚡';
  document.getElementById('detailModalTitle').textContent = `PPPoE クライアント詳細 (${c.username})`;

  const srvDisplay = c.service_name || c.ac_name;
  const srvMeta = (typeof pppoeServers !== 'undefined')
    ? pppoeServers.find(s => (s.service_name || s.ac_name) === srvDisplay)
    : null;
  const isVrf = Boolean(c.vrf_enabled ?? (srvMeta?.vrf_enabled ?? false));
  const vrfClass = isVrf ? ' server-ac-badge--vrf' : '';
  const vrfIcon = isVrf ? ' 🔒' : '';
  const vrfDevice = c.vrf_device || (isVrf && srvDisplay ? `vrf-${srvDisplay.replace(/\s+/g, '_').slice(0, 11)}` : null);

  const vrfBadge = document.getElementById('detailModalVrfBadge');
  if (vrfBadge) {
    vrfBadge.style.display = isVrf ? 'inline-block' : 'none';
  }

  const modeBadge = document.getElementById('detailModalModeBadge');
  const isFix = c.ip_mode === '固定IP';
  modeBadge.textContent = isFix ? '固定IP' : '動的';
  modeBadge.style.background = isFix ? 'rgba(16, 185, 129, 0.2)' : 'rgba(99, 102, 241, 0.2)';
  modeBadge.style.color = isFix ? 'var(--accent-emerald)' : 'var(--badge-accent-text)';
  modeBadge.style.borderColor = isFix ? 'rgba(16, 185, 129, 0.4)' : 'rgba(99, 102, 241, 0.4)';

  const rawIp = c.remote_ip_cidr || (c.remote_ip + '/32');
  const displayIp = rawIp.endsWith('/32') ? rawIp.slice(0, -3) : rawIp;
  const pingText = c.ping_ok
    ? `<span style="color:var(--accent-emerald); font-weight:600;">● 応答あり (${c.ping_rtt || '正常'})</span>`
    : `<span style="color:var(--accent-rose); font-weight:600;">✕ 応答なし</span>`;

  document.getElementById('detailModalBody').innerHTML = `
        <div class="detail-grid">
          <div class="detail-card">
            <div class="detail-label">ホスト名 (FQDN)</div>
            <div class="detail-value mono nowrap" style="color:var(--text-strong); font-size:13px; font-weight:600;" title="${c.ddns_v4 || '-'}">${c.ddns_v4 || '-'}</div>
          </div>
          <div class="detail-card">
            <div class="detail-label">備考 (Note)</div>
            <div class="detail-value nowrap" style="color:var(--accent-amber); font-weight:600;" title="${c.note || '-'}">${c.note || '-'}</div>
          </div>
          <div class="detail-card">
            <div class="detail-label">アカウント (ユーザー名)</div>
            <div class="detail-value user-tag nowrap" style="display:inline-block;">${c.username}</div>
          </div>
          <div class="detail-card">
            <div class="detail-label">クライアントIP</div>
            <div class="detail-value mono nowrap" style="color:var(--accent-cyan); font-weight:600; font-size:15px;">${displayIp}</div>
          </div>
          <div class="detail-card">
            <div class="detail-label">IF 名 (インターフェース)</div>
            <div class="detail-value mono nowrap" style="color:var(--text-strong); font-size:14px;">${c.interface}</div>
          </div>
          <div class="detail-card">
            <div class="detail-label">MAC アドレス</div>
            <div class="detail-value mono nowrap" style="color:var(--text-strong); font-size:14px;">${c.mac || '-'}</div>
          </div>
          <div class="detail-card">
            <div class="detail-label">サービス名 (Service Name)</div>
            <div class="detail-value nowrap" style="display:flex; align-items:center;">
              ${(srvDisplay && srvDisplay !== '*')
      ? `<span class="server-ac-badge${vrfClass}" style="${getServiceBadgeStyle(srvDisplay)}">${srvDisplay}${vrfIcon}</span>`
      : '<span style="color:var(--text-faint);">-</span>'}
            </div>
          </div>
          <div class="detail-card">
            <div class="detail-label">閉域網分離 (VRF)</div>
            <div class="detail-value nowrap" style="display:flex; flex-direction:column; gap:2px;">
              ${isVrf
      ? `<span style="color:var(--accent-amber); font-weight:700; display:flex; align-items:center; gap:4px;">
                     <span>🔒</span> 有効 (${vrfDevice || 'VRF 分離中'})
                   </span>`
      : `<span style="color:var(--text-muted); font-weight:500;">無効 (グローバル / main)</span>`
    }
            </div>
          </div>
          <div class="detail-card">
            <div class="detail-label">接続 VLAN / 物理IF</div>
            <div class="detail-value mono nowrap" style="color:var(--text-strong);">${c.vlan || '-'}</div>
          </div>
          <div class="detail-card">
            <div class="detail-label">接続開始時刻</div>
            <div class="detail-value mono nowrap" style="color:var(--text-soft);">${formatDateTime(c.connect_time)}</div>
          </div>
          <div class="detail-card">
            <div class="detail-label">接続時間 (Uptime)</div>
            <div class="detail-value mono nowrap" style="color:var(--text-soft); font-weight:600;">${c.uptime}</div>
          </div>
          <div class="detail-card">
            <div class="detail-label">プロセス PID</div>
            <div class="detail-value mono nowrap" style="color:var(--text-muted);">${c.pid || '-'}</div>
          </div>
          <div class="detail-card">
            <div class="detail-label">Ping 疎通</div>
            <div class="detail-value">${pingText}</div>
          </div>
        </div>
      `;

  document.getElementById('detailModalFooter').innerHTML = `
        <button class="btn-primary" style="padding:7px 14px; font-size:13px; display:inline-flex; align-items:center; gap:6px; background:linear-gradient(135deg, #f97316 0%, #ea580c 100%); border:none; color:#fff;" onclick="openRustScanModal('${c.remote_ip}', { vrf: '${vrfDevice || ''}', title: 'PPPoE: ${escapeHtml(c.username || c.remote_ip)}' })">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
          ⚡ RustScan (全ポート)
        </button>
        <button class="btn-disconnect" style="padding:7px 14px; font-size:13px;" onclick="closeClientDetailModal(); confirmDisconnectPPPoE('${c.interface}', ${c.pid}, '${c.username}', '${c.remote_ip}')">切断</button>
        <button class="btn-cancel" onclick="closeClientDetailModal()">閉じる</button>
      `;
  document.getElementById('clientDetailModal').style.display = 'flex';
}

function openMAPEDetail(idx) {
  const c = currentFilteredMAPE[idx];
  if (!c) return;
  document.getElementById('detailModalIcon').textContent = '🔗';
  document.getElementById('detailModalTitle').textContent = `MAP-E クライアント詳細 (${c.ipv4})`;

  const vrfBadge = document.getElementById('detailModalVrfBadge');
  if (vrfBadge) {
    vrfBadge.style.display = 'none';
  }

  const modeBadge = document.getElementById('detailModalModeBadge');
  const mode = c.mode_display || 'DHCP-PD (動的)';
  const norm = String(mode).trim();
  const l1 = (norm.includes('PD') || norm.includes('DHCP-PD')) ? 'DHCP-PD' : 'SLAAC';
  const l2 = (norm.includes('固定') || norm.includes('fix') || norm.includes('static')) ? '固定' : '動的';

  const normKey = `${l1} ${l2}`;
  modeBadge.innerHTML = `<span class="badge-tier-main">${l1}</span><span class="badge-tier-sub">${l2}</span>`;
  modeBadge.className = 'server-ac-badge server-ac-badge--2tier';
  if (typeof getServiceBadgeStyle === 'function') {
    modeBadge.setAttribute('style', `${getServiceBadgeStyle(normKey)}`);
  }

  const pingText = c.ping_ok
    ? `<span style="color:var(--accent-emerald); font-weight:600;">● 応答あり (${c.ping_rtt || '正常'})</span>`
    : `<span style="color:var(--accent-rose); font-weight:600;">✕ 応答なし</span>`;

  document.getElementById('detailModalBody').innerHTML = `
        <div class="detail-grid">
          <!-- 1行目: 基本識別・備考 -->
          <div class="detail-card">
            <div class="detail-label">ホスト名 (FQDN)</div>
            <div class="detail-value mono nowrap" style="color:var(--text-strong); font-size:13px; font-weight:600;" title="${c.hostname}">${c.hostname}</div>
          </div>
          <div class="detail-card">
            <div class="detail-label">備考 (Note)</div>
            <div class="detail-value nowrap" style="color:var(--accent-amber); font-weight:600;" title="${c.note || '-'}">${c.note || '-'}</div>
          </div>

          <!-- 2行目: 物理・L2インターフェース -->
          <div class="detail-card">
            <div class="detail-label">IF 名 (インターフェース)</div>
            <div class="detail-value mono nowrap" style="color:var(--text-strong); font-size:14px;">${c.interface || '-'}</div>
          </div>
          <div class="detail-card">
            <div class="detail-label">MAC アドレス (HWADDR)</div>
            <div class="detail-value mono nowrap" style="color:var(--text-strong); font-size:14px;">${c.hwaddr !== '-' ? c.hwaddr : '-'}</div>
          </div>

          <!-- 3行目: 収容・ネットワークプレフィックス (左: VLAN / 右: 委任IPv6) -->
          <div class="detail-card">
            <div class="detail-label">VLAN 番号</div>
            <div class="detail-value mono nowrap" style="color:var(--text-strong); font-weight:600;">${c.vlan}</div>
          </div>
          <div class="detail-card">
            <div class="detail-label">委任 IPv6 プレフィックス</div>
            <div class="detail-value mono nowrap" style="color:var(--accent-primary-hover); font-size:12.5px; font-weight:600;" title="${c.prefix}">${c.prefix}</div>
          </div>

          <!-- 4行目: 通信アドレス (左: 割当IPv4 / 右: CE IPv6トンネル終端) -->
          <div class="detail-card">
            <div class="detail-label">割当 IPv4 アドレス</div>
            <div class="detail-value mono nowrap" style="color:var(--accent-cyan); font-size:15px; font-weight:700;">${c.ipv4}</div>
          </div>
          <div class="detail-card">
            <div class="detail-label">CE IPv6 アドレス (トンネル終端)</div>
            <div class="detail-value mono nowrap" style="color:var(--accent-primary-hover); font-size:11.8px; letter-spacing:-0.02em;" title="${c.ce_ipv6}">${c.ce_ipv6}</div>
          </div>

          <!-- 5行目: 共有ポート・対向BR (左: PSID / 右: Gateway IPv6リンクローカル) -->
          <div class="detail-card">
            <div class="detail-label">PSID (ポートセットID)</div>
            <div class="detail-value mono nowrap" style="color:var(--accent-cyan); font-weight:700; font-size:15px;">${c.psid ?? 0}</div>
          </div>
          <div class="detail-card">
            <div class="detail-label">Gateway IPv6 (リンクローカル)</div>
            <div class="detail-value mono nowrap" style="color:var(--accent-primary-hover); font-size:12px; letter-spacing:-0.01em;" title="${c.gateway_ipv6 || '-'}">${c.gateway_ipv6 || '-'}</div>
          </div>

          <!-- 6行目: 接続・トンネル日時 -->
          <div class="detail-card">
            <div class="detail-label">接続開始日時</div>
            <div class="detail-value mono nowrap" style="color:var(--text-soft);">${formatDateTime(c.connect_start || c.lease_start)}</div>
          </div>
          <div class="detail-card">
            <div class="detail-label">トンネル作成日時</div>
            <div class="detail-value mono nowrap" style="color:var(--text-soft);">${formatDateTime(c.tunnel_created)}</div>
          </div>

          <!-- 7行目: DHCP-PD リース日時 -->
          <div class="detail-card">
            <div class="detail-label">DHCP-PD リース開始 (初回 / 更新)</div>
            <div class="detail-value mono nowrap" style="color:var(--text-soft); font-size:12px;">
              ${formatDateTime(c.initial_lease_start || c.raw_lease_start)}
              ${c.last_renewed && c.last_renewed !== c.initial_lease_start && c.last_renewed !== '-' ? `<span style="font-size:11px;color:var(--text-muted);display:block;">更新: ${formatDateTime(c.last_renewed)}</span>` : ''}
            </div>
          </div>
          <div class="detail-card">
            <div class="detail-label">DHCP-PD 有効期限</div>
            <div class="detail-value mono nowrap" style="color:var(--text-soft);">${formatDateTime(c.lease_expire)} <span style="font-size:11px;color:var(--text-muted);">(${c.valid_lifetime})</span></div>
          </div>

          <!-- 8行目: プロビジョニング履歴・動的ステータス (Ping) -->
          <div class="detail-card">
            <div class="detail-label">最終プロビジョニング日時</div>
            <div class="detail-value mono nowrap" style="color:var(--text-soft);">${formatDateTime(c.last_provisioned)}</div>
          </div>
          <div class="detail-card">
            <div class="detail-label">Ping 疎通</div>
            <div class="detail-value">${pingText}</div>
          </div>

          <!-- 9行目: ハードウェア固有識別子 (全幅) -->
          <div class="detail-card" style="grid-column: 1 / -1;">
            <div class="detail-label">DHCPv6 DUID</div>
            <div class="detail-value mono nowrap" style="font-size:12px; color:var(--text-muted);" title="${c.duid}">${c.duid}</div>
          </div>
        </div>
      `;

  const targetV4 = (c.ping_target && !c.ping_target.includes(':') && c.ping_target !== '-')
    ? c.ping_target
    : (function(ip) {
        if (!ip || ip === '-' || ip === '0.0.0.0') return '';
        const clean = ip.trim();
        if (clean.includes('/')) {
          const parts = clean.split('/');
          const octets = parts[0].split('.');
          const prefixLen = parseInt(parts[1], 10);
          if (octets.length === 4 && prefixLen < 31) {
            octets[3] = String(Number(octets[3]) + 1);
            return octets.join('.');
          }
          return parts[0];
        }
        return clean;
      })(c.ipv4);

  const escHost = (typeof escapeHtml === 'function') ? escapeHtml(c.hostname || targetV4) : (c.hostname || targetV4);

  document.getElementById('detailModalFooter').innerHTML = `
        ${targetV4 ? `
        <button class="btn-primary" style="padding:7px 14px; font-size:13px; display:inline-flex; align-items:center; gap:6px; background:linear-gradient(135deg, #f97316 0%, #ea580c 100%); border:none; color:#fff;" onclick="openRustScanModal('${targetV4}', { title: 'MAP-E: ${escHost}' })">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
          ⚡ RustScan (全ポート)
        </button>
        ` : ''}
        <button class="btn-disconnect" style="padding:7px 14px; font-size:13px;" onclick="closeClientDetailModal(); confirmDisconnectMAPE('${c.prefix}', '${c.ipv4}', '${c.ce_ipv6}')">切断</button>
        <button class="btn-cancel" onclick="closeClientDetailModal()">閉じる</button>
      `;
  document.getElementById('clientDetailModal').style.display = 'flex';
}

function closeClientDetailModal() {
  document.getElementById('clientDetailModal').style.display = 'none';
}

// Global ESC key listener to close modals
window.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' || e.key === 'Esc') {
    const ntpModal = document.getElementById('ntpStatusModal');
    if (ntpModal && ntpModal.style.display !== 'none') {
      closeNtpStatusModal();
      return;
    }
    const detailModal = document.getElementById('clientDetailModal');
    if (detailModal && detailModal.style.display !== 'none') {
      closeClientDetailModal();
      return;
    }
    const configModal = document.getElementById('configModal');
    if (configModal && configModal.style.display !== 'none') {
      closeConfigModal();
      return;
    }
    const restartModal = document.getElementById("restartConfirmModal");
    if (restartModal && restartModal.style.display !== "none") {
      closeRestartConfirmModal();
      return;
    }
    const discModal = document.getElementById("disconnectModal");
    if (discModal && discModal.style.display !== "none") {
      closeModal();
      return;
    }
  }
});

// Modal Handlers
const modal = document.getElementById('disconnectModal');
const modalMessage = document.getElementById('modalMessage');
const modalConfirmBtn = document.getElementById('modalConfirmBtn');

function closeModal() {
  modal.style.display = 'none';
  modalConfirmBtn.onclick = null;
}

function confirmDisconnectPPPoE(ifname, pid, username, ip) {
  modalMessage.innerHTML = `
        以下の PPPoE セッションを強制切断しますか？<br><br>
        <strong>アカウント:</strong> ${username}<br>
        <strong>クライアント IP:</strong> ${ip}<br>
        <strong>インターフェース:</strong> ${ifname} (PID: ${pid})<br><br>
        <span style="color:var(--accent-rose); font-size:12px;">※切断シグナル (SIGTERM) を送信して pppd を終了します。</span>
      `;
  modalConfirmBtn.onclick = async () => {
    closeModal();
    try {
      const res = await fetch('/api/disconnect/pppoe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ interface: ifname, pid: pid })
      });
      const json = await res.json();
      if (json.success) {
        showToast(`PPPoE ${ifname} (${username}) を切断しました。`, true);
        setTimeout(() => loadData(true), 800);
      } else {
        showToast(`切断に失敗しました: ${json.error || '不明なエラー'}`, false);
      }
    } catch (e) {
      showToast(`エラーが発生しました: ${e.message}`, false);
    }
  };
  modal.style.display = 'flex';
}

function confirmDisconnectMAPE(prefix, ipv4, ce_ipv6) {
  modalMessage.innerHTML = `
        以下の MAP-E クライアントセッションを切断しますか？<br><br>
        <strong>IPv6 Prefix:</strong> ${prefix}<br>
        <strong>割り当て IPv4:</strong> ${ipv4}<br>
        <strong>CE IPv6:</strong> ${ce_ipv6}<br><br>
        <span style="color:var(--accent-rose); font-size:12px;">※Kea DHCPv6 リース情報、mpe-common トンネルルート、および近傍キャッシュを削除します。</span>
      `;
  modalConfirmBtn.onclick = async () => {
    closeModal();
    try {
      const res = await fetch('/api/disconnect/mape', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prefix: prefix, ipv4: ipv4, ce_ipv6: ce_ipv6 })
      });
      const json = await res.json();
      if (json.success) {
        showToast(`MAP-E クライアント (${prefix}) を切断しました。`, true);
        setTimeout(() => loadData(true), 800);
      } else {
        showToast(`切断に失敗しました: ${json.error || '不明なエラー'}`, false);
      }
    } catch (e) {
      showToast(`エラーが発生しました: ${e.message}`, false);
    }
  };
  modal.style.display = 'flex';
}

// FortiGate Certificate Modal Handlers
async function openCertModal() {
  const modal = document.getElementById('certModal');
  const box = document.getElementById('certCommandBox');
  const rbox = document.getElementById('refreshCommandBox');
  modal.style.display = 'flex';

  try {
    const res = await fetch('/api/cert/fortigate');
    if (!res.ok) throw new Error('証明書データの取得に失敗しました');
    const data = await res.json();
    box.textContent = data.import_command || '証明書が見つかりませんでした';
    rbox.textContent = data.refresh_command || '';
  } catch (err) {
    box.textContent = 'エラー: ' + err.message;
  }
}

function closeCertModal() {
  document.getElementById('certModal').style.display = 'none';
}

async function copyTextToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch (e) {
      // フォールバックへ移行
    }
  }
  // Firefox や HTTP 環境向けのフォールバック処理 (textarea + execCommand)
  const textArea = document.createElement('textarea');
  textArea.value = text;
  textArea.style.position = 'fixed';
  textArea.style.left = '-999999px';
  textArea.style.top = '-999999px';
  textArea.setAttribute('readonly', '');
  document.body.appendChild(textArea);
  textArea.focus();
  textArea.select();
  try {
    const successful = document.execCommand('copy');
    document.body.removeChild(textArea);
    if (!successful) {
      throw new Error('execCommand でのコピーに失敗しました');
    }
  } catch (err) {
    document.body.removeChild(textArea);
    throw err;
  }
}

function copyCertCommand() {
  const box = document.getElementById('certCommandBox');
  const btn = document.getElementById('btnCopyCert');
  copyTextToClipboard(box.textContent).then(() => {
    btn.textContent = '✓ コピー完了！';
    btn.classList.add('copied');
    showToast('FortiGate オレオレ証明書インポートコマンドをコピーしました', true);
    setTimeout(() => {
      btn.textContent = '📋 コピー';
      btn.classList.remove('copied');
    }, 2500);
  }).catch(err => {
    showToast('コピーに失敗しました: ' + err, false);
  });
}

function copyRefreshCommand() {
  const rbox = document.getElementById('refreshCommandBox');
  const btn = document.getElementById('btnCopyRefresh');
  copyTextToClipboard(rbox.textContent).then(() => {
    btn.textContent = '✓ コピー完了！';
    btn.classList.add('copied');
    showToast('再プロビジョニングコマンドをコピーしました', true);
    setTimeout(() => {
      btn.textContent = '📋 コピー';
      btn.classList.remove('copied');
    }, 2500);
  }).catch(err => {
    showToast('コピーに失敗しました: ' + err, false);
  });
}

// =============================================================================
// NTP Status Modal Handlers (chrony)
// =============================================================================
async function openNtpStatusModal() {
  const modal = document.getElementById('ntpStatusModal');
  if (!modal) return;
  modal.style.display = 'flex';
  await refreshNtpStatus();
}

function closeNtpStatusModal() {
  const modal = document.getElementById('ntpStatusModal');
  if (modal) modal.style.display = 'none';
}

async function refreshNtpStatus() {
  const loading = document.getElementById('ntpModalLoading');
  const content = document.getElementById('ntpModalContent');
  if (loading) loading.style.display = 'block';
  if (content) content.style.display = 'none';

  try {
    const res = await fetch('/api/ntp/status');
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    const data = await res.json();
    renderNtpStatus(data);
  } catch (err) {
    if (loading) {
      loading.innerHTML = `<div style="color:var(--accent-rose, #ef4444); padding:16px;">NTP ステータスの取得に失敗しました: ${typeof escapeHtml === 'function' ? escapeHtml(err.message) : err.message}</div>`;
    }
  }
}

function renderNtpStatus(data) {
  const loading = document.getElementById('ntpModalLoading');
  const content = document.getElementById('ntpModalContent');
  if (loading) loading.style.display = 'none';
  if (content) content.style.display = 'block';

  const esc = (typeof escapeHtml === 'function') ? escapeHtml : (s => String(s ?? ''));
  const trk = data.tracking || {};
  const stratum = trk['Stratum'] || '-';
  const refId = trk['Reference ID'] || '-';
  const lastOffset = trk['Last offset'] || '-';
  const freq = trk['Frequency'] || '-';

  // 1. Stratum バッジ
  const badge = document.getElementById('ntpStratumBadge');
  if (badge) {
    if (stratum === '10') {
      badge.textContent = `Stratum ${stratum} (ローカル時計)`;
      badge.style.background = 'rgba(245, 158, 11, 0.18)';
      badge.style.color = 'var(--accent-amber, #f59e0b)';
      badge.style.borderColor = 'rgba(245, 158, 11, 0.4)';
    } else if (stratum !== '-' && Number(stratum) > 0 && Number(stratum) < 16) {
      badge.textContent = `Stratum ${stratum} (同期中)`;
      badge.style.background = 'rgba(16, 185, 129, 0.18)';
      badge.style.color = 'var(--accent-emerald, #10b981)';
      badge.style.borderColor = 'rgba(16, 185, 129, 0.4)';
    } else {
      badge.textContent = `Stratum ${stratum} (未同期)`;
      badge.style.background = 'rgba(239, 68, 68, 0.18)';
      badge.style.color = 'var(--accent-rose, #ef4444)';
      badge.style.borderColor = 'rgba(239, 68, 68, 0.4)';
    }
  }

  // 2. ステータスカード
  const elSync = document.getElementById('ntpValSyncStatus');
  if (elSync) {
    if (stratum === '10') {
      elSync.innerHTML = '<span style="color:var(--accent-amber, #f59e0b);">● 単独稼働 (Local Orphan)</span>';
    } else if (stratum !== '-' && Number(stratum) > 0 && Number(stratum) < 16) {
      elSync.innerHTML = '<span style="color:var(--accent-emerald, #10b981);">● 正常同期中</span>';
    } else {
      elSync.innerHTML = '<span style="color:var(--accent-rose, #ef4444);">✕ 未同期</span>';
    }
  }

  const elRef = document.getElementById('ntpValRefId');
  if (elRef) elRef.textContent = refId;
  const elOff = document.getElementById('ntpValOffset');
  if (elOff) elOff.textContent = lastOffset;
  const elFreq = document.getElementById('ntpValFreq');
  if (elFreq) elFreq.textContent = freq;

  // 3. 上位 NTP Sources テーブル
  const tbody = document.getElementById('ntpSourcesTableBody');
  if (tbody) {
    const sources = data.sources || [];
    if (sources.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" style="padding:14px; text-align:center; color:var(--text-faint, #64748b);">登録された上位 NTP サーバーがありません (または到達不能)</td></tr>';
    } else {
      tbody.innerHTML = sources.map(s => {
        let stateBadge = '';
        if (s.state === '*') {
          stateBadge = '<span class="badge-mode" style="background:rgba(16,185,129,0.2); color:var(--accent-emerald, #10b981); border-color:rgba(16,185,129,0.4);">★ マスター</span>';
        } else if (s.state === '+') {
          stateBadge = '<span class="badge-mode" style="background:rgba(59,130,246,0.2); color:var(--accent-cyan, #06b6d4); border-color:rgba(59,130,246,0.4);">＋ 候補</span>';
        } else if (s.state === '-') {
          stateBadge = '<span class="badge-mode" style="background:rgba(148,163,184,0.15); color:var(--text-soft, #94a3b8);">－ 良好</span>';
        } else if (s.state === '?') {
          stateBadge = '<span class="badge-mode" style="background:rgba(239,68,68,0.15); color:var(--accent-rose, #ef4444); border-color:rgba(239,68,68,0.3);">？ 未達/不達</span>';
        } else {
          stateBadge = `<span class="badge-mode">${esc(s.state)}</span>`;
        }

        const reachVal = s.reach;
        const reachDisplay = (reachVal === '377')
          ? '<span style="color:var(--accent-emerald, #10b981); font-weight:600;">377 (良好)</span>'
          : (reachVal === '0')
            ? '<span style="color:var(--accent-rose, #ef4444); font-weight:600;">0 (未達)</span>'
            : `<span style="color:var(--accent-amber, #f59e0b);">${esc(reachVal)}</span>`;

        return `
          <tr style="border-bottom: 1px solid var(--border-subtle, rgba(255,255,255,0.06));">
            <td style="padding: 8px 10px;">${stateBadge}</td>
            <td style="padding: 8px 10px; font-weight: 600; color: var(--text-strong, #f8fafc); font-family: monospace;">${esc(s.name)}</td>
            <td style="padding: 8px 10px; font-family: monospace;">${esc(s.stratum)}</td>
            <td style="padding: 8px 10px; font-family: monospace;">${reachDisplay}</td>
            <td style="padding: 8px 10px; font-family: monospace;">${esc(s.poll)}</td>
            <td style="padding: 8px 10px; font-family: monospace; color: var(--text-soft, #94a3b8);">${esc(s.last_sample)}</td>
          </tr>
        `;
      }).join('');
    }
  }

  // 4. Raw テキスト
  const elRawTrk = document.getElementById('ntpRawTracking');
  if (elRawTrk) elRawTrk.textContent = data.raw_tracking || '-';
  const elRawCli = document.getElementById('ntpRawClients');
  if (elRawCli) elRawCli.textContent = data.raw_clients || '-';
}

// =============================================================================
// RustScan 全ポートスキャンモーダル制御 (エスケープシーケンス除去 & 高視認性フォーマッタ)
// =============================================================================
window._rustscanSource = null;
window._rustscanRawOutput = '';

function stripAnsiCodes(text) {
  if (!text) return '';
  return text.replace(/[\u001b\u009b][[()#;?]*(?:[0-9]{1,4}(?:;[0-9]{0,4})*)?[0-9A-ORZcf-nqry=><]/g, '');
}

function formatRustScanLogLine(rawLine) {
  const line = stripAnsiCodes(rawLine);
  const esc = (typeof escapeHtml === 'function') ? escapeHtml : (s => String(s ?? ''));
  const trimmed = line.trim();

  if (!trimmed) {
    return '<div>&nbsp;</div>';
  }

  // 1. オープンポート検出 (RustScan)
  if (trimmed.startsWith('Open ')) {
    return `<div style="color:#10b981; font-weight:700; background:rgba(16,185,129,0.1); padding:2px 6px; border-radius:4px; margin:2px 0;">🟢 [OPEN] ${esc(trimmed)}</div>`;
  }

  // 2. Nmap テーブルヘッダー
  if (trimmed.startsWith('PORT') && trimmed.includes('STATE')) {
    return `<div style="color:#38bdf8; font-weight:700; border-bottom:1px solid rgba(56,189,248,0.3); padding:4px 0 2px; margin-top:6px;">${esc(trimmed)}</div>`;
  }

  // 3. Nmap スキャン結果行 (open)
  if (/^\d+\/\w+\s+open\s+/.test(trimmed)) {
    return `<div style="color:#34d399; font-weight:600; padding:1px 0;">  ✔ ${esc(trimmed)}</div>`;
  }

  // 4. Nmap スキャン結果行 (filtered / closed)
  if (/^\d+\/\w+\s+(filtered|closed)\s+/.test(trimmed)) {
    return `<div style="color:#94a3b8; font-size:11.5px; padding:1px 0;">  · ${esc(trimmed)}</div>`;
  }

  // 5. ホスト疎通確認 / スキャン進行情報
  if (trimmed.startsWith('Host is up') || trimmed.startsWith('Initiating') || trimmed.startsWith('Completed') || trimmed.startsWith('Scanning')) {
    return `<div style="color:#60a5fa; font-size:11.5px;">ℹ ${esc(trimmed)}</div>`;
  }

  // 6. Nmap 完了行
  if (trimmed.startsWith('Nmap done:')) {
    return `<div style="color:#c084fc; font-weight:600; margin-top:6px; border-top:1px dashed rgba(192,132,252,0.3); padding-top:4px;">✨ ${esc(trimmed)}</div>`;
  }

  // 7. オープンポートなし / 警告
  if (trimmed.includes("didn't find any open ports") || trimmed.startsWith('[!]')) {
    return `<div style="color:#fbbf24; font-weight:600; background:rgba(245,158,11,0.1); padding:4px 8px; border-radius:4px; margin:4px 0;">⚠️ ${esc(trimmed)}</div>`;
  }

  // 8. ulimit / 内部情報
  if (trimmed.includes('ulimit value') || trimmed.startsWith('[~]') || trimmed.startsWith('[>]')) {
    return `<div style="color:#64748b; font-size:11px;">${esc(trimmed)}</div>`;
  }

  // 通常行
  return `<div style="color:#e2e8f0;">${esc(trimmed)}</div>`;
}

function openRustScanModal(targetIp, options = {}) {
  if (!targetIp || targetIp === '-' || targetIp === '0.0.0.0') {
    if (typeof showToast === 'function') {
      showToast('有効なスキャン対象 IPv4 アドレスがありません', false);
    } else {
      alert('有効なスキャン対象 IPv4 アドレスがありません');
    }
    return;
  }

  const vrf = options.vrf || '';
  const title = options.title || targetIp;

  // モーダル要素の初期化
  const elTarget = document.getElementById('rustscanTargetIp');
  if (elTarget) elTarget.textContent = `${targetIp} (${title})`;

  const elVrf = document.getElementById('rustscanVrfBadge');
  if (elVrf) {
    if (vrf) {
      elVrf.textContent = `VRF: ${vrf}`;
      elVrf.style.background = 'rgba(59, 130, 246, 0.2)';
      elVrf.style.color = 'var(--accent-cyan)';
      elVrf.style.borderColor = 'rgba(59, 130, 246, 0.4)';
    } else {
      elVrf.textContent = 'Global';
      elVrf.style.background = 'rgba(245, 158, 11, 0.18)';
      elVrf.style.color = 'var(--accent-amber)';
      elVrf.style.borderColor = 'rgba(245, 158, 11, 0.4)';
    }
  }

  const elCmd = document.getElementById('rustscanCmdText');
  if (elCmd) {
    elCmd.textContent = vrf
      ? `sudo ip vrf exec ${vrf} rustscan --accessible --no-banner --ulimit 5000 -b 2000 -t 1000 -a ${targetIp} -r 1-65535 -- -sV -Pn`
      : `sudo rustscan --accessible --no-banner --ulimit 5000 -b 2000 -t 1000 -a ${targetIp} -r 1-65535 -- -sV -Pn`;
  }

  const elStatus = document.getElementById('rustscanStatusBadge');
  if (elStatus) {
    elStatus.className = 'status-badge badge-active';
    elStatus.innerHTML = '<span class="pulse-dot" style="width:6px;height:6px;background:#f97316;"></span> スキャン中...';
  }

  const elStopBtn = document.getElementById('rustscanStopBtn');
  if (elStopBtn) elStopBtn.style.display = 'inline-block';

  window._rustscanRawOutput = `[RustScan] ターゲット ${targetIp} (1-65535 全ポート) への高速ポートスキャンを開始...\n`;

  const elOutput = document.getElementById('rustscanOutput');
  if (elOutput) {
    elOutput.innerHTML = `<div style="color:var(--accent-cyan); font-weight:600; margin-bottom:6px;">⚡ [RustScan] ターゲット ${targetIp} (全 65535 ポート) への高速走査を開始中...</div>`;
  }

  // 既存の接続があれば切断
  if (window._rustscanSource) {
    window._rustscanSource.close();
    window._rustscanSource = null;
  }

  // モーダル表示
  const modal = document.getElementById('rustscanModal');
  if (modal) modal.style.display = 'flex';

  // SSE 接続開始
  let url = `/api/rustscan/stream?host=${encodeURIComponent(targetIp)}`;
  if (vrf) {
    url += `&vrf=${encodeURIComponent(vrf)}`;
  }

  const es = new EventSource(url);
  window._rustscanSource = es;

  es.onmessage = function (e) {
    const rawLine = e.data || '';
    const cleanLine = stripAnsiCodes(rawLine);
    window._rustscanRawOutput += cleanLine + '\n';

    if (elOutput) {
      elOutput.insertAdjacentHTML('beforeend', formatRustScanLogLine(cleanLine));
      elOutput.scrollTop = elOutput.scrollHeight;
    }
  };

  es.addEventListener('done', function (e) {
    es.close();
    window._rustscanSource = null;
    if (elStatus) {
      elStatus.className = 'status-badge badge-active';
      elStatus.innerHTML = '<span class="pulse-dot" style="width:6px;height:6px;background:#10b981;"></span> スキャン完了';
    }
    if (elStopBtn) elStopBtn.style.display = 'none';

    const endMsg = `\n[✓] 全ポートスキャンが完了しました (終了コード: ${e.data})\n`;
    window._rustscanRawOutput += endMsg;
    if (elOutput) {
      elOutput.insertAdjacentHTML('beforeend', `<div style="color:#10b981; font-weight:700; margin-top:8px; padding-top:6px; border-top:1px solid rgba(16,185,129,0.3);">✔ 全ポートスキャンが正常に完了しました (終了コード: ${e.data})</div>`);
      elOutput.scrollTop = elOutput.scrollHeight;
    }
  });

  es.addEventListener('error', function (e) {
    if (e.data) {
      const errLine = stripAnsiCodes(e.data);
      window._rustscanRawOutput += `\n[!] エラー: ${errLine}\n`;
      if (elOutput) {
        elOutput.insertAdjacentHTML('beforeend', `<div style="color:#ef4444; font-weight:600; margin-top:6px;">❌ エラー: ${errLine}</div>`);
        elOutput.scrollTop = elOutput.scrollHeight;
      }
    }
    es.close();
    window._rustscanSource = null;
    if (elStatus) {
      elStatus.className = 'status-badge badge-unreachable';
      elStatus.innerHTML = 'スキャン終了';
    }
    if (elStopBtn) elStopBtn.style.display = 'none';
  });
}

function stopRustScan() {
  if (window._rustscanSource) {
    window._rustscanSource.close();
    window._rustscanSource = null;
  }
  const elStatus = document.getElementById('rustscanStatusBadge');
  if (elStatus) {
    elStatus.className = 'status-badge badge-unreachable';
    elStatus.innerHTML = '中断';
  }
  const elStopBtn = document.getElementById('rustscanStopBtn');
  if (elStopBtn) elStopBtn.style.display = 'none';

  const stopMsg = '\n[!] ユーザーによってスキャンが中断されました。\n';
  window._rustscanRawOutput += stopMsg;
  const elOutput = document.getElementById('rustscanOutput');
  if (elOutput) {
    elOutput.insertAdjacentHTML('beforeend', `<div style="color:#f59e0b; font-weight:600; margin-top:6px;">⏹ スキャンが手動停止されました。</div>`);
    elOutput.scrollTop = elOutput.scrollHeight;
  }
}

function closeRustScanModal() {
  stopRustScan();
  const modal = document.getElementById('rustscanModal');
  if (modal) modal.style.display = 'none';
}

function copyRustScanOutput() {
  const text = window._rustscanRawOutput || (document.getElementById('rustscanOutput') ? document.getElementById('rustscanOutput').innerText : '');
  if (!text || !text.trim()) {
    if (typeof showToast === 'function') {
      showToast('コピーする内容がありません', false);
    }
    return;
  }
  copyTextToClipboard(text)
    .then(() => {
      if (typeof showToast === 'function') {
        showToast('RustScan スキャン結果をコピーしました', true);
      }
    })
    .catch(err => {
      if (typeof showToast === 'function') {
        showToast('コピーに失敗しました: ' + err, false);
      }
    });
}

