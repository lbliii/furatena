/**
 * Furatena — catalog sidebar disclosure enhancement.
 *
 * Lifecycle: initialized only via FuraDocs.enhance (docs-enhance.js) on
 * htmx:afterSettle. Do not self-bootstrap — #page-root is ephemeral.
 *
 * Progressive enhancement for partials/docs_sidebar.html.
 */

(function () {
  'use strict';

  var ROOT_SELECTOR = '[data-chirp-theme-doc-nav="catalog"]';
  var SIDEBAR_LABEL = 'Documentation sections';
  var SECTION_SELECTOR = '.chirp-theme-docs-nav__section--has-toggle';
  var TOGGLE_SELECTOR = '.chirp-theme-docs-nav__toggle';
  var ACTIVE_SELECTOR =
    '.chirp-theme-docs-nav__summary-link--active,' +
    '.chirp-theme-docs-nav [aria-current="page"],' +
    '.chirp-theme-docs-nav__link.chirpui-sidebar__link--active';

  function resolveRoot(root) {
    if (root && root.nodeType === 1) {
      return root;
    }
    return document.getElementById('page-root') || document;
  }

  function labelSidebarLandmark(root) {
    var nav = root.querySelector('.chirp-theme-docs-nav.chirpui-sidebar') ||
              root.querySelector('[data-chirp-theme-doc-nav="sections"] .chirpui-sidebar');
    if (nav && !nav.getAttribute('aria-label')) {
      nav.setAttribute('aria-label', SIDEBAR_LABEL);
    }
  }

  function panelFor(toggle) {
    var id = toggle.getAttribute('aria-controls');
    var panel = id ? document.getElementById(id) : null;
    if (panel) return panel;
    var section = toggle.closest(SECTION_SELECTOR);
    return section
      ? section.querySelector(':scope > .chirp-theme-docs-nav__section-links')
      : null;
  }

  function setExpanded(toggle, expanded) {
    var panel = panelFor(toggle);
    toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    if (!panel) return;
    if (expanded) {
      panel.hidden = false;
      panel.style.removeProperty('display');
    } else {
      panel.hidden = true;
      panel.style.display = 'none';
    }
  }

  function initToggles(root) {
    var toggles = root.querySelectorAll(TOGGLE_SELECTOR);
    toggles.forEach(function (toggle) {
      var expanded = toggle.getAttribute('aria-expanded') === 'true';
      setExpanded(toggle, expanded);

      if (toggle.dataset.docsNavBound === '1') return;
      toggle.dataset.docsNavBound = '1';
      toggle.addEventListener('click', function () {
        var isOpen = toggle.getAttribute('aria-expanded') === 'true';
        setExpanded(toggle, !isOpen);
      });
    });
  }

  function openActiveTrail(root) {
    var active = root.querySelector(ACTIVE_SELECTOR);
    if (!active) return;

    var node = active.parentElement;
    while (node && !(node.matches && node.matches(ROOT_SELECTOR))) {
      if (node.matches && node.matches(SECTION_SELECTOR)) {
        var toggle = node.querySelector(':scope > .chirp-theme-docs-nav__section-header > ' + TOGGLE_SELECTOR);
        if (toggle && toggle.getAttribute('aria-expanded') !== 'true') {
          setExpanded(toggle, true);
        }
      }
      node = node.parentElement;
    }
  }

  function enhanceCatalogRoot(root) {
    labelSidebarLandmark(root);
    initToggles(root);
    openActiveTrail(root);
  }

  function enhance(root) {
    var scope = resolveRoot(root);
    var catalogs = scope.querySelectorAll(ROOT_SELECTOR);
    if (catalogs.length === 0) {
      if (scope.matches && scope.matches(ROOT_SELECTOR)) {
        enhanceCatalogRoot(scope);
      }
      return;
    }
    catalogs.forEach(enhanceCatalogRoot);
  }

  window.FuraDocsNav = {
    enhance: enhance,
  };
})();
