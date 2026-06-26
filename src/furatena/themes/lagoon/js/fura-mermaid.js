/**
 * Furatena Mermaid diagrams — lazy vendor load, theme token bridge, htmx-safe enhance.
 */
(function () {
  'use strict';

  var utils = window.FuraDocsUtils || {};
  var log = utils.log || function () {};
  var ready = utils.ready || function (cb) {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', cb, { once: true });
    } else {
      cb();
    }
  };
  var cssColorVar = utils.cssColorVar || function (name, fallback) {
    return fallback;
  };

  var MERMAID_SRC = '/docs-vendor/mermaid.min.js';
  var loadPromise = null;
  var themeListenerBound = false;
  var themeTimer = null;

  function isDarkMode() {
    var theme = document.documentElement.getAttribute('data-theme');
    if (theme === 'dark' || theme === 'oled') return true;
    if (theme === 'light') return false;
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  }

  function getMermaidThemeConfig() {
    var darkMode = isDarkMode();
    var primaryColorRaw = cssColorVar('--color-primary', '#4FA8A0');
    var primaryHover = cssColorVar('--color-primary-hover', '#3D9287');
    var primaryDark = cssColorVar('--color-primary-dark', '#236962');
    var primaryTextColor = cssColorVar('--color-text-inverse', '#FFFFFF');
    var textColor = cssColorVar('--color-text-primary', '#252525');
    var secondaryTextColor = cssColorVar('--color-text-secondary', '#5F5B56');
    var bgPrimary = cssColorVar('--color-bg-primary', '#FDFCF9');
    var bgSecondary = cssColorVar('--color-bg-secondary', '#F9F6F0');
    var bgTertiary = cssColorVar('--color-bg-tertiary', '#F2EDE3');
    var borderColor = cssColorVar('--color-border', '#E5DFD6');
    var borderStrong = cssColorVar('--color-border-strong', '#CFC7BC');
    var accentColorRaw = cssColorVar('--color-accent', '#5BB8AF');
    var accentHover = cssColorVar('--color-accent-hover', '#4FA8A0');
    var successColorRaw = cssColorVar('--color-success', '#2E7D5A');
    var successDark = cssColorVar('--color-success-text', '#1B5E42');
    var warningColorRaw = cssColorVar('--color-warning', '#D97706');
    var warningDark = cssColorVar('--color-warning-text', '#7C3E03');
    var errorColorRaw = cssColorVar('--color-error', '#C62828');
    var errorDark = cssColorVar('--color-error-text', '#7F1D1D');
    var infoColorRaw = cssColorVar('--color-info', '#3D9DAF');
    var infoDark = cssColorVar('--color-info-text', '#1E5C6B');

    var primaryColor = darkMode ? primaryColorRaw : (primaryHover || primaryDark || primaryColorRaw);
    var accentColor = darkMode ? accentColorRaw : (accentHover || accentColorRaw);
    var successColor = darkMode ? successColorRaw : (successDark || successColorRaw);
    var warningColor = darkMode ? warningColorRaw : (warningDark || warningColorRaw);
    var errorColor = darkMode ? errorColorRaw : (errorDark || errorColorRaw);
    var infoColor = darkMode ? infoColorRaw : (infoDark || infoColorRaw);

    var textOnPrimary = primaryTextColor;
    var textOnSuccess = cssColorVar('--color-success-text', textColor);
    var textOnWarning = cssColorVar('--color-warning-text', textColor);
    var textOnError = cssColorVar('--color-error-text', textColor);

    return {
      theme: 'base',
      themeVariables: {
        darkMode: darkMode,
        primaryColor: primaryColor,
        primaryTextColor: textColor,
        primaryBorderColor: borderStrong,
        secondaryColor: accentColor,
        secondaryTextColor: textColor,
        secondaryBorderColor: borderColor,
        tertiaryColor: accentColor,
        tertiaryTextColor: secondaryTextColor,
        tertiaryBorderColor: borderColor,
        background: bgPrimary,
        mainBkg: darkMode ? bgSecondary : bgTertiary,
        secondBkg: darkMode ? bgTertiary : borderColor,
        tertiaryBkg: darkMode ? bgTertiary : borderColor,
        textColor: textColor,
        lineColor: borderStrong,
        border1: borderColor,
        border2: borderStrong,
        edgeLabelBackground: bgPrimary,
        clusterBkg: bgSecondary,
        clusterBorder: borderColor,
        noteBkgColor: bgSecondary,
        noteTextColor: textColor,
        noteBorderColor: borderColor,
        actorBkg: bgSecondary,
        actorBorder: borderStrong,
        actorTextColor: textColor,
        actorLineColor: borderStrong,
        activationBkgColor: primaryColor,
        activationBorderColor: primaryHover,
        activationTextColor: textOnPrimary,
        sequenceNumberColor: textOnPrimary,
        sectionBkgColor: bgTertiary,
        sectionBkgColor2: bgSecondary,
        altSectionBkgColor: bgSecondary,
        excludeBkgColor: bgTertiary,
        taskBkgColor: primaryColor,
        taskTextColor: textOnPrimary,
        taskTextLightColor: secondaryTextColor,
        taskTextOutsideColor: textColor,
        taskTextClickableColor: primaryColor,
        activeTaskBkgColor: primaryHover,
        activeTaskBorderColor: borderStrong,
        gridColor: borderColor,
        doneTaskBkgColor: successColor,
        doneTaskBorderColor: borderStrong,
        doneTaskTextColor: textOnSuccess,
        critBorderColor: errorColor,
        critBkgColor: errorColor,
        critTaskTextColor: textOnError,
        todayLineColor: warningColor,
        labelColor: textColor,
        errorBkgColor: errorColor,
        errorTextColor: textOnError,
        pie1: primaryColor,
        pie2: accentColor,
        pie3: successColor,
        pie4: infoColor,
        pie5: warningColor,
        pie6: errorColor,
        pieTitleTextColor: textColor,
        pieLegendTextColor: textColor,
        pieStrokeColor: borderStrong
      }
    };
  }

  function decodeHtmlEntities(text) {
    var textarea = document.createElement('textarea');
    textarea.innerHTML = text;
    return textarea.value;
  }

  function preserveSyntax(element) {
    if (element.getAttribute('data-mermaid-syntax')) return;
    var textContent = element.textContent.trim();
    if (!textContent) return;
    element.setAttribute('data-mermaid-syntax', decodeHtmlEntities(textContent).trim());
  }

  function restoreSyntax(element) {
    var stored = element.getAttribute('data-mermaid-syntax');
    if (!stored) return;
    element.innerHTML = '';
    Array.from(element.attributes).forEach(function (attr) {
      if (attr.name.startsWith('data-') && attr.name !== 'data-mermaid-syntax') {
        element.removeAttribute(attr.name);
      }
    });
    element.textContent = stored;
    element.classList.add('mermaid');
    element.removeAttribute('data-fura-mermaid-done');
  }

  function initializeMermaid() {
    if (typeof window.mermaid === 'undefined') return;
    var config = getMermaidThemeConfig();
    window.mermaid.initialize({
      startOnLoad: false,
      theme: config.theme,
      themeVariables: config.themeVariables,
      securityLevel: 'loose',
      flowchart: {
        useMaxWidth: true,
        htmlLabels: true
      }
    });
  }

  function ensureLoaded() {
    if (window.mermaid) {
      return Promise.resolve(window.mermaid);
    }
    if (loadPromise) return loadPromise;
    loadPromise = new Promise(function (resolve, reject) {
      var script = document.createElement('script');
      script.src = MERMAID_SRC;
      script.async = true;
      script.onload = function () {
        resolve(window.mermaid);
      };
      script.onerror = function () {
        reject(new Error('Failed to load Mermaid from ' + MERMAID_SRC));
      };
      document.head.appendChild(script);
    });
    return loadPromise;
  }

  function renderNodes(nodes) {
    if (!nodes.length || typeof window.mermaid === 'undefined') return Promise.resolve();

    nodes.forEach(preserveSyntax);
    initializeMermaid();

    return window.mermaid.run({
      nodes: nodes,
      suppressErrors: true
    }).then(function () {
      nodes.forEach(function (node) {
        node.setAttribute('data-fura-mermaid-done', '1');
      });
    }).catch(function (err) {
      log('Mermaid render error:', err);
    });
  }

  function rerenderAll() {
    var nodes = document.querySelectorAll('.mermaid');
    if (!nodes.length) return Promise.resolve();
    Array.from(nodes).forEach(restoreSyntax);
    initializeMermaid();
    return renderNodes(Array.from(nodes));
  }

  function enhance(root) {
    root = root || document;
    var pending = root.querySelectorAll('.mermaid:not([data-fura-mermaid-done])');
    if (!pending.length) return;
    ensureLoaded()
      .then(function () {
        return renderNodes(Array.from(pending));
      })
      .catch(function (err) {
        log('Mermaid unavailable:', err);
      });
  }

  function bindThemeListener() {
    if (themeListenerBound) return;
    themeListenerBound = true;
    window.addEventListener('themechange', function () {
      if (!document.querySelector('.mermaid')) return;
      if (themeTimer) window.clearTimeout(themeTimer);
      themeTimer = window.setTimeout(function () {
        ensureLoaded().then(function () {
          return rerenderAll();
        });
      }, 50);
    });
  }

  if (window.FuraDocs && window.FuraDocs.enhance && window.FuraDocs.enhance.register) {
    window.FuraDocs.enhance.register('mermaid', { enhance: enhance });
  }

  ready(function () {
    bindThemeListener();
    enhance(document.getElementById('page-root') || document);
  });

  window.FuraMermaid = {
    ensureLoaded: ensureLoaded,
    rerenderAll: rerenderAll,
    getMermaidThemeConfig: getMermaidThemeConfig
  };
})();
