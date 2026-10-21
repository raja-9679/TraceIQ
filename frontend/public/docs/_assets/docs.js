/* Builds the guide's chrome around each page: side navigation (from nav.js),
   "On this page", previous/next links, image zoom and the mobile menu.
   A page only declares <body data-page="group/page"> and its own content.
   No inline scripts anywhere: the app's CSP is script-src 'self'. */
(function () {
  'use strict';

  var ICONS = {
    inbox: '<polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
    flag: '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" x2="4" y1="22" y2="15"/>',
    chart: '<path d="M3 3v18h18"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/>',
    flask: '<path d="M10 2v7.5a2 2 0 0 1-.2.9L4.7 20.5a1 1 0 0 0 .9 1.5h12.8a1 1 0 0 0 .9-1.5l-5.1-10.1a2 2 0 0 1-.2-.9V2"/><path d="M8.5 2h7"/><path d="M7 16h10"/>',
    play: '<polygon points="6 3 20 12 6 21 6 3"/>',
    radio: '<path d="M4.9 19.1C1 15.2 1 8.8 4.9 4.9"/><path d="M7.8 16.2c-2.3-2.3-2.3-6.1 0-8.5"/><circle cx="12" cy="12" r="2"/><path d="M16.2 7.8c2.3 2.3 2.3 6.1 0 8.5"/><path d="M19.1 4.9C23 8.8 23 15.1 19.1 19"/>',
    shield: '<path d="M20 13c0 5-3.5 7.5-7.7 9a1 1 0 0 1-.6 0C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.2-2.7a1.2 1.2 0 0 1 1.6 0C14.5 3.8 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
    settings: '<path d="M20 7h-9"/><path d="M14 17H5"/><circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/>',
    book: '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>',
    chev: '<path d="m9 18 6-6-6-6"/>',
    pulse: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    menu: '<line x1="4" x2="20" y1="6" y2="6"/><line x1="4" x2="20" y1="12" y2="12"/><line x1="4" x2="20" y1="18" y2="18"/>',
    out: '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  };
  function icon(name, size) {
    var s = size || 16;
    return '<svg xmlns="http://www.w3.org/2000/svg" width="' + s + '" height="' + s + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + ICONS[name] + '</svg>';
  }
  function esc(t) { return String(t).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  // The guide's root URL, from this script's own address (…/docs/_assets/docs.js),
  // so the guide works wherever the app is hosted.
  var me = document.currentScript && document.currentScript.src;
  var ROOT = me ? new URL('../', me).href : '/docs/';
  var NAV = window.DOCS_NAV || [];
  var current = document.body.getAttribute('data-page') || '';
  window.DOCS_ICON = icon;
  window.DOCS_ROOT = ROOT;

  // ---------- side navigation ----------
  var side = document.getElementById('sidenav');
  if (side) {
    var html = '<a class="nav-home' + (current === '' ? ' active' : '') + '" href="' + ROOT + '">' +
      '<span class="nav-dot t-lavender">' + icon('book', 15) + '</span>Start here</a>';
    NAV.forEach(function (g, gi) {
      var open = g.pages.some(function (p) { return p.slug === current; });
      html += '<div class="nav-group' + (open ? ' open' : '') + '" data-group="' + gi + '">' +
        '<button type="button" aria-expanded="' + open + '">' +
        '<span class="nav-dot ' + g.tone + '">' + icon(g.icon, 15) + '</span>' + esc(g.name) +
        '<span class="chev">' + icon('chev', 15) + '</span></button><ul>';
      g.pages.forEach(function (p) {
        if (p.ready) {
          html += '<li><a href="' + ROOT + p.slug + '.html"' + (p.slug === current ? ' class="active" aria-current="page"' : '') + '>' + esc(p.name) + '</a></li>';
        } else {
          html += '<li><span>' + esc(p.name) + ' <em class="soon">soon</em></span></li>';
        }
      });
      html += '</ul></div>';
    });
    side.innerHTML = '<nav aria-label="Guide">' + html + '</nav>';
    side.addEventListener('click', function (e) {
      var b = e.target.closest('.nav-group > button');
      if (!b) return;
      var grp = b.parentElement;
      grp.classList.toggle('open');
      b.setAttribute('aria-expanded', grp.classList.contains('open'));
    });
  }

  // ---------- home page: group cards ----------
  var places = document.getElementById('places');
  if (places) {
    places.innerHTML = NAV.map(function (g) {
      var first = g.pages.filter(function (p) { return p.ready; })[0];
      var inner = '<span class="nav-dot ' + g.tone + '">' + icon(g.icon, 19) + '</span><span><h4>' + esc(g.name) +
        '</h4><p>' + esc(g.blurb) + '</p><p class="soon" style="margin-top:6px">' + g.pages.length + ' page' + (g.pages.length > 1 ? 's' : '') +
        (first ? '' : ' · coming soon') + '</p></span>';
      return first
        ? '<a class="mini place-card" href="' + ROOT + first.slug + '.html">' + inner + '</a>'
        : '<div class="mini place-card">' + inner + '</div>';
    }).join('');
  }

  // ---------- breadcrumbs + previous / next ----------
  var flat = [];
  NAV.forEach(function (g) { g.pages.forEach(function (p) { if (p.ready) flat.push({ g: g, p: p }); }); });
  var idx = -1;
  flat.forEach(function (x, i) { if (x.p.slug === current) idx = i; });

  var crumbs = document.getElementById('crumbs');
  if (crumbs && idx >= 0) {
    crumbs.innerHTML = '<a href="' + ROOT + '">Guide</a><span aria-hidden="true">/</span><span>' + esc(flat[idx].g.name) +
      '</span><span aria-hidden="true">/</span><span>' + esc(flat[idx].p.name) + '</span>';
  }
  var pager = document.getElementById('pager');
  if (pager) {
    var prev = idx > 0 ? flat[idx - 1] : (idx === 0 ? null : null);
    var next = idx >= 0 ? flat[idx + 1] : flat[0];
    var out = '';
    if (idx >= 0) {
      out += prev ? '<a href="' + ROOT + prev.p.slug + '.html"><small>Previous</small><strong>' + esc(prev.p.name) + '</strong></a>'
        : '<a href="' + ROOT + '"><small>Previous</small><strong>Start here</strong></a>';
    }
    if (next) out += '<a class="next" href="' + ROOT + next.p.slug + '.html"><small>Next</small><strong>' + esc(next.p.name) + '</strong></a>';
    pager.innerHTML = out;
  }

  // ---------- on this page ----------
  var toc = document.getElementById('toc');
  var heads = Array.prototype.slice.call(document.querySelectorAll('.content h2[id]'));
  if (toc && heads.length) {
    toc.innerHTML = '<h4>On this page</h4>' + heads.map(function (h) {
      return '<a href="#' + h.id + '">' + esc(h.getAttribute('data-toc') || h.textContent) + '</a>';
    }).join('');
    var links = toc.querySelectorAll('a');
    var spy = function () {
      var y = window.scrollY + 120, active = 0;
      heads.forEach(function (h, i) { if (h.getBoundingClientRect().top + window.scrollY <= y) active = i; });
      links.forEach(function (a, i) { a.classList.toggle('active', i === active); });
    };
    window.addEventListener('scroll', spy, { passive: true });
    spy();
  }

  // ---------- image zoom ----------
  var box = document.createElement('div');
  box.className = 'lightbox';
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-label', 'Enlarged screenshot');
  box.innerHTML = '<img alt="">';
  document.body.appendChild(box);
  document.addEventListener('click', function (e) {
    var img = e.target.closest('figure:not(.video) .frame img');
    if (img) {
      box.querySelector('img').src = img.currentSrc || img.src;
      box.querySelector('img').alt = img.alt;
      box.classList.add('open');
      return;
    }
    if (e.target.closest('.lightbox')) box.classList.remove('open');
  });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') { box.classList.remove('open'); document.body.classList.remove('nav-open'); } });

  // ---------- mobile menu ----------
  var tog = document.getElementById('menu-toggle');
  if (tog) tog.addEventListener('click', function () { document.body.classList.toggle('nav-open'); });

  // ---------- brand + header icons ----------
  document.querySelectorAll('[data-icon]').forEach(function (el) { el.innerHTML = icon(el.getAttribute('data-icon'), +el.getAttribute('data-size') || 16); });
})();
