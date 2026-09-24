/* ============================================================
 * Bearded Theme manager for the ISP Emulation Dashboard
 * ============================================================
 * - Applies a palette (window.BEARDED_THEMES) to the entire page by
 *   setting CSS custom properties on :root.
 * - Reacts to <select id="themeSelect"> changes in real time.
 * - Persists the selection in localStorage and syncs across tabs /
 *   popup tool windows via the `storage` event.
 *
 * The palette registry is defined in bearded-themes.js and is fully
 * self-contained (the cloned bearded-theme/ repo is NOT required at
 * runtime). Source / license metadata is carried in the registry.
 * ============================================================*/
window.THEME_MANAGER = (function () {
  'use strict';

  var STORAGE_KEY = 'map_e_dashboard_theme';
  var registry = window.BEARDED_THEMES || { themes: [] };
  var lastAppliedKey = null;

  /* ---------- minimal color utilities (hex) ---------- */

  function hexToRgb(hex) {
    if (!hex || hex.charAt(0) !== '#') return null;
    var h = hex.slice(1);
    if (h.length === 3 || h.length === 4) h = h.replace(/(.)/g, '$1$1');
    if (h.length === 8) h = h.slice(0, 6); // drop alpha channel
    if (h.length !== 6) return null;
    var v = parseInt(h, 16);
    if (Number.isNaN(v)) return null;
    return [(v >> 16) & 0xff, (v >> 8) & 0xff, v & 0xff];
  }

  function rgbToHex(rgb) {
    return '#' + rgb.map(function (c) {
      return Math.max(0, Math.min(255, Math.round(c)))
        .toString(16).padStart(2, '0');
    }).join('');
  }

  /** Mix two hex colors: weight (0..1) toward color b. */
  function mix(a, b, weight) {
    var ra = hexToRgb(a), rb = hexToRgb(b);
    if (!ra || !rb) return a;
    return rgbToHex([
      ra[0] + (rb[0] - ra[0]) * weight,
      ra[1] + (rb[1] - ra[1]) * weight,
      ra[2] + (rb[2] - ra[2]) * weight,
    ]);
  }

  /* ---------- registry helpers ---------- */

  function getTheme(key) {
    if (!key) return null;
    for (var i = 0; i < registry.themes.length; i++) {
      if (registry.themes[i].key === key) return registry.themes[i];
    }
    return null;
  }

  function isDarkTheme(theme) {
    return theme.appearance !== 'light';
  }
/* ---------- CSS variable computation ---------- */

  function computeVars(theme) {
    var c = theme.colors;
    var dark = isDarkTheme(theme);
    var white = '#ffffff';
    var black = '#000000';
    var text = c.text || '#f3f4f6';
    var muted = c.textMuted || text;

    // ライトテーマでは muted が純黒 #000000 になることがあるため、
    // 補助テキスト用にグレーへ調整する（視認性のため）。
    if (!dark && (muted === '#000000' || muted === '#000')) {
      muted = mix(text, white, 0.38);
    }

    // アクセントバッジ用のコントラスト配色
    var accentRgb = hexToRgb(c.accent) || [99, 102, 241];
    var accentRgba = function (a) {
      return 'rgba(' + accentRgb[0] + ',' + accentRgb[1] + ',' + accentRgb[2] + ',' + a + ')';
    };

    // 危険（danger）系ボタン用のコントラスト配色
    var dangerHex = c.danger || '#f43f5e';
    var dangerRgb = hexToRgb(dangerHex) || [244, 63, 94];
    var dangerRgba = function (a) {
      return 'rgba(' + dangerRgb[0] + ',' + dangerRgb[1] + ',' + dangerRgb[2] + ',' + a + ')';
    };
    var dangerText = dark ? mix(dangerHex, white, 0.45) : mix(dangerHex, black, 0.25);

    return {
      '--bg-main': c.bg,
      '--bg-panel': c.bgPanel || c.bg,
      '--bg-card': c.bgCard || c.bg,
      '--bg-card-hover': c.bgHover || c.bgCard || c.bg,
      '--bg-input': mix(c.bg, dark ? black : white, dark ? 0.08 : 0.25),
      '--bg-overlay': dark ? 'rgba(255,255,255,0.03)' : 'rgba(0,0,0,0.06)',
      '--bg-table-header': dark ? 'rgba(15,23,42,0.6)' : 'rgba(148,163,184,0.35)',
      '--bg-banner': dark ? 'rgba(15,23,42,0.5)' : 'rgba(148,163,184,0.25)',
      '--border-color': c.border,
      '--border-strong': dark ? 'rgba(255,255,255,0.22)' : 'rgba(0,0,0,0.25)',
      '--border-faint': dark ? 'rgba(255,255,255,0.04)' : 'rgba(0,0,0,0.10)',
      '--border-focus': c.borderFocus || c.accent,
      '--text-main': text,
      '--text-strong': mix(text, dark ? white : black, dark ? 0.35 : 0.30),
      '--text-soft': text,
      '--text-muted': muted,
      '--text-faint': mix(muted, dark ? black : white, 0.25),
      '--accent-primary': c.accent,
      '--accent-primary-hover': mix(c.accent, dark ? white : black, 0.2),
      '--accent-cyan': c.info || c.accent,
      '--accent-emerald': c.success || '#10b981',
      '--accent-amber': c.warning || '#f59e0b',
      '--accent-rose': c.danger || '#f43f5e',
      '--danger-bg': dangerRgba(0.15),
      '--danger-border': dangerRgba(0.5),
      '--danger-text': dangerText,
      '--danger-bg-hover': dangerRgba(0.3),
      '--badge-accent-bg': dark ? accentRgba(0.22) : accentRgba(0.12),
      '--badge-accent-border': dark ? accentRgba(0.4) : accentRgba(0.5),
      '--badge-accent-text': dark ? mix(c.accent, white, 0.55) : mix(c.accent, black, 0.3),
      '--glow-1': c.glow1 || '99,102,241',
      '--glow-2': c.glow2 || '6,182,212',
    };
  }

  /* ---------- application ---------- */

  function applyTheme(key) {
    var theme = getTheme(key) || (registry.themes && registry.themes[0]);
    if (!theme) return;
    var vars = computeVars(theme);
    var root = document.documentElement;

    for (var prop in vars) {
      if (Object.prototype.hasOwnProperty.call(vars, prop)) {
        root.style.setProperty(prop, vars[prop]);
      }
    }
    lastAppliedKey = theme.key;
    try {
      localStorage.setItem(STORAGE_KEY, theme.key);
    } catch (e) { /* storage may be unavailable (file://, private mode) */ }

    updateControls(theme);
    try {
      window.dispatchEvent(new CustomEvent('theme-changed', {
        detail: { key: theme.key },
      }));
    } catch (e) { /* older browsers */ }
  }
/* ---------- header <select> + info area ---------- */

  function populateSelect() {
    var sel = document.getElementById('themeSelect');
    if (!sel) return;

    var darkGroup = document.createElement('optgroup');
    darkGroup.label = 'ダークテーマ';
    var lightGroup = document.createElement('optgroup');
    lightGroup.label = 'ライトテーマ';
    var themes = registry.themes || [];

    for (var i = 0; i < themes.length; i++) {
      var t = themes[i];
      var opt = document.createElement('option');
      opt.value = t.key;
      opt.textContent = t.key === 'default'
        ? t.name
        : 'Bearded Theme — ' + t.name.replace(/^Bearded Theme\s*/, '');
      (t.appearance === 'light' ? lightGroup : darkGroup).appendChild(opt);
    }
    sel.appendChild(darkGroup);
    sel.appendChild(lightGroup);
    sel.value = lastAppliedKey || 'default';
  }

  function updateControls(theme) {
    var sel = document.getElementById('themeSelect');
    if (sel) {
      var any = false;
      for (var i = 0; i < sel.options.length; i++) {
        if (sel.options[i].value === theme.key) {
          sel.selectedIndex = i;
          any = true;
          break;
        }
      }
      if (!any) sel.selectedIndex = 0;
    }

    var nameEl = document.getElementById('themeName');
    if (nameEl) nameEl.textContent = theme.name || theme.key;

    var srcEl = document.getElementById('themeSourceLink');
    if (srcEl) {
      srcEl.href = registry.sourceUrl || 'https://github.com/BeardedBear/bearded-theme';
      srcEl.textContent = registry.sourceName || 'Bearded Theme';
      srcEl.target = '_blank';
      srcEl.rel = 'noopener';
    }

    var licEl = document.getElementById('themeLicenseLink');
    if (licEl) {
      licEl.href = registry.licenseUrl || '';
      licEl.textContent = (registry.licenseName || 'GPL-3.0') + ' ライセンス';
      licEl.target = '_blank';
      licEl.rel = 'noopener';
    }

    // Small palette preview swatches
    var sw = document.getElementById('themeSwatches');
    if (sw) {
      var c = theme.colors;
      var palette = [c.bg, c.bgCard || c.bg, c.accent, c.success, c.warning, c.danger];
      var html = '';
      for (var j = 0; j < palette.length; j++) {
        html += '<span class="theme-swatch" style="background:' + palette[j] + ';"></span>';
      }
      sw.innerHTML = html;
    }
  }

  /* ---------- exports / events ---------- */

  function onThemeSelectChanged() {
    var sel = document.getElementById('themeSelect');
    if (sel) applyTheme(sel.value);
  }

  function init() {
    if (!registry.themes || !registry.themes.length) return;
    populateSelect();
    var saved = null;
    try { saved = localStorage.getItem(STORAGE_KEY); } catch (e) { /* ignore */ }
    applyTheme(saved || 'default');

    // 初期適用完了後にのみトランジションを有効化
    // （ページを開いた瞬間のチラつき・ジワッとした変化を防ぐため）
    document.documentElement.classList.add('theme-transition-ready');

    window.addEventListener('storage', function (e) {
      if (e.key !== STORAGE_KEY) return;
      if (!e.newValue || e.newValue === lastAppliedKey) return;
      var sel = document.getElementById('themeSelect');
      if (sel && sel.value === e.newValue) return;
      applyTheme(e.newValue);
    });
  }

  /* Attach the global change handler used by inline onchange=... */
  window.onThemeSelectChanged = onThemeSelectChanged;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  return {
    applyTheme: applyTheme,
    init: init,
    getRegistry: function () { return registry; },
  };
})();