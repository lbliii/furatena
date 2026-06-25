/**
 * Furatena appearance menu — native popover theme picker.
 *
 * Uses packaged chirp-theme `.theme-dropdown__menu--popover` styles and
 * persists to `chirpui-theme` (same key as shell pre-paint init).
 */
(function () {
  'use strict';

  var THEME_KEY = 'chirpui-theme';
  var THEMES = ['system', 'light', 'dark', 'oled'];
  var boundMenus = new WeakSet();

  function safeGet(key) {
    try {
      return localStorage.getItem(key);
    } catch (_err) {
      return null;
    }
  }

  function safeSet(key, value) {
    try {
      localStorage.setItem(key, value);
      return true;
    } catch (_err2) {
      return false;
    }
  }

  function getStoredTheme() {
    var stored = safeGet(THEME_KEY);
    return THEMES.indexOf(stored) >= 0 ? stored : 'system';
  }

  function setTheme(theme) {
    if (THEMES.indexOf(theme) < 0) {
      return;
    }
    document.documentElement.setAttribute('data-theme', theme);
    safeSet(THEME_KEY, theme);
    document.querySelectorAll('.theme-dropdown__menu--popover[popover]').forEach(updatePopoverActiveStates);
  }

  function positionPopover(popover, trigger) {
    if (!trigger) {
      return;
    }
    var triggerRect = trigger.getBoundingClientRect();
    var popoverRect = popover.getBoundingClientRect();
    var viewportWidth = window.innerWidth;
    var viewportHeight = window.innerHeight;
    var gap = 8;
    var inCatalogRail = trigger.closest('.chirp-theme-doc-catalog-rail__theme');

    if (inCatalogRail) {
      var railTop = triggerRect.top + (triggerRect.height - popoverRect.height) / 2;
      var railLeft = triggerRect.right + gap;
      railTop = Math.max(gap, Math.min(railTop, viewportHeight - popoverRect.height - gap));
      if (railLeft + popoverRect.width > viewportWidth - gap) {
        railLeft = triggerRect.left - popoverRect.width - gap;
      }
      popover.style.top = railTop + 'px';
      popover.style.left = railLeft + 'px';
      popover.style.right = 'auto';
      return;
    }

    var top = triggerRect.bottom + gap;
    var right = viewportWidth - triggerRect.right;
    var left = viewportWidth - right - popoverRect.width;
    if (left < gap) {
      right = viewportWidth - popoverRect.width - gap;
    }
    popover.style.top = top + 'px';
    popover.style.right = right + 'px';
    popover.style.left = 'auto';
  }

  function updatePopoverActiveStates(menu) {
    var current = getStoredTheme();
    menu.querySelectorAll('[data-appearance]').forEach(function (btn) {
      btn.classList.toggle('active', btn.getAttribute('data-appearance') === current);
    });
  }

  function bindMenu(menu) {
    if (boundMenus.has(menu)) {
      return;
    }
    boundMenus.add(menu);

    var triggerId = menu.id;
    var triggerBtn = triggerId
      ? document.querySelector('[popovertarget="' + triggerId + '"]')
      : menu.previousElementSibling;

    menu.addEventListener('click', function (event) {
      var btn = event.target.closest('[data-appearance]');
      if (!btn) {
        return;
      }
      setTheme(btn.getAttribute('data-appearance'));
      if (typeof menu.hidePopover === 'function') {
        menu.hidePopover();
      }
    });

    menu.addEventListener('toggle', function (event) {
      if (event.newState === 'open') {
        positionPopover(menu, triggerBtn);
        updatePopoverActiveStates(menu);
      }
    });

    updatePopoverActiveStates(menu);
  }

  function enhance(root) {
    root = root || document;
    root.querySelectorAll('.theme-dropdown__menu--popover[popover]').forEach(bindMenu);
  }

  window.FuraDocsTheme = {
    enhance: enhance,
    setTheme: setTheme,
    getTheme: getStoredTheme,
  };
  if (window.FuraDocs && window.FuraDocs.enhance && window.FuraDocs.enhance.register) {
    window.FuraDocs.enhance.register('theme-menu', { enhance: enhance });
    enhance(document.getElementById('page-root') || document);
  }
})();
