/* Clerk auth gate for Listing Scraper.
   Loads @clerk/clerk-js from the publishable-key derived Frontend API host.
   - If user not signed in -> show centered Clerk SignIn component
   - If user signed in    -> show the existing app + a Sign Out button
   Exposes:
     window.__appAuth = {
       ready: Promise<void>,   // resolves once auth state has been determined
       getUser: () => userOrNull,
       getToken: async () => string|null,  // for backend bearer auth (future)
       onChange: (cb) => removeFn,
     }
   The existing /app.js waits for ready before bootstrapping.
*/
(function () {
  var cfg = window.__APP_CONFIG__ || {};
  var pk = (cfg.clerkPublishableKey || '').trim();
  var listeners = [];
  var clerkInstance = null;
  var resolveReady;
  var ready = new Promise(function (r) { resolveReady = r; });

  function notify() {
    var u = clerkInstance ? clerkInstance.user : null;
    listeners.forEach(function (cb) {
      try { cb(u); } catch (e) { /* swallow */ }
    });
  }

  function showFatal(msg) {
    var gate = document.getElementById('auth-gate');
    var host = document.getElementById('signin-host');
    if (gate) gate.hidden = false;
    if (host) {
      host.innerHTML = '';
      var box = document.createElement('div');
      box.className = 'auth-error';
      box.textContent = msg;
      host.appendChild(box);
    }
    document.getElementById('app-shell').hidden = true;
  }

  function deriveFrontendApiHost(publishableKey) {
    // pk_test_<base64('host$')> or pk_live_<base64('host$')>
    try {
      var parts = publishableKey.split('_');
      if (parts.length < 3) return null;
      var encoded = parts.slice(2).join('_');
      // Pad base64 if needed
      var pad = encoded.length % 4;
      if (pad) encoded = encoded + new Array(5 - pad).join('=');
      var decoded = atob(encoded);
      return decoded.replace(/\$+$/, '');
    } catch (e) {
      return null;
    }
  }

  function loadClerkScript() {
    return new Promise(function (resolve, reject) {
      if (window.Clerk) return resolve();
      var host = deriveFrontendApiHost(pk);
      if (!host) return reject(new Error('Invalid Clerk publishable key'));
      var s = document.createElement('script');
      s.src = 'https://' + host + '/npm/@clerk/clerk-js@5/dist/clerk.browser.js';
      s.async = true;
      s.crossOrigin = 'anonymous';
      s.setAttribute('data-clerk-publishable-key', pk);
      s.onload = function () { resolve(); };
      s.onerror = function () { reject(new Error('Failed to load Clerk SDK from ' + s.src)); };
      document.head.appendChild(s);
    });
  }

  function showSignIn() {
    document.getElementById('auth-gate').hidden = false;
    document.getElementById('app-shell').hidden = true;
    var host = document.getElementById('signin-host');
    if (!host) return;
    host.innerHTML = '';
    if (clerkInstance && typeof clerkInstance.mountSignIn === 'function') {
      try {
        clerkInstance.mountSignIn(host, {
          appearance: {
            elements: {
              rootBox: { width: '100%' },
              card: {
                boxShadow: 'none',
                border: 'none',
                background: 'transparent',
                padding: '0'
              }
            }
          }
        });
      } catch (e) {
        host.textContent = 'Could not display sign-in: ' + (e && e.message || e);
      }
    } else {
      host.textContent = 'Sign-in component not available.';
    }
  }

  function unmountSignIn() {
    var host = document.getElementById('signin-host');
    if (host && clerkInstance && typeof clerkInstance.unmountSignIn === 'function') {
      try { clerkInstance.unmountSignIn(host); } catch (e) { /* ignore */ }
    }
  }

  function showApp() {
    unmountSignIn();
    document.getElementById('auth-gate').hidden = true;
    document.getElementById('app-shell').hidden = false;
    renderUserArea();
  }

  function renderUserArea() {
    var area = document.getElementById('user-area');
    if (!area || !clerkInstance) return;
    while (area.firstChild) area.removeChild(area.firstChild);
    var user = clerkInstance.user;
    if (!user) return;

    var label = document.createElement('span');
    label.className = 'user-label';
    var displayName = '';
    if (user.firstName) displayName = user.firstName;
    if (!displayName && user.primaryEmailAddress && user.primaryEmailAddress.emailAddress) {
      displayName = user.primaryEmailAddress.emailAddress;
    }
    if (!displayName) displayName = user.id;
    label.textContent = displayName;
    area.appendChild(label);

    var btn = document.createElement('button');
    btn.className = 'btn btn-secondary btn-sm signout-btn';
    btn.textContent = 'Sign Out';
    btn.setAttribute('data-testid', 'btn-sign-out');
    btn.addEventListener('click', function () {
      btn.disabled = true;
      btn.textContent = 'Signing out...';
      Promise.resolve(clerkInstance.signOut()).then(function () {
        // Auth listener will react and show sign-in
      }).catch(function () {
        btn.disabled = false;
        btn.textContent = 'Sign Out';
      });
    });
    area.appendChild(btn);
  }

  function applyAuthState() {
    if (!clerkInstance) return;
    if (clerkInstance.user) {
      showApp();
    } else {
      showSignIn();
    }
    notify();
  }

  // Public API
  window.__appAuth = {
    ready: ready,
    getUser: function () { return clerkInstance ? clerkInstance.user : null; },
    getToken: function () {
      if (!clerkInstance || !clerkInstance.session) return Promise.resolve(null);
      try { return Promise.resolve(clerkInstance.session.getToken()); } catch (e) { return Promise.resolve(null); }
    },
    onChange: function (cb) {
      listeners.push(cb);
      return function () {
        var i = listeners.indexOf(cb);
        if (i >= 0) listeners.splice(i, 1);
      };
    }
  };

  function bootstrap() {
    if (!pk) {
      showFatal('Clerk publishable key not configured. Set CLERK_PUBLISHABLE_KEY in /app/frontend/.env.');
      resolveReady();
      return;
    }
    loadClerkScript().then(function () {
      // Some versions auto-construct window.Clerk from the data-attribute on the
      // script tag; others need explicit `new Clerk(pk)`.
      if (window.Clerk && typeof window.Clerk.load === 'function') {
        clerkInstance = window.Clerk;
      } else if (typeof window.Clerk === 'function') {
        clerkInstance = new window.Clerk(pk);
      } else {
        throw new Error('Clerk global not available after script load');
      }
      return clerkInstance.load();
    }).then(function () {
      // React to auth state changes
      if (typeof clerkInstance.addListener === 'function') {
        clerkInstance.addListener(function () { applyAuthState(); });
      }
      applyAuthState();
      resolveReady();
    }).catch(function (e) {
      console.error('Clerk init failed:', e);
      showFatal('Sign-in is unavailable right now. ' + (e && e.message ? '(' + e.message + ')' : ''));
      resolveReady();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootstrap);
  } else {
    bootstrap();
  }
})();
