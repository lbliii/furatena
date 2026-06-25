/**
 * Furatena enhancement registry — one lifecycle for HTMX-swapped chrome.
 *
 * Contract:
 * - Shell binds once (document delegation, search modal, author poll).
 * - Anything inside #page-root is ephemeral — re-enhance on htmx:afterSettle.
 * - Entry point: ChirpDocs.enhance.refresh(#page-root)
 */
(function () {
  'use strict';

  var modules = [];
  var copyDelegationBound = false;
  var pageActionsBound = false;
  var BODY_SHELL_CLASSES = [
    'chirp-theme-shell--rail-only',
    'fura-surface--catalog',
    'fura-surface--app',
  ];

  function register(name, handlers) {
    modules.push({ name: name, enhance: handlers.enhance, cleanup: handlers.cleanup });
  }

  function cleanup(root) {
    for (var i = 0; i < modules.length; i += 1) {
      if (modules[i].cleanup) {
        modules[i].cleanup(root);
      }
    }
  }

  function enhance(root) {
    for (var i = 0; i < modules.length; i += 1) {
      if (modules[i].enhance) {
        modules[i].enhance(root);
      }
    }
  }

  function refresh(root) {
    root = root || document.getElementById('page-root') || document;
    cleanup(root);
    window.requestAnimationFrame(function () {
      window.requestAnimationFrame(function () {
        enhance(root);
        if (window.Alpine && typeof window.Alpine.initTree === 'function') {
          window.Alpine.initTree(root);
        }
      });
    });
  }

  var copyToClipboard =
    (window.ChirpDocsUtils && window.ChirpDocsUtils.copyToClipboard) ||
    (async function (text) {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
        return;
      }
      var area = document.createElement('textarea');
      area.value = text;
      area.setAttribute('readonly', '');
      area.style.position = 'fixed';
      area.style.left = '-9999px';
      document.body.appendChild(area);
      area.select();
      document.execCommand('copy');
      document.body.removeChild(area);
    });

  var COPY_ICON =
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' +
    '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>' +
    '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>' +
    '</svg><span>Copy</span>';

  var COPIED_ICON =
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' +
    '<polyline points="20 6 9 17 4 12"></polyline></svg><span>Copied!</span>';

  register('body-surface', {
    enhance: function () {
      var pageRoot = document.getElementById('page-root');
      var surface = (pageRoot && pageRoot.getAttribute('data-fura-surface')) || 'app';
      var body = document.body;
      if (!body) return;
      if (!body.classList.contains('chirp-theme-shell')) {
        body.classList.add('chirp-theme-shell');
      }
      BODY_SHELL_CLASSES.forEach(function (cls) {
        body.classList.remove(cls);
      });
      body.classList.add('fura-surface--' + surface);
      if (surface === 'catalog') {
        body.classList.add('chirp-theme-shell--rail-only');
      }
    },
  });

  register('heading-anchors', {
    enhance: function (root) {
      var copyToClipboardFn =
        (window.ChirpDocsUtils && window.ChirpDocsUtils.copyToClipboard) || copyToClipboard;
      var scopes = root.querySelectorAll(
        '.chirp-theme-docs-layout__content, .chirp-theme-track-section__body, .docs-content'
      );
      if (!scopes.length) {
        scopes = [root];
      }
      var linkIcon =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
        '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path>' +
        '<path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path>' +
        '</svg>';
      var copiedIcon =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
        '<polyline points="20 6 9 17 4 12"></polyline></svg>';

      scopes.forEach(function (scope) {
        scope.querySelectorAll('h2[id], h3[id], h4[id], h5[id], h6[id]').forEach(function (heading) {
          if (heading.closest('.steps')) return;
          if (heading.querySelector('.copy-link')) return;

          var id = heading.getAttribute('id');
          if (!id) return;

          heading.classList.add('heading-anchor');

          var link = document.createElement('a');
          link.href = '#' + id;
          link.className = 'copy-link';
          link.setAttribute('aria-label', 'Copy link to this section');
          link.setAttribute('title', 'Copy link to section');
          link.innerHTML = linkIcon;

          link.addEventListener('click', function (event) {
            if (event.ctrlKey || event.metaKey || event.shiftKey || event.button !== 0) return;
            event.preventDefault();
            var url =
              window.location.origin + window.location.pathname + window.location.search + '#' + id;
            copyToClipboardFn(url)
              .then(function () {
                link.innerHTML = copiedIcon;
                link.classList.add('copied');
                link.setAttribute('aria-label', 'Link copied!');
                window.setTimeout(function () {
                  link.innerHTML = linkIcon;
                  link.classList.remove('copied');
                  link.setAttribute('aria-label', 'Copy link to this section');
                  link.blur();
                }, 2000);
              })
              .catch(function () {
                link.setAttribute('aria-label', 'Failed to copy link');
              });
          });

          heading.appendChild(link);
        });
      });
    },
  });

  register('code-copy', {
    enhance: function () {
      if (copyDelegationBound) return;
      copyDelegationBound = true;
      document.addEventListener('click', async function (event) {
        var button = event.target.closest('[data-fura-copy-code]');
        if (!button) return;
        event.preventDefault();
        var wrapper = button.closest('.code-block-wrapper');
        var code = wrapper && wrapper.querySelector('pre code');
        if (!code) return;
        try {
          await copyToClipboard(code.textContent || '');
          button.classList.add('copied');
          button.setAttribute('aria-label', 'Code copied!');
          button.innerHTML = COPIED_ICON;
        } catch (_err) {
          button.setAttribute('aria-label', 'Failed to copy');
        }
        window.setTimeout(function () {
          button.classList.remove('copied');
          button.setAttribute('aria-label', 'Copy');
          button.innerHTML = COPY_ICON;
        }, 2000);
      });
    },
  });

  function closePageActionsMenu(fromEl) {
    var root = fromEl && fromEl.closest('[data-chirp-page-actions]');
    if (!root) return;
    if (root._x_dataStack && root._x_dataStack[0] && typeof root._x_dataStack[0].close === 'function') {
      var trigger = root.querySelector('.chirp-theme-page-actions__trigger');
      root._x_dataStack[0].close(trigger);
      return;
    }
    var menu = root.querySelector('[popover]');
    if (menu && typeof menu.hidePopover === 'function') {
      menu.hidePopover();
    }
  }

  function toAbsoluteUrl(relativeUrl) {
    if (!relativeUrl) return window.location.href;
    if (relativeUrl.indexOf('http://') === 0 || relativeUrl.indexOf('https://') === 0) {
      return relativeUrl;
    }
    return window.location.origin + relativeUrl;
  }

  function buildAIUrl(aiName, prompt) {
    var encodedPrompt = encodeURIComponent(prompt);
    var baseUrls = {
      claude: 'https://claude.ai/new?q=',
      chatgpt: 'https://chatgpt.com/?q=',
      gemini: 'https://gemini.google.com/app?q=',
    };
    return (baseUrls[aiName] || baseUrls.claude) + encodedPrompt;
  }

  function flashActionItem(button, html, className, delay) {
    var originalHTML = button.innerHTML;
    if (className) button.classList.add(className);
    button.innerHTML = html;
    window.setTimeout(function () {
      if (className) button.classList.remove(className);
      button.innerHTML = originalHTML;
    }, delay || 2000);
  }

  register('page-actions', {
    enhance: function () {
      if (pageActionsBound) return;
      pageActionsBound = true;

      document.addEventListener('click', async function (event) {
        var copyButton = event.target.closest('[data-action^="copy"]');
        if (copyButton) {
          event.preventDefault();
          var action = copyButton.getAttribute('data-action');
          var url = copyButton.getAttribute('data-url');
          if (!url) return;
          try {
            if (action === 'copy-url') {
              await copyToClipboard(toAbsoluteUrl(url));
              flashActionItem(
                copyButton,
                '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="20 6 9 17 4 12"></polyline></svg><span>URL copied!</span>',
                'success'
              );
            } else if (action === 'copy-llm-txt') {
              var response = await fetch(url);
              if (!response.ok) throw new Error('HTTP ' + response.status);
              await copyToClipboard(await response.text());
              flashActionItem(
                copyButton,
                '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="20 6 9 17 4 12"></polyline></svg><span>LLM text copied!</span>',
                'success'
              );
            }
          } catch (_err) {
            flashActionItem(
              copyButton,
              '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M6 18L18 6M6 6l12 12"></path></svg><span>Copy failed</span>'
            );
          }
          return;
        }

        var aiLink = event.target.closest('[data-ai]');
        if (!aiLink) return;
        event.preventDefault();
        var menuRoot = aiLink.closest('[data-chirp-page-actions]');
        var copyLlmBtn = menuRoot && menuRoot.querySelector('[data-action="copy-llm-txt"]');
        var llmTxtUrl = copyLlmBtn && copyLlmBtn.getAttribute('data-url');
        if (!llmTxtUrl) {
          window.open(aiLink.getAttribute('href'), '_blank', 'noopener,noreferrer');
          return;
        }
        var originalHTML = aiLink.innerHTML;
        aiLink.innerHTML = '<span>Loading...</span>';
        try {
          var llmResponse = await fetch(llmTxtUrl);
          if (!llmResponse.ok) throw new Error('HTTP ' + llmResponse.status);
          await copyToClipboard(await llmResponse.text());
          var aiPrompt =
            'I have documentation content from ' +
            toAbsoluteUrl(llmTxtUrl) +
            ' copied to my clipboard. Please help me understand it.';
          var aiUrl = buildAIUrl(aiLink.getAttribute('data-ai'), aiPrompt);
          aiLink.innerHTML = '<span>Copied! Opening...</span>';
          window.setTimeout(function () {
            aiLink.innerHTML = originalHTML;
            closePageActionsMenu(aiLink);
            window.open(aiUrl, '_blank', 'noopener,noreferrer');
          }, 500);
        } catch (_err2) {
          aiLink.innerHTML = originalHTML;
          window.open(aiLink.getAttribute('href'), '_blank', 'noopener,noreferrer');
        }
      });
    },
  });

  register('toc', {
    enhance: function (root) {
      if (window.ChirpDocsTOC) {
        window.ChirpDocsTOC.init(root);
      }
    },
    cleanup: function (root) {
      if (window.ChirpDocsTOC) {
        window.ChirpDocsTOC.cleanup(root);
      }
    },
  });

  register('docs-nav', {
    enhance: function (root) {
      if (window.ChirpDocsNav) {
        window.ChirpDocsNav.enhance(root);
      }
    },
  });

  window.ChirpDocs = window.ChirpDocs || {};
  window.ChirpDocs.enhance = {
    register: register,
    refresh: refresh,
    cleanup: cleanup,
    enhance: enhance,
  };

  window.chirpDocsTabSet = function (initial, syncKey, tabIds) {
    tabIds = tabIds || [];
    var storageKey = syncKey && syncKey.toLowerCase() !== 'none' ? 'fura-tabs-sync-' + syncKey : '';
    var active = initial;
    if (storageKey) {
      try {
        var stored = localStorage.getItem(storageKey);
        if (stored !== null && stored !== '') {
          var storedIndex = parseInt(stored, 10);
          if (!isNaN(storedIndex) && tabIds[storedIndex]) {
            active = tabIds[storedIndex];
          } else if (tabIds.indexOf(stored) !== -1) {
            active = stored;
          }
        }
      } catch (_err) {}
    }
    if (tabIds.length && tabIds.indexOf(active) === -1) {
      active = initial;
    }
    return {
      active: active,
      tabIds: tabIds,
      pick: function (id) {
        var clickedIndex = tabIds.indexOf(id);
        this.active = id;
        if (syncKey && syncKey.toLowerCase() !== 'none' && clickedIndex !== -1) {
          document.querySelectorAll('[data-sync="' + syncKey + '"]').forEach(function (el) {
            var stack = el._x_dataStack && el._x_dataStack[0];
            if (!stack || !stack.tabIds) return;
            if (clickedIndex < stack.tabIds.length) {
              stack.active = stack.tabIds[clickedIndex];
            }
          });
        }
        if (!storageKey) return;
        try {
          localStorage.setItem(storageKey, String(clickedIndex >= 0 ? clickedIndex : 0));
        } catch (_err2) {}
      },
    };
  };

  window.ChirpDocsChrome = { refresh: refresh };

  (function bootstrapEnhancements() {
    var run = function () {
      refresh(document.getElementById('page-root') || document);
    };
    if (window.ChirpDocsUtils && window.ChirpDocsUtils.ready) {
      window.ChirpDocsUtils.ready(run);
    } else if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', run, { once: true });
    } else {
      run();
    }
  })();
})();
