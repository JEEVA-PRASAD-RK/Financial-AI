/**
 * index_fix.js
 * Add <script src="index_fix.js"></script> before </body> in index.html
 *
 * Fixes:
 * 1. Hides "Login / Register" button in top nav for guests
 *    (the guest banner already provides login access)
 * 2. Keeps auth modal accessible via banner button
 */
(function() {
  function applyIndexFixes() {
    const u = (() => { try { return JSON.parse(sessionStorage.getItem('ft_user')||'null'); } catch(_) { return null; } })();

    const loginBtn = document.getElementById('login-btn');

    if (!u) {
      // Guest: hide the top-nav login button (banner handles it)
      if (loginBtn) loginBtn.style.display = 'none';

      // Make sure avatar dropdown is also hidden
      const uab = document.getElementById('user-avatar-btn');
      if (uab) uab.style.display = 'none';
    }
    // If logged in, the existing setLoggedIn() function handles showing/hiding correctly
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', applyIndexFixes);
  } else {
    applyIndexFixes();
  }
})();