(function () {
  "use strict";

  if (!document.body || !document.body.hasAttribute("data-fura-static")) {
    return;
  }

  var config = window.FURA_STATIC || {};
  var basePath = config.basePath || "";
  var searchUrl = config.searchUrl || (basePath + "/search.json");
  var indexPromise = null;

  function loadIndex() {
    if (!indexPromise) {
      indexPromise = fetch(searchUrl)
        .then(function (response) {
          if (!response.ok) throw new Error("search index unavailable");
          return response.json();
        })
        .catch(function () {
          return { entries: [] };
        });
    }
    return indexPromise;
  }

  function tokenize(query) {
    return query
      .toLowerCase()
      .split(/\W+/)
      .filter(function (term) {
        return term.length > 1;
      });
  }

  function scoreSection(section, needle, terms) {
    var heading = (section.heading || "").toLowerCase();
    var body = (section.body || "").toLowerCase();
    var score = 0;
    if (needle && heading.indexOf(needle) >= 0) score += 12;
    terms.forEach(function (term) {
      if (heading.indexOf(term) >= 0) score += 8;
      if (body.indexOf(term) >= 0) score += 3;
    });
    return score;
  }

  function bestSectionMatch(entry, needle, terms) {
    var best = null;
    (entry.sections || []).forEach(function (section) {
      var score = scoreSection(section, needle, terms);
      if (score > 0 && (!best || score > best.score)) {
        best = { section: section, score: score };
      }
    });
    return best;
  }

  function scoreEntry(entry, needle, terms) {
    var title = (entry.title || "").toLowerCase();
    var desc = (entry.description || "").toLowerCase();
    var snippet = (entry.snippet || "").toLowerCase();
    var score = 0;
    if (needle && title.indexOf(needle) >= 0) score += 20;
    terms.forEach(function (term) {
      if (title.indexOf(term) >= 0) score += 8;
      if (desc.indexOf(term) >= 0) score += 5;
      if (snippet.indexOf(term) >= 0) score += 2;
      (entry.sections || []).forEach(function (section) {
        score += scoreSection(section, needle, [term]);
      });
    });
    return score;
  }

  function resultSnippet(entry, needle, terms, sectionMatch) {
    if (sectionMatch && sectionMatch.section) {
      var heading = sectionMatch.section.heading || "";
      var body = sectionMatch.section.body || "";
      if (heading && body) return heading + " — " + body.slice(0, 160);
      if (body) return body.slice(0, 180);
      if (heading) return heading;
    }
    if (entry.description) return entry.description;
    if (entry.snippet) return entry.snippet;
    var sections = entry.sections || [];
    for (var i = 0; i < sections.length; i++) {
      var bodyText = sections[i].body || "";
      var lower = bodyText.toLowerCase();
      if ((needle && lower.indexOf(needle) >= 0) || terms.some(function (t) { return lower.indexOf(t) >= 0; })) {
        return bodyText.slice(0, 180);
      }
    }
    return "";
  }

  function searchEntries(entries, query, limit) {
    var needle = query.trim().toLowerCase();
    if (!needle) return [];
    var terms = tokenize(needle);
    if (!terms.length) terms = [needle];
    return entries
      .map(function (entry) {
        var sectionMatch = bestSectionMatch(entry, needle, terms);
        return {
          entry: entry,
          score: scoreEntry(entry, needle, terms),
          snippet: resultSnippet(entry, needle, terms, sectionMatch),
          sectionMatch: sectionMatch,
        };
      })
      .filter(function (hit) {
        return hit.score > 0;
      })
      .sort(function (a, b) {
        return b.score - a.score || (a.entry.title || "").localeCompare(b.entry.title || "");
      })
      .slice(0, limit || 12);
  }

  function pageUrl(entry, sectionMatch) {
    var url = entry.url || "/";
    if (basePath && url.indexOf(basePath) === 0) {
      url = url.slice(basePath.length) || "/";
    }
    if (sectionMatch && sectionMatch.section && sectionMatch.section.anchor) {
      return url + "#" + sectionMatch.section.anchor;
    }
    return url;
  }

  function renderSuggestHtml(hits, query) {
    if (!hits.length) {
      return query
        ? '<p class="search-modal__no-results-text">No results for “' + query + '”.</p>'
        : "";
    }
    var html = '<ul class="search-modal__results-list fura-search-suggest" role="listbox">';
    hits.forEach(function (hit) {
      html += '<li class="search-modal__result fura-search-suggest__item">';
      html += '<a class="search-modal__result-link" href="' + pageUrl(hit.entry, hit.sectionMatch) + '" role="option">';
      html += '<span class="search-modal__result-title">' + hit.entry.title + "</span>";
      if (hit.snippet) {
        html += '<span class="search-modal__result-snippet">' + hit.snippet + "</span>";
      }
      html += "</a></li>";
    });
    html += "</ul>";
    return html;
  }

  function renderResultsHtml(hits, query) {
    if (!hits.length) {
      return query
        ? '<div class="search-page__no-results"><p class="search-page__no-results-text">No results for “' + query + '”.</p></div>'
        : '<div class="search-page__empty"><p class="search-page__empty-hint">Type to search the documentation index.</p></div>';
    }
    var html = '<div class="search-page__results-header"><span class="search-page__results-count">' + hits.length + " results</span></div>";
    html += '<ul class="search-page__results-list">';
    hits.forEach(function (hit) {
      html += '<li class="search-page__result-item">';
      html += '<a class="search-page__result-link" href="' + pageUrl(hit.entry, hit.sectionMatch) + '">';
      html += '<div class="search-page__result-content"><span class="search-page__result-title">' + hit.entry.title + "</span>";
      if (hit.entry.section) {
        html += '<span class="search-page__result-section">' + hit.entry.section + "</span>";
      }
      html += "</div>";
      if (hit.snippet) {
        html += '<p class="search-page__result-excerpt">' + hit.snippet + "</p>";
      }
      html += "</a></li>";
    });
    html += "</ul>";
    return html;
  }

  function bindSearchModal() {
    var modal = document.getElementById("search-modal");
    var input = document.getElementById("search-modal-input");
    var results = document.getElementById("search-modal-results-list");
    if (!modal || !input || !results || input.dataset.chirpStaticSearchBound) return;
    input.dataset.chirpStaticSearchBound = "1";

    var timer = null;
    input.addEventListener("input", function () {
      var query = input.value.trim();
      window.clearTimeout(timer);
      timer = window.setTimeout(function () {
        loadIndex().then(function (payload) {
          var hits = searchEntries(payload.entries || [], query, 6);
          results.innerHTML = renderSuggestHtml(hits, query);
        });
      }, 200);
    });
  }

  function bindSearchPage() {
    var panel = document.getElementById("search-results-panel");
    var input = document.getElementById("search-page-input");
    var form = input && input.closest("form");
    if (!panel || !input || !form || form.dataset.chirpStaticSearchBound) return;
    form.dataset.chirpStaticSearchBound = "1";
    form.removeAttribute("hx-get");
    form.removeAttribute("hx-target");
    form.removeAttribute("hx-trigger");
    form.removeAttribute("hx-push-url");

    function render() {
      var query = input.value.trim();
      loadIndex().then(function (payload) {
        var hits = searchEntries(payload.entries || [], query, 12);
        panel.innerHTML = renderResultsHtml(hits, query);
      });
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      render();
    });
    input.addEventListener("input", function () {
      window.clearTimeout(form._chirpSearchTimer);
      form._chirpSearchTimer = window.setTimeout(render, 250);
    });

    if (input.value.trim()) render();
  }

  function init() {
    bindSearchModal();
    bindSearchPage();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
