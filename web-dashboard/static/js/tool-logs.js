// =============================================================================
// tool-logs.js — サービスログ閲覧画面 (Client-side Log Cache & Live Viewer)
// =============================================================================

const ALL_LOG_TYPES = ['pppoe', 'mape', 'kea', 'bind', 'radvd', 'chrony', 'messages'];
const BADGE_MAP = {
  pppoe: 'PPPoE',
  mape: 'MAP-E',
  kea: 'Kea',
  bind: 'DNS',
  radvd: 'RADVD',
  chrony: 'Chrony',
  messages: 'System'
};

// 種別ごとのログキャッシュ保持ストア
const logCache = {
  pppoe: [],
  mape: [],
  kea: [],
  bind: [],
  radvd: [],
  chrony: [],
  messages: []
};
let sourcesCache = {};
let isFetching = false;
let displayedEntries = [];
let isInitialLoad = true;

function getSelectedTypes() {
  const cbs = document.querySelectorAll('input[name="logType"]:checked');
  return Array.from(cbs).map(cb => cb.value);
}

function updatePillStyles() {
  document.querySelectorAll('input[name="logType"]').forEach(cb => {
    const pill = document.getElementById('pill_' + cb.value);
    if (pill) {
      if (cb.checked) {
        pill.classList.add('active-' + cb.value);
      } else {
        pill.classList.remove('active-' + cb.value);
      }
    }
  });
}

// 読み込み中インジケータ（砂時計 & 点滅）の制御
function setLoadingState(loading) {
  isFetching = loading;
  const refreshIcon = document.getElementById('refreshIcon');
  const titleIcon = document.getElementById('titleIcon');
  const loadingIndicator = document.getElementById('loadingIndicator');

  if (loading) {
    if (refreshIcon) {
      refreshIcon.textContent = '⏳';
      refreshIcon.className = 'loading-hourglass';
    }
    if (titleIcon) {
      titleIcon.textContent = '⏳';
      titleIcon.className = 'loading-hourglass';
    }
    if (loadingIndicator) {
      loadingIndicator.style.display = 'inline-flex';
    }
  } else {
    if (refreshIcon) {
      refreshIcon.textContent = '🔄';
      refreshIcon.className = '';
    }
    if (titleIcon) {
      titleIcon.textContent = '📜';
      titleIcon.className = '';
    }
    if (loadingIndicator) {
      loadingIndicator.style.display = 'none';
    }
  }
}

// チェックボックス変更時のハンドラ (キャッシュから即時レンダリング & バックグラウンド最新化)
function handleTypeChange() {
  updatePillStyles();
  saveSettings();
  renderFromCache();
  fetchLogs(false);
}

function selectAllTypes(checkAll) {
  document.querySelectorAll('input[name="logType"]').forEach(cb => {
    cb.checked = checkAll;
  });
  updatePillStyles();
  saveSettings();
  renderFromCache();
  fetchLogs(false);
}

function handleLinesChange() {
  renderFromCache();
  fetchLogs(true);
}

function handleLevelChange() {
  saveSettings();
  fetchLogs(true, true);
}

function manualRefresh() {
  isInitialLoad = true;
  scrollToBottomNow();
  fetchLogs(true, true);
}

function saveSettings() {
  const selected = getSelectedTypes();
  localStorage.setItem('map_e_log_types', JSON.stringify(selected));
  const lvl = document.getElementById('levelSelect').value;
  localStorage.setItem('map_e_log_level', lvl);
}

function loadSettings() {
  try {
    const saved = localStorage.getItem('map_e_log_types');
    if (saved) {
      const list = JSON.parse(saved);
      if (Array.isArray(list) && list.length > 0) {
        document.querySelectorAll('input[name="logType"]').forEach(cb => {
          cb.checked = list.includes(cb.value);
        });
      }
    }
    const savedLvl = localStorage.getItem('map_e_log_level');
    if (savedLvl && document.getElementById('levelSelect')) {
      document.getElementById('levelSelect').value = savedLvl;
    }
  } catch (e) {}
  updatePillStyles();
}

// =========================================================================
// キャッシュからの高速描画 (ブラウザ完結・ノーウェイト)
// =========================================================================
function renderFromCache() {
  const selectedTypes = getSelectedTypes();
  const linesLimit = parseInt(document.getElementById('linesSelect').value, 10) || 200;
  const box = document.getElementById('logBox');
  const sourcesList = document.getElementById('sourcesList');

  if (selectedTypes.length === 0) {
    box.innerHTML = '<div class="empty-state">ログ種別が選択されていません。上部のチェックボックスから表示したいログを選択してください。</div>';
    if (sourcesList) sourcesList.innerHTML = '<span>選択なし</span>';
    document.getElementById('statusCounter').textContent = '0 件';
    displayedEntries = [];
    return;
  }

  // 選択された種別のログをマージ
  const merged = [];
  selectedTypes.forEach(t => {
    const list = logCache[t] || [];
    for (let i = 0; i < list.length; i++) {
      merged.push(list[i]);
    }
  });

  // タイムスタンプ順でソート
  merged.sort((a, b) => (a.ts || 0) - (b.ts || 0));

  // 件数制限
  displayedEntries = merged.slice(-linesLimit);

  // ソース一覧の更新
  const sourceItems = selectedTypes.map(k => {
    const desc = sourcesCache[k] || '-';
    return `<span class="source-item"><strong style="color:var(--text-soft, #cbd5e1);">[${BADGE_MAP[k] || k}]</strong> ${escapeHtml(desc)}</span>`;
  });
  if (sourcesList) sourcesList.innerHTML = sourceItems.length > 0 ? sourceItems.join(' ') : '<span>-</span>';

  filterLines();
}

// =========================================================================
// サーバーからのフェッチ (全種別キャッシュをスマート更新)
// =========================================================================
async function fetchLogs(showLoading = true, fetchAll = false) {
  if (isFetching && !fetchAll) return;
  if (showLoading) setLoadingState(true);

  const lines = document.getElementById('linesSelect').value;
  const level = document.getElementById('levelSelect') ? document.getElementById('levelSelect').value : 'INFO';
  const selectedTypes = getSelectedTypes();

  // 初回または手動更新時は全種別を取得してキャッシュを完全充填
  const typesToFetch = fetchAll ? ALL_LOG_TYPES : (selectedTypes.length > 0 ? selectedTypes : ALL_LOG_TYPES);

  try {
    const typesStr = typesToFetch.join(',');
    const res = await fetch(`/api/logs?types=${encodeURIComponent(typesStr)}&lines=${lines}&level=${encodeURIComponent(level)}`);
    const data = await res.json();

    // 取得したソース情報をキャッシュに格納
    if (data.sources) {
      sourcesCache = Object.assign({}, sourcesCache, data.sources);
    }

    // 取得したエントリを種別ごとのキャッシュに格納
    const entries = data.entries || [];
    typesToFetch.forEach(t => {
      logCache[t] = [];
    });
    entries.forEach(e => {
      const t = e.type || 'pppoe';
      if (logCache[t]) {
        logCache[t].push(e);
      }
    });

    // 画面を再描画
    renderFromCache();
  } catch (e) {
    console.error('Fetch logs error:', e);
    if (displayedEntries.length === 0) {
      document.getElementById('logBox').innerHTML = `<div class="empty-state" style="color:var(--accent-rose, #ef4444);">取得エラー: ${escapeHtml(e.message)}</div>`;
    }
  } finally {
    if (showLoading) setLoadingState(false);
  }
}

// =========================================================================
// スクロール判定 & 自動更新制御
// =========================================================================

// ログボックスが最下部（またはその近傍）を表示しているかどうか判定
function isScrolledToBottom(threshold = 50) {
  const box = document.getElementById('logBox');
  if (!box || box.clientHeight === 0) return true;
  const distanceFromBottom = box.scrollHeight - box.scrollTop - box.clientHeight;
  return distanceFromBottom <= threshold;
}

// 最新ログ（最下部）へ即座かつ確実にスクロール
function scrollToBottomNow() {
  const box = document.getElementById('logBox');
  if (!box) return;
  box.scrollTop = box.scrollHeight;
  requestAnimationFrame(() => {
    box.scrollTop = box.scrollHeight;
    setTimeout(() => {
      box.scrollTop = box.scrollHeight;
      updateScrollUI();
    }, 50);
  });
}

// スクロール状態に応じたUI（上部の一時停止バッジ）の更新
function updateScrollUI() {
  const atBottom = isScrolledToBottom(50);
  const pausedTag = document.getElementById('scrollPausedTag');
  const autoRefresh = document.getElementById('autoRefreshCheck').checked;

  if (!atBottom && autoRefresh) {
    if (pausedTag) pausedTag.style.display = 'inline-flex';
  } else {
    if (pausedTag) pausedTag.style.display = 'none';
  }
}

// ボタンクリックで最下部に移動し、自動更新を即座に再開
function scrollToBottomAndResume() {
  scrollToBottomNow();
  fetchLogs(false, false);
}

// =========================================================================
// フィルタリングと HTML レンダリング
// =========================================================================
function filterLines() {
  const box = document.getElementById('logBox');
  const filter = document.getElementById('filterInput').value.toLowerCase().trim();
  const counter = document.getElementById('statusCounter');

  // 再描画前のスクロール位置と「最下部にいたか」を記録
  const wasAtBottom = isScrolledToBottom(50);
  const prevScrollTop = box.scrollTop;

  if (displayedEntries.length === 0) {
    box.innerHTML = '<div class="empty-state">表示可能なログデータがありません。</div>';
    counter.textContent = '0 件';
    updateScrollUI();
    return;
  }

  let displayedCount = 0;
  const htmlArray = [];

  for (let i = 0; i < displayedEntries.length; i++) {
    const entry = displayedEntries[i];
    const type = entry.type || 'pppoe';
    const label = entry.label || BADGE_MAP[type] || type.toUpperCase();
    const time = entry.time || '';
    const text = entry.text || '';

    const fullStr = `${label} ${time} ${text}`.toLowerCase();
    const isMatch = filter ? fullStr.includes(filter) : true;

    if (!isMatch) continue;
    displayedCount++;

    let renderedText = escapeHtml(text);
    if (filter) {
      const re = new RegExp(escapeRegex(filter), 'gi');
      renderedText = renderedText.replace(re, m => `<span class="mark-highlight">${m}</span>`);
    }

    const lvl = entry.level || 'INFO';
    htmlArray.push(`
      <div class="log-line ${filter ? 'highlight' : ''}">
        <span class="badge badge-${escapeHtml(type)}">[${escapeHtml(label)}]</span>
        <span class="lvl-badge lvl-${escapeHtml(lvl)}">${escapeHtml(lvl)}</span>
        <span class="log-time">${escapeHtml(time)}</span>
        <span class="log-text">${renderedText}</span>
      </div>
    `);
  }

  counter.textContent = `${displayedCount} 件表示`;

  if (displayedCount === 0) {
    box.innerHTML = `<div class="empty-state">「${escapeHtml(filter)}」に一致するログは見つかりませんでした。</div>`;
  } else {
    box.innerHTML = htmlArray.join('');
  }

  // 初回表示時 (isInitialLoad) は必ず最新ログ（最下部）を表示。
  // 以降も最下部を表示中は常に最新行へ自動追従スクロールし、上方閲覧時は位置を維持。
  if (isInitialLoad || wasAtBottom) {
    scrollToBottomNow();
    isInitialLoad = false;
  } else {
    box.scrollTop = prevScrollTop;
  }

  updateScrollUI();
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function escapeRegex(string) {
  return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// =========================================================================
// イベントリスナーと起動時初期化
// =========================================================================
const logBoxEl = document.getElementById('logBox');
if (logBoxEl) {
  logBoxEl.addEventListener('scroll', updateScrollUI);
}
const autoRefreshCheckEl = document.getElementById('autoRefreshCheck');
if (autoRefreshCheckEl) {
  autoRefreshCheckEl.addEventListener('change', updateScrollUI);
}

loadSettings();
fetchLogs(true, false).then(() => {
  scrollToBottomNow();
  // 初回表示完了後、残りの種別をバックグラウンドでプリフェッチ
  setTimeout(() => {
    if (!isFetching) {
      fetchLogs(false, true);
    }
  }, 1500);
});

window.addEventListener('load', () => {
  scrollToBottomNow();
});

if (document.fonts && document.fonts.ready) {
  document.fonts.ready.then(() => {
    if (isInitialLoad || isScrolledToBottom(50)) {
      scrollToBottomNow();
    }
  });
}

// 定期バックグラウンド自動更新
setInterval(() => {
  const autoRefresh = document.getElementById('autoRefreshCheck').checked;
  if (autoRefresh && !isFetching && isScrolledToBottom(50)) {
    fetchLogs(false, false);
  }
}, 4000);
