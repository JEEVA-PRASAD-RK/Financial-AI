/**
 * finace-nav.js — Unified navigation + theme helper for all Finace pages
 *
 * Features:
 * 1. Active nav item highlighted correctly per page
 * 2. Avatar/name → profile.html click
 * 3. Guest banner injection
 * 4. Global dark/light theme toggle — persists via localStorage (ft_theme)
 *    Toggle button is auto-injected into every sidebar — NO page changes needed
 */
(function () {
  'use strict';

  const PAGE_MAP = {
    'index.html':       'nav-ai',
    '':                 'nav-ai',
    'dashboard.html':   'nav-dash',
    'investment.html':  'nav-invest',
    'suggestions.html': 'nav-suggestions',
    'networth.html':    'nav-assets',
    'profile.html':     null,
  };

  const NO_BANNER_PAGES = new Set(['index.html', '', 'profile.html']);

  function getUser() {
    try {
      return JSON.parse(localStorage.getItem('ft_user') || sessionStorage.getItem('ft_user') || 'null');
    } catch(_) { return null; }
  }

  function getCurrentPage() {
    return location.pathname.split('/').pop() || 'index.html';
  }

  /* ══════════════════════════════════════════════════════════
     THEME SYSTEM
     ft_theme = 'dark' (default) | 'light'
  ══════════════════════════════════════════════════════════ */
  function getTheme() {
    return localStorage.getItem('ft_theme') || 'dark';
  }

  function applyTheme(theme) {
    if (theme === 'light') {
      document.documentElement.setAttribute('data-theme', 'light');
    } else {
      document.documentElement.removeAttribute('data-theme');
    }
    // Update all toggle buttons on the page (in case there are multiple)
    document.querySelectorAll('.theme-toggle-label').forEach(el => {
      el.textContent = theme === 'light' ? '☀️ Light Mode' : '🌙 Dark Mode';
    });
  }

  function toggleTheme() {
    const current = getTheme();
    const next = current === 'dark' ? 'light' : 'dark';
    localStorage.setItem('ft_theme', next);
    applyTheme(next);
  }

  /** Inject the theme toggle button into the sidebar user section */
  function injectThemeToggle() {
    // Only inject once
    if (document.getElementById('finace-theme-toggle')) return;

    // Find the sidebar user section or the sidebar itself as fallback
    const sbUser = document.querySelector('.sb-user, .sidebar-user, [class*="sb-user"]');
    const sidebar = document.querySelector('.sidebar');
    const mount   = sbUser || sidebar;
    if (!mount) return;

    const theme = getTheme();
    const btn = document.createElement('button');
    btn.id = 'finace-theme-toggle';
    btn.className = 'theme-toggle-btn';
    btn.setAttribute('title', 'Toggle light / dark theme');
    btn.innerHTML = `
      <span class="theme-toggle-label">${theme === 'light' ? '☀️ Light Mode' : '🌙 Dark Mode'}</span>
      <span class="theme-toggle-pill"></span>
    `;
    btn.addEventListener('click', toggleTheme);

    // Insert before the user section (or at top of sidebar user block)
    if (sbUser) {
      sbUser.parentNode.insertBefore(btn, sbUser);
    } else {
      sidebar.appendChild(btn);
    }
  }

  /* ── Navigation functions ──────────────────────────────── */
  window.goToAIAdvisor   = () => { window.location.href = 'index.html'; };
  window.goToDashboard   = () => { window.location.href = 'dashboard.html'; };
  window.goToInvestment  = () => { window.location.href = 'investment.html'; };
  window.goToSuggestions = () => { window.location.href = 'suggestions.html'; };
  window.goToAssets      = () => { window.location.href = 'networth.html'; };
  window.goToProfile     = () => { window.location.href = 'profile.html'; };
  window.showPage = (p) => { window.location.href = p === 'ai' ? 'index.html' : p + '.html'; };
  window.setNav = function(el) {
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    if (el) el.classList.add('active');
  };

  /* ── Highlight correct nav item ────────────────────────── */
  function syncActiveNav() {
    const page = getCurrentPage();
    const id = PAGE_MAP[page];
    if (!id) return;
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    const el = document.getElementById(id);
    if (el) el.classList.add('active');
  }

  /* ── Wire avatar → profile ─────────────────────────────── */
  function wireAvatarLinks() {
    ['sb-av','sbAv','sidebar-av'].forEach(id => {
      const el = document.getElementById(id);
      if (!el || el.dataset.navWired) return;
      el.dataset.navWired = '1';
      el.style.cursor = 'pointer';
      el.title = 'My Profile';
      el.addEventListener('click', e => { e.stopPropagation(); location.href = 'profile.html'; });
    });
    ['sb-uname','sbName','sidebar-name'].forEach(id => {
      const el = document.getElementById(id);
      if (!el || el.dataset.navWired) return;
      const txt = el.textContent.trim();
      if (!txt || ['Guest','—','?','Loading…',''].includes(txt)) return;
      el.dataset.navWired = '1';
      el.style.cursor = 'pointer';
      el.addEventListener('click', () => { location.href = 'profile.html'; });
    });
  }

  /* ── Guest banner ──────────────────────────────────────── */
  function injectGuestBanner() {
    const page = getCurrentPage();
    if (NO_BANNER_PAGES.has(page)) return;
    if (getUser()) return;
    if (document.getElementById('guest-banner')) return;

    const mount = document.querySelector('.main') || document.body;
    const b = document.createElement('div');
    b.id = 'guest-banner';
    b.style.cssText = 'position:sticky;top:0;z-index:9998;background:linear-gradient(90deg,#4f46e5,#7c3aed);color:#fff;padding:9px 18px;display:flex;align-items:center;justify-content:space-between;font-family:Outfit,Inter,sans-serif;font-size:.82rem;font-weight:600;box-shadow:0 2px 12px rgba(79,70,229,.35);flex-wrap:wrap;gap:8px;';
    b.innerHTML = '<span>👋 Browsing as <strong>Guest</strong> — log in to save your data.</span><div style="display:flex;align-items:center;gap:10px;flex-shrink:0"><a href="index.html" style="padding:6px 16px;border-radius:7px;border:1.5px solid rgba(255,255,255,.5);background:rgba(255,255,255,.15);color:#fff;font-family:Outfit,sans-serif;font-size:.78rem;font-weight:700;text-decoration:none;white-space:nowrap">Login →</a><button onclick="this.closest(\'#guest-banner\').remove()" style="background:none;border:none;color:rgba(255,255,255,.6);cursor:pointer;font-size:1.1rem;line-height:1;padding:0 4px">✕</button></div>';
    mount.prepend(b);
  }

  function removeBannerIfLoggedIn() {
    if (getUser()) {
      const b = document.getElementById('guest-banner');
      if (b) b.remove();
    }
  }

  /* ── Update sidebar user info ──────────────────────────── */
  function syncSidebarUser() {
    const u = getUser();
    const initials = u ? (u.initials || (u.fullName||u.name||'U')[0].toUpperCase()) : '?';
    const name     = u ? (u.fullName || u.name || 'User') : 'Guest';
    const email    = u ? (u.email || '—') : 'Not logged in';

    ['sb-av','sbAv','sidebar-av'].forEach(id => {
      const el = document.getElementById(id);
      if (el && !el.querySelector('img')) el.textContent = initials;
    });
    ['sb-uname','sbName','sidebar-name'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.textContent = name;
    });
    ['sb-uemail','sbEmail','sidebar-email'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.textContent = email;
    });
  }

  /* ── INIT ───────────────────────────────────────────────── */
  function init() {
    // Apply saved theme FIRST — before anything renders visibly
    applyTheme(getTheme());

    syncActiveNav();
    syncSidebarUser();
    wireAvatarLinks();
    removeBannerIfLoggedIn();
    injectGuestBanner();
    injectThemeToggle();
  }

  // Apply theme immediately (before DOMContentLoaded) to avoid flash
  applyTheme(getTheme());

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
    setTimeout(wireAvatarLinks, 500);
  }
})();