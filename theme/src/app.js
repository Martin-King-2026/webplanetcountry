/* Веб-камеры планеты — скрипт темы.
   Читает блок <div class="wp" data-t="..."> в посте и достраивает страницу:
   алфавитные списки, описание из Викигида/Википедии, слайд-шоу, погоду, плеер камер.
   YouTube IFrame API и hls.js подгружаются только по требованию. */
(function () {
  'use strict';

  // static: true — сайт собран статичными файлами (GitHub Pages), ленты Blogger заменены JSON-файлами в {base}feeds/
  var CFG = Object.assign({
    windyKey: '', static: false, base: '/',
    hlsUrl: 'https://cdn.jsdelivr.net/npm/hls.js@1.5.13/dist/hls.min.js',
    leafletJs: 'https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js',
    leafletCss: 'https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css',
    tiles: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    maxSlides: 10
  }, window.WP_CONFIG || {});

  var D = document, R = D.documentElement;
  var WD = 'https://www.wikidata.org/w/api.php';

  // ---------- утилиты ----------
  function $(s, el) { return (el || D).querySelector(s); }
  function $$(s, el) { return Array.prototype.slice.call((el || D).querySelectorAll(s)); }
  function h(tag, attrs) {
    var el = D.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      var v = attrs[k];
      if (v == null || v === false) return;
      if (k === 'text') el.textContent = v;
      else if (k === 'html') el.innerHTML = v;
      else if (k.indexOf('on') === 0) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? '' : v);
    });
    for (var i = 2; i < arguments.length; i++) add(el, arguments[i]);
    return el;
  }
  function add(el, c) {
    if (c == null || c === false) return;
    if (Array.isArray(c)) c.forEach(function (x) { add(el, x); });
    else el.append(c.nodeType ? c : String(c));
  }
  function svg(path) {
    return '<svg viewBox="0 0 24 24" aria-hidden="true">' + path + '</svg>';
  }
  var ICON = {
    star: svg('<path d="M12 3l2.8 5.7 6.2.9-4.5 4.4 1 6.2L12 17.3 6.5 20.2l1-6.2L3 9.6l6.2-.9z"/>'),
    share: svg('<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="M8.6 13.5l6.8 4M15.4 6.5l-6.8 4"/>'),
    play: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4l14 8-14 8z"/></svg>',
    map: svg('<path d="M9 4L3 6v14l6-2 6 2 6-2V4l-6 2z"/><path d="M9 4v14M15 6v14"/>')
  };
  var store = {
    get: function (k, d) { try { var v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set: function (k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* приватный режим */ } }
  };
  var cache = {
    get: function (k) { try { var v = sessionStorage.getItem(k); return v ? JSON.parse(v) : null; } catch (e) { return null; } },
    set: function (k, v) { try { sessionStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* переполнено */ } }
  };
  function enc(s) { return encodeURIComponent(s); }
  function norm(s) {
    return String(s || '').toLowerCase().replace(/ё/g, 'е').normalize('NFD').replace(/[̀-ͯ]/g, '');
  }
  function split2(s) {
    var p = String(s || '').split('|');
    return { ru: p[0].trim(), en: (p[1] || p[0]).trim() };
  }
  function pathOf(href) { try { return new URL(href, location.href).pathname; } catch (e) { return href; } }
  function dist(a, b, c, d) {
    var r = Math.PI / 180, x = Math.sin((c - a) * r / 2), y = Math.sin((d - b) * r / 2);
    return 12742 * Math.asin(Math.sqrt(x * x + Math.cos(a * r) * Math.cos(c * r) * y * y));
  }
  function getJSON(url, opt) {
    opt = opt || {};
    var key = 'c:' + url;
    if (opt.cache !== false) { var c = cache.get(key); if (c) return Promise.resolve(c); }
    var ctl = window.AbortController ? new AbortController() : null;
    var tm = setTimeout(function () { if (ctl) ctl.abort(); }, opt.timeout || 12000);
    return fetch(url, { headers: opt.headers, signal: ctl && ctl.signal })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (j) { if (opt.cache !== false) cache.set(key, j); return j; })
      .finally(function () { clearTimeout(tm); });
  }
  function loadScript(src) {
    return new Promise(function (res, rej) {
      var s = D.createElement('script');
      s.src = src; s.async = true; s.onload = res; s.onerror = rej;
      D.head.appendChild(s);
    });
  }
  function toast(msg) {
    var el = h('div', { class: 'toast', role: 'status' }, msg);
    D.body.append(el);
    setTimeout(function () { el.remove(); }, 2600);
  }

  // ---------- языки ----------
  var T = {
    ru: {
      site: 'Веб-камеры планеты', countries: 'Страны', cities: 'Города', allCams: 'Все камеры',
      top: 'Топ по просмотрам', fav: 'Избранное', favEmpty: 'Пока пусто. Нажмите «В избранное» на странице камеры, города или страны.',
      search: 'Страна, город или камера', searchAll: 'Искать «{q}» по сайту', filter: 'Быстрый поиск по списку',
      noCams: 'Здесь пока нет веб-камер.', nearest: 'Ближайшие камеры', otherCams: 'Другие камеры города',
      localTime: 'Местное время', day: 'день', night: 'ночь', wind: 'ветер', ms: 'м/с', sunrise: 'восход', sunset: 'закат',
      sights: 'Достопримечательности', watch: 'Смотреть', share: 'Поделиться', copy: 'Копировать ссылку', copied: 'Ссылка скопирована',
      addFav: 'В избранное', inFav: 'В избранном', loading: 'Загрузка…',
      textSrc: 'Текст: {src}, статья «{title}», лицензия CC BY-SA', wv: 'Викигид', wp: 'Википедия',
      photoSrc: 'Фото: Википедия и Викисклад, нажмите на подпись, чтобы открыть статью',
      switching: 'Трансляция недоступна — включаю следующую', allFailed: 'Сейчас ни одна трансляция не отвечает. Попробуйте позже.',
      notLoading: 'Не загружается? Выберите другую трансляцию ниже.', km: 'км',
      weekTop: 'За неделю', allTop: 'За всё время', newCams: 'Новые камеры', popular: 'Популярное',
      homeLead: 'Прямые трансляции с веб-камер городов мира. Выберите страну, город или найдите камеру через поиск.',
      statC: 'стран: {n}', statK: 'камер: {n}', camsN: 'камер: {n}', theme: 'Светлая или тёмная тема', lang: 'Switch to English',
      windy: 'Камеры Windy.com поблизости', notFound: 'Страница не найдена', wxSrc: 'Погода: Open-Meteo.com',
      menu: 'Меню', noTop: 'Статистика просмотров появится, когда у блога накопятся посещения.', empty: 'Список пуст.',
      onSource: 'Смотреть на сайте владельца камеры ↗',
      mapCities: 'Карта городов с камерами', mapCams: 'Карта камер', mapFail: 'Карта сейчас не загружается. Попробуйте позже.',
      mapEmpty: 'Пока нечего показать на карте.', approx: 'место примерное: у камеры нет точных координат',
      approxMany: 'Ещё {n} камер без точных координат — в списке ниже ↓',
      searchHint: 'Введите в поиске слева хотя бы две буквы названия страны, города или камеры.',
      nothingFound: 'По запросу «{q}» ничего не найдено.'
    },
    en: {
      site: 'Webcams of the Planet', countries: 'Countries', cities: 'Cities', allCams: 'All webcams',
      top: 'Most viewed', fav: 'Favorites', favEmpty: 'Nothing here yet. Tap “Add to favorites” on a webcam, city or country page.',
      search: 'Country, city or webcam', searchAll: 'Search the site for “{q}”', filter: 'Filter this list',
      noCams: 'No webcams here yet.', nearest: 'Nearest webcams', otherCams: 'More webcams in this city',
      localTime: 'Local time', day: 'day', night: 'night', wind: 'wind', ms: 'm/s', sunrise: 'sunrise', sunset: 'sunset',
      sights: 'Sights', watch: 'Watch', share: 'Share', copy: 'Copy link', copied: 'Link copied',
      addFav: 'Add to favorites', inFav: 'In favorites', loading: 'Loading…',
      textSrc: 'Text: {src}, article “{title}”, CC BY-SA license', wv: 'Wikivoyage', wp: 'Wikipedia',
      photoSrc: 'Photos: Wikipedia and Wikimedia Commons, tap a caption to open the article',
      switching: 'Stream unavailable — switching to the next one', allFailed: 'No stream is responding right now. Please try later.',
      notLoading: 'Not loading? Pick another stream below.', km: 'km',
      weekTop: 'This week', allTop: 'All time', newCams: 'New webcams', popular: 'Popular',
      homeLead: 'Live streams from webcams in cities around the world. Pick a country or city, or search for a webcam.',
      statC: 'countries: {n}', statK: 'webcams: {n}', camsN: 'webcams: {n}', theme: 'Light or dark theme', lang: 'Переключить на русский',
      windy: 'Windy.com webcams nearby', notFound: 'Page not found', wxSrc: 'Weather: Open-Meteo.com',
      menu: 'Menu', noTop: 'View statistics will appear once the blog gets some visits.', empty: 'The list is empty.',
      onSource: 'Watch on the camera owner’s site ↗',
      mapCities: 'Map of cities with webcams', mapCams: 'Map of webcams', mapFail: 'The map is not loading right now. Please try later.',
      mapEmpty: 'Nothing to show on the map yet.', approx: 'approximate location: no exact coordinates for this webcam',
      approxMany: '{n} more webcams without exact coordinates — see the list below ↓',
      searchHint: 'Type at least two letters of a country, city or webcam name in the search box on the left.',
      nothingFound: 'Nothing found for “{q}”.'
    }
  };
  var WMO = [
    [[0], '☀️', 'ясно', 'clear'], [[1, 2], '🌤️', 'малооблачно', 'partly cloudy'], [[3], '☁️', 'пасмурно', 'overcast'],
    [[45, 48], '🌫️', 'туман', 'fog'], [[51, 53, 55, 56, 57], '🌦️', 'морось', 'drizzle'],
    [[61, 63, 65, 66, 67], '🌧️', 'дождь', 'rain'], [[71, 73, 75, 77], '🌨️', 'снег', 'snow'],
    [[80, 81, 82], '🌧️', 'ливень', 'showers'], [[85, 86], '🌨️', 'снегопад', 'snow showers'],
    [[95, 96, 99], '⛈️', 'гроза', 'thunderstorm']
  ];
  var lang = R.getAttribute('data-lang') === 'en' ? 'en' : 'ru';
  var langHooks = [];
  function t(k, v) {
    var s = (T[lang][k] != null ? T[lang][k] : (T.ru[k] != null ? T.ru[k] : k));
    return v ? s.replace(/\{(\w+)\}/g, function (m, x) { return v[x] != null ? v[x] : ''; }) : s;
  }
  function pick(title) { var p = split2(title); return lang === 'en' ? p.en : p.ru; }
  function nm(it) { return (lang === 'en' ? it.en : it.ru) || it.ru || ''; }
  function setLang(l) {
    lang = l;
    R.setAttribute('data-lang', l);
    R.lang = l;
    store.set('wp-lang', l);
    applyLang();
  }
  function applyLang() {
    $$('[data-en]').forEach(function (el) {
      if (el.classList.contains('wp')) return;
      if (el.getAttribute('data-ru') == null) el.setAttribute('data-ru', el.textContent);
      el.textContent = lang === 'en' ? el.getAttribute('data-en') : el.getAttribute('data-ru');
    });
    $$('[data-title]').forEach(function (el) { el.textContent = pick(el.getAttribute('data-title')); });
    $$('[data-i18n]').forEach(function (el) { el.textContent = t(el.getAttribute('data-i18n')); });
    $$('[data-i18n-ph]').forEach(function (el) { el.placeholder = t(el.getAttribute('data-i18n-ph')); });
    $$('[data-i18n-label]').forEach(function (el) {
      var s = t(el.getAttribute('data-i18n-label'));
      el.title = s; el.setAttribute('aria-label', s);
    });
    var h1 = $('.post-title');
    D.title = (h1 && h1.textContent ? h1.textContent + ' — ' : '') + t('site');
    langHooks.forEach(function (f) { try { f(); } catch (e) { console.error(e); } });
  }

  // ---------- тема ----------
  function effTheme() {
    var th = R.getAttribute('data-theme');
    if (th) return th;
    return window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  function toggleTheme() {
    var next = effTheme() === 'dark' ? 'light' : 'dark';
    R.setAttribute('data-theme', next);
    store.set('wp-theme', next);
  }

  // ---------- данные блога ----------
  function feed(label, max) {
    if (CFG.static) {
      return getJSON(CFG.base + 'feeds/' + enc(label || 'all') + '.json').then(function (j) { return (j.entries || []).slice(0, max || 150); });
    }
    var url = '/feeds/posts/default' + (label ? '/-/' + enc(label) : '') + '?alt=json&max-results=' + (max || 150);
    return getJSON(url).then(function (j) {
      return ((j.feed && j.feed.entry) || []).map(function (e) {
        var alt = (e.link || []).filter(function (l) { return l.rel === 'alternate'; })[0];
        return { title: e.title.$t, href: alt ? alt.href : '#', html: (e.content || e.summary || {}).$t || '' };
      });
    });
  }
  function itemFromA(a) {
    var d = a.dataset, ru = (a.getAttribute('data-ru') || a.textContent).trim();
    return {
      ru: ru, en: d.en || ru, href: a.getAttribute('href'), cc: d.cc, iso: d.iso, n: d.n ? +d.n : null,
      yt: d.yt, rt: d.rt, lat: d.lat ? +d.lat : null, lon: d.lon ? +d.lon : null,
      sub: d.c ? split2(d.c) : (d.k ? split2(d.k) : null), country: d.k ? split2(d.k) : null, cityHref: d.ch
    };
  }
  var indexP = null;
  // Индекс для поиска и «ближайших камер»: посты с меткой wp-index (список стран и список всех камер)
  function loadIndex() {
    if (!indexP) indexP = feed('wp-index', 60).then(function (es) {
      var idx = { countries: [], cams: [], cities: [] }, seen = {}, byIso = {};
      var docs = es.map(function (e) { return new DOMParser().parseFromString(e.html, 'text/html'); });
      // Сначала страны: по их коду камеры получают название страны
      docs.forEach(function (d) {
        var wp = $('.wp', d);
        if (!wp || wp.getAttribute('data-t') !== 'countries') return;
        $$('ul.az > li > a', d).forEach(function (a) {
          var it = itemFromA(a);
          it.type = 'country';
          idx.countries.push(it);
          if (it.cc) byIso[it.cc] = { ru: it.ru, en: it.en };
        });
      });
      docs.forEach(function (d) {
        var wp = $('.wp', d);
        if (!wp || wp.getAttribute('data-t') !== 'cams') return;
        $$('ul.az > li > a', d).forEach(function (a) {
          var it = itemFromA(a);
          it.type = 'cam';
          it.country = it.country || byIso[it.iso] || null;
          idx.cams.push(it);
          if (it.cityHref && !seen[it.cityHref]) {
            seen[it.cityHref] = 1;
            idx.cities.push({ type: 'city', ru: it.sub.ru, en: it.sub.en, href: it.cityHref, sub: it.country, iso: it.iso, lat: it.lat, lon: it.lon });
          }
        });
      });
      return idx;
    }).catch(function () { return { countries: [], cams: [], cities: [] }; });
    return indexP;
  }
  function ytFromHtml(html) {
    var m = /data-type="yt"[^>]*data-id="([\w-]{11})"|data-id="([\w-]{11})"[^>]*data-type="yt"/.exec(html);
    return m ? (m[1] || m[2]) : null;
  }
  function camTitle(title) {
    // «Эйфелева башня — Париж | Eiffel Tower — Paris» → короткие имена без города
    var p = split2(title);
    return { ru: p.ru.split(' — ')[0], en: p.en.split(' — ')[0] };
  }

  // ---------- алфавитный список ----------
  var ALPHA = { ru: 'АБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЭЮЯ', en: 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' };
  function letterOf(s) {
    var c = String(s || '').trim().charAt(0).toUpperCase();
    if (c === 'Ё') c = 'Е';
    if (!/[А-Я]/.test(c)) c = c.normalize('NFD').charAt(0);
    return ALPHA[lang].indexOf(c) >= 0 || /[A-Z]/.test(c) ? c : '#';
  }
  function thumb(it) {
    if (it.thumb) return it.thumb;
    if (it.yt) return 'https://i.ytimg.com/vi/' + it.yt + '/mqdefault.jpg';
    return it.rt ? 'https://rutube.ru/api/video/' + it.rt + '/thumbnail/?redirect=1' : null;
  }
  function rtFromHtml(html) {
    var m = /rutube\.ru\/play\/embed\/([0-9a-f]{32})/.exec(html);
    return m ? m[1] : null;
  }
  function card(it) {
    var src = thumb(it);
    return h('a', { class: 'card', href: it.href },
      h('span', { class: src ? 'th' : 'th noimg' },
        src ? h('img', { src: src, alt: '', loading: 'lazy', onerror: function () { this.parentNode.classList.add('noimg'); this.remove(); } }) : null,
        h('span', { class: 'live' }, 'LIVE')),
      h('span', { class: 'nm' }, nm(it),
        it.sub ? h('small', {}, nm(it.sub)) : null,
        it.km != null ? h('small', {}, Math.round(it.km) + ' ' + t('km')) : null));
  }
  function cardsGrid(items) { return h('div', { class: 'cards' }, items.map(card)); }

  function azList(host, items, opt) {
    opt = opt || {};
    var q = '';
    var wrap = h('div', { class: 'az-wrap' });
    var filter = items.length > 24 ? h('input', {
      type: 'search', class: 'az-filter', placeholder: t('filter'), 'data-i18n-ph': 'filter',
      oninput: function (e) { q = e.target.value; body(); }
    }) : null;
    var bar = h('div', { class: 'az-bar' });
    var list = h('div');
    add(wrap, [filter, bar, list]);
    host.replaceChildren(wrap);

    function li(it) {
      return h('li', {},
        it.cc ? h('img', { class: 'flag', src: 'https://flagcdn.com/w40/' + it.cc.toLowerCase() + '.png', alt: '', loading: 'lazy', width: 20, height: 15 }) : null,
        h('a', { href: it.href }, nm(it)),
        it.sub ? h('small', {}, ' · ' + nm(it.sub)) : null,
        it.n ? h('small', {}, ' · ' + t('camsN', { n: it.n })) : null);
    }
    function body() {
      var coll = new Intl.Collator(lang), qq = norm(q);
      var sorted = items.filter(function (it) { return !qq || norm(it.ru).indexOf(qq) >= 0 || norm(it.en).indexOf(qq) >= 0; })
        .sort(function (a, b) { return coll.compare(nm(a), nm(b)); });
      var groups = {}, order = [];
      sorted.forEach(function (it) {
        var L = letterOf(nm(it));
        if (!groups[L]) { groups[L] = []; order.push(L); }
        groups[L].push(it);
      });
      bar.replaceChildren();
      // В русском режиме латинские названия (города без русского имени) идут после кириллицы своими буквами
      var extra = order.filter(function (L) { return L !== '#' && ALPHA[lang].indexOf(L) < 0; }).sort();
      var letters = ALPHA[lang].split('').concat(extra, groups['#'] ? ['#'] : []);
      letters.forEach(function (L) {
        bar.append(h('button', {
          type: 'button', disabled: !groups[L],
          onclick: function () { var g = list.querySelector('[data-l="' + L + '"]'); if (g) g.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
        }, L));
      });
      bar.hidden = sorted.length < 12;
      list.replaceChildren();
      if (!sorted.length) { list.append(opt.empty ? opt.empty() : h('p', { class: 'stub' }, t('empty'))); return; }
      // Короткий список карточек не дробим на буквы — одна сетка по алфавиту
      if (opt.cards && sorted.length <= 12) { list.append(cardsGrid(sorted)); return; }
      letters.filter(function (L) { return groups[L]; }).forEach(function (L) {
        list.append(h('section', { class: 'az-g', 'data-l': L }, h('h3', {}, L),
          opt.cards ? cardsGrid(groups[L]) : h('ul', {}, groups[L].map(li))));
      });
    }
    body();
    langHooks.push(body);
    return {
      // Добавляет пункты, которых ещё нет (сравнение по адресу), и перерисовывает список
      add: function (more) {
        var have = {};
        items.forEach(function (it) { have[pathOf(it.href)] = 1; });
        var fresh = more.filter(function (it) { return !have[pathOf(it.href)]; });
        if (!fresh.length) return;
        items = items.concat(fresh);
        if (filter && items.length > 24) filter.hidden = false;
        body();
      }
    };
  }
  // Превращает <ul class="az"> из поста в алфавитный список. Закомментированные пункты (<!-- -->) не попадают в DOM.
  function azFromPost(ul, opt) {
    if (!ul) return null;
    var items = $$(':scope > li > a', ul).map(itemFromA);
    var host = h('div');
    ul.replaceWith(host);
    return azList(host, items, opt);
  }
  // Города, у которых появились камеры после генерации постов, берутся из индекса «Все камеры»
  function mergeIndexCities(list, keep) {
    if (!list) return;
    loadIndex().then(function (idx) { list.add(idx.cities.filter(keep)); });
  }

  // ---------- Викиданные, Викигид, Википедия ----------
  function linksOf(qid) {
    return getJSON(WD + '?action=wbgetentities&props=sitelinks&sitefilter=ruwiki|enwiki|ruwikivoyage|enwikivoyage&format=json&origin=*&ids=' + qid)
      .then(function (j) {
        var sl = (j.entities && j.entities[qid] && j.entities[qid].sitelinks) || {}, out = { qid: qid };
        Object.keys(sl).forEach(function (k) { out[k] = sl[k].title; });
        return out;
      });
  }
  // Статьи об объекте: по QID из поста, а если у него нет статей — по названию en-статьи, затем по GeoNames ID
  function sitelinks(src) {
    var useful = function (o) { return o && (o.ruwiki || o.enwiki); };
    var byQid = src.qid ? linksOf(src.qid) : Promise.resolve(null);
    return byQid.then(function (o) {
      if (useful(o) || !src.wen) return o;
      return getJSON(WD + '?action=wbgetentities&sites=enwiki&props=info&format=json&origin=*&titles=' + enc(src.wen))
        .then(function (j) {
          var q = Object.keys(j.entities || {}).filter(function (k) { return k.charAt(0) === 'Q'; })[0];
          return q ? linksOf(q) : o;
        });
    }).then(function (o) {
      if (useful(o) || !src.gid) return o;
      return getJSON(WD + '?action=query&list=search&srlimit=1&format=json&origin=*&srsearch=haswbstatement:P1566=' + src.gid)
        .then(function (j) { var s = j.query && j.query.search && j.query.search[0]; return s ? linksOf(s.title) : o; });
    }).then(function (o) { return o || {}; });
  }
  function extract(host, title) {
    return getJSON('https://' + host + '/w/api.php?action=query&prop=extracts&exintro=1&explaintext=1&redirects=1&format=json&origin=*&titles=' + enc(title))
      .then(function (j) {
        var pages = (j.query && j.query.pages) || {}, p = pages[Object.keys(pages)[0]];
        if (!p || !p.extract) return null;
        return { text: p.extract, title: p.title, url: 'https://' + host + '/wiki/' + enc(p.title.replace(/ /g, '_')) };
      });
  }
  // Убирает из выдержки ударения и пустые хвосты транскрипций: «(фр. France, МФА: )» → «(фр. France)»
  function cleanText(s) {
    return s.replace(/́/g, '')
      .replace(/[,;]?\s*(МФА|IPA|слушать|listen)\s*:?\s*(?=[)\]])/gi, '')
      .replace(/\(\s*[,;]?\s*\)/g, '')
      .replace(/[ \t]{2,}/g, ' ').replace(/\s+([,.;:])/g, '$1');
  }
  function hasOwnContent(el) {
    return Array.prototype.some.call(el.childNodes, function (n) {
      return n.nodeType === 1 || (n.nodeType === 3 && n.textContent.trim());
    });
  }
  // Описание: свой текст в посте важнее; иначе вступление Викигида, дополненное вступлением Википедии
  function descBlock(body, src) {
    var els = { ru: $('.wp-desc.ru', body), en: $('.wp-desc.en', body) };
    var done = {};
    function fill() {
      var L = lang, el = els[L];
      if (!el || done[L] || hasOwnContent(el) || !src) return;
      done[L] = 1;
      el.classList.add('desc');
      el.append(h('p', { class: 'ld' }, t('loading')));
      sitelinks(src).then(function (sl) {
        var tries = [];
        if (!src.noVoyage && sl[L + 'wikivoyage']) tries.push([L + '.wikivoyage.org', sl[L + 'wikivoyage'], 'wv']);
        if (sl[L + 'wiki']) tries.push([L + '.wikipedia.org', sl[L + 'wiki'], 'wp']);
        return Promise.all(tries.map(function (x) { return extract(x[0], x[1]).catch(function () { return null; }); }))
          .then(function (res) {
            el.replaceChildren();
            var left = 6, notes = [];
            res.forEach(function (r, i) {
              if (!r || left <= 0) return;
              var paras = cleanText(r.text).split(/\n+/).map(function (s) { return s.trim(); }).filter(Boolean).slice(0, left);
              if (!paras.length) return;
              left -= paras.length;
              paras.forEach(function (p) { el.append(h('p', {}, p)); });
              notes.push(h('a', { href: r.url, target: '_blank', rel: 'noopener' }, t('textSrc', { src: t(tries[i][2]), title: r.title })));
            });
            if (notes.length) el.append(h('p', { class: 'srcnote' }, notes.map(function (a, i) { return i ? [' · ', a] : a; })));
          });
      }).catch(function () { el.replaceChildren(); });
    }
    fill();
    langHooks.push(fill);
  }

  // ---------- слайд-шоу ----------
  function slideshow(el, slides) {
    var track = h('div', { class: 'ss-track' });
    var dots = h('div', { class: 'ss-dots' });
    slides.forEach(function (s, i) {
      track.append(h('figure', {},
        h('img', { src: s.src, alt: s.title || '', loading: i ? 'lazy' : 'eager', decoding: 'async' }),
        s.title ? h('figcaption', {},
          s.href ? h('a', { href: s.href, target: '_blank', rel: 'noopener' }, s.title) : s.title,
          s.credit ? h('small', {}, s.credit) : null) : null));
      dots.append(h('i'));
    });
    var box = h('div', { class: 'ss' }, track);
    var cur = 0, timer = null;
    function go(i) { cur = (i + slides.length) % slides.length; track.scrollTo({ left: track.clientWidth * cur }); mark(); }
    function mark() { $$('i', dots).forEach(function (d, i) { d.classList.toggle('on', i === cur); }); }
    if (slides.length > 1) {
      box.append(
        h('button', { class: 'ss-nav ss-prev', type: 'button', 'aria-label': '←', onclick: function () { go(cur - 1); } }, '‹'),
        h('button', { class: 'ss-nav ss-next', type: 'button', 'aria-label': '→', onclick: function () { go(cur + 1); } }, '›'),
        dots);
      track.addEventListener('scroll', function () {
        var i = Math.round(track.scrollLeft / Math.max(1, track.clientWidth));
        if (i !== cur) { cur = i; mark(); }
      }, { passive: true });
      var start = function () { stop(); timer = setInterval(function () { go(cur + 1); }, 6000); };
      var stop = function () { clearInterval(timer); };
      ['mouseenter', 'focusin', 'touchstart'].forEach(function (ev) { box.addEventListener(ev, stop, { passive: true }); });
      box.addEventListener('mouseleave', start);
      if (!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches)) start();
      mark();
    }
    el.replaceChildren(box);
  }
  var BAD = /станци|station|метро|metro|округ|district|arrondissement|район|коммун|commune|муниципал|municipal|провинци|province|регион|region|област|кантон|canton|компани|company|школ|school|больниц|hospital|улиц|street|аэропорт|airport|депутат|politician|футбол|football|список|list of/i;
  // Главные достопримечательности по Викиданным: объекты с фото в радиусе 8 км, известность — число языковых статей,
  // объекты культурного наследия получают надбавку. События, организации и населённые пункты отсекаются.
  var SKIP_TYPES = 'wd:Q515 wd:Q1549591 wd:Q5119 wd:Q15284 wd:Q2983893 wd:Q123705 wd:Q928830 wd:Q55488 wd:Q1248784 wd:Q3914 ' +
    'wd:Q16917 wd:Q4830453 wd:Q1093829 wd:Q7930989 wd:Q3957 wd:Q532 wd:Q484170 wd:Q747074 wd:Q5 wd:Q5503 wd:Q13406463';
  var SIGHT_BAD = /станци|station|аэропорт|airport|школ|school|больниц|hospital|компани|company|список|list of/i;
  function wdSights(L, lat, lon, cityQid) {
    var key = 'sights:' + L + ':' + lat + ':' + lon, c = cache.get(key);
    if (c) return Promise.resolve(c);
    var q = 'SELECT ?item ?label ?alt ?desc ?img ?sl ?her ?article WHERE { ' +
      'SERVICE wikibase:around { ?item wdt:P625 ?loc . bd:serviceParam wikibase:center "Point(' + lon + ' ' + lat + ')"^^geo:wktLiteral ; wikibase:radius "8" . } ' +
      '?item wdt:P18 ?img ; wikibase:sitelinks ?sl . FILTER(?sl >= 4) ' +
      'FILTER NOT EXISTS { ?item wdt:P585|wdt:P580|wdt:P576|wdt:P159|wdt:P1454|wdt:P1082 [] } ' +
      'FILTER NOT EXISTS { ?item wdt:P31 ?t . VALUES ?t { ' + SKIP_TYPES + ' } } ' +
      'OPTIONAL { ?item wdt:P1435 ?her } ' +
      'OPTIONAL { ?item rdfs:label ?label FILTER(lang(?label) = "' + L + '") } ' +
      'OPTIONAL { ?item rdfs:label ?alt FILTER(lang(?alt) = "en") } ' +
      'OPTIONAL { ?item schema:description ?desc FILTER(lang(?desc) = "' + L + '") } ' +
      'OPTIONAL { ?article schema:about ?item ; schema:isPartOf <https://' + L + '.wikipedia.org/> . } ' +
      '} ORDER BY DESC(?sl) LIMIT 40';
    return getJSON('https://query.wikidata.org/sparql?format=json&query=' + enc(q), { cache: false, timeout: 30000 }).then(function (j) {
      var seen = {}, out = [];
      j.results.bindings.forEach(function (b) {
        var id = b.item.value.split('/').pop();
        if (seen[id] || seen[b.img.value] || id === cityQid) return;
        seen[id] = seen[b.img.value] = 1;
        var label = (b.label || b.alt || {}).value, desc = (b.desc || {}).value || '';
        if (!label || SIGHT_BAD.test(label + ' ' + desc)) return;
        out.push({
          s: +b.sl.value + (b.her ? 25 : 0), src: b.img.value.replace(/^http:/, 'https:') + '?width=1280',
          title: label, href: b.article ? b.article.value : b.item.value, credit: desc
        });
      });
      out = out.sort(function (a, b) { return b.s - a.s; }).slice(0, CFG.maxSlides);
      cache.set(key, out);
      return out;
    });
  }
  // Запасной источник: места рядом с точкой из Википедии; просмотры API отдаёт порциями, поэтому дозапрашиваем продолжения
  function wikiGeo(L, lat, lon) {
    var base = 'https://' + L + '.wikipedia.org/w/api.php?action=query&format=json&origin=*&generator=geosearch&ggscoord=' +
      lat + '|' + lon + '&ggsradius=10000&ggslimit=50&prop=pageimages|pageviews|description&piprop=thumbnail|name&pithumbsize=1280&pilimit=50&pvipdays=30';
    var pages = {};
    function step(cont, n, retry) {
      var url = base + (cont ? '&' + Object.keys(cont).map(function (k) { return k + '=' + enc(cont[k]); }).join('&') : '');
      return getJSON(url, { cache: !cont && !retry }).then(function (j) {
        if (j.error) {
          if (retry) throw new Error(j.error.code);
          return new Promise(function (r) { setTimeout(r, 1500); }).then(function () { return step(cont, n, true); });
        }
        Object.keys((j.query && j.query.pages) || {}).forEach(function (k) {
          var p = j.query.pages[k], old = pages[k];
          if (!old) pages[k] = p;
          else if (p.pageviews) old.pageviews = Object.assign(old.pageviews || {}, p.pageviews);
        });
        if (j.continue && j.continue.pvipcontinue && n < 5) return step(j.continue, n + 1);
        return Object.keys(pages).map(function (k) { return pages[k]; });
      });
    }
    var key = 'geo:' + L + ':' + lat + ':' + lon, c = cache.get(key);
    if (c) return Promise.resolve(c);
    return step(null, 0).then(function (list) { cache.set(key, list); return list; });
  }
  function geoSlides(L, lat, lon, names) {
    return wikiGeo(L, lat, lon).then(function (all) {
      var city = norm(names[L] || names.ru);
      return all
        .filter(function (p) {
          return p.thumbnail && p.thumbnail.width >= 500 && !/\.svg$/i.test(p.pageimage || '') &&
            norm(p.title) !== city && !BAD.test((p.description || '') + ' ' + p.title);
        })
        .map(function (p) {
          var v = 0; Object.keys(p.pageviews || {}).forEach(function (d) { v += p.pageviews[d] || 0; });
          return {
            v: v, src: p.thumbnail.source, title: p.title.replace(/\s*\([^)]*\)$/, ''),
            href: 'https://' + L + '.wikipedia.org/wiki/' + enc(p.title.replace(/ /g, '_')), credit: p.description || ''
          };
        })
        .sort(function (a, b) { return b.v - a.v; }).slice(0, CFG.maxSlides);
    });
  }
  // Фото в посте (<img> в .wp-slides) важнее; подписи на английском — в data-en и data-credit-en
  function slidesBlock(el, lat, lon, names, qid) {
    if (!el) return;
    var heading = el.previousElementSibling;
    var own = $$('img', el);
    if (own.length) {
      var drawOwn = function () {
        slideshow(el, own.map(function (img) {
          var en = lang === 'en';
          return {
            src: img.getAttribute('src'), title: (en && img.getAttribute('data-en')) || img.alt,
            href: (en && img.getAttribute('data-href-en')) || img.getAttribute('data-href'),
            credit: (en && img.getAttribute('data-credit-en')) || img.getAttribute('data-credit')
          };
        }));
      };
      drawOwn(); langHooks.push(drawOwn);
      return;
    }
    if (isNaN(lat)) return;
    function hide() { el.hidden = true; if (heading && heading.tagName === 'H2') heading.hidden = true; }
    function load() {
      var L = lang;
      el.replaceChildren(h('p', { class: 'ld' }, t('loading')));
      wdSights(L, lat, lon, qid)
        .then(function (list) { return list.length >= 3 ? list : geoSlides(L, lat, lon, names); })
        .catch(function () { return geoSlides(L, lat, lon, names); })
        .then(function (list) {
          if (L !== lang) return;
          if (!list.length) { hide(); return; }
          slideshow(el, list);
          el.append(h('p', { class: 'srcnote' }, t('photoSrc')));
        }).catch(hide);
    }
    load();
    langHooks.push(load);
  }

  // ---------- время и погода ----------
  function weatherBlock(el, lat, lon, tz) {
    if (!el || isNaN(lat)) return;
    var clock = h('b'), wx = h('span'), dn = h('span', { class: 'dn', hidden: true }), sun = h('span');
    var data = null;
    el.className = 'wx';
    el.replaceChildren(h('span', {}, h('span', { 'data-i18n': 'localTime' }, t('localTime')), ': '), clock, dn, wx, sun);
    function fmt(d, o) {
      try { return new Intl.DateTimeFormat(lang, Object.assign({ timeZone: tz || undefined }, o)).format(d); }
      catch (e) { return new Intl.DateTimeFormat(lang, o).format(d); }
    }
    function tick() { clock.textContent = fmt(new Date(), { hour: '2-digit', minute: '2-digit', weekday: 'short' }); }
    function draw() {
      tick();
      if (!data || !data.current) return;
      var c = data.current, w = WMO.filter(function (x) { return x[0].indexOf(c.weather_code) >= 0; })[0];
      var temp = Math.round(c.temperature_2m);
      dn.hidden = false;
      dn.textContent = c.is_day ? '☀️ ' + t('day') : '🌙 ' + t('night');
      wx.textContent = (w ? w[1] + ' ' : '') + (temp > 0 ? '+' : '') + temp + ' °C' + (w ? ', ' + (lang === 'en' ? w[3] : w[2]) : '') +
        ' · ' + t('wind') + ' ' + Math.round(c.wind_speed_10m) + ' ' + t('ms');
      var dl = data.daily;
      sun.textContent = dl && dl.sunrise ? t('sunrise') + ' ' + dl.sunrise[0].slice(11, 16) + ' · ' + t('sunset') + ' ' + dl.sunset[0].slice(11, 16) : '';
    }
    tick();
    setInterval(tick, 20000);
    var bucket = Math.floor(Date.now() / 600000);
    getJSON('https://api.open-meteo.com/v1/forecast?latitude=' + lat + '&longitude=' + lon +
      '&current=temperature_2m,weather_code,is_day,wind_speed_10m&daily=sunrise,sunset&timezone=auto&forecast_days=1&wind_speed_unit=ms&_=' + bucket)
      .then(function (j) {
        data = j;
        if (!tz && j.timezone) tz = j.timezone;
        draw();
        el.append(h('span', { class: 'srcnote' }, h('a', { href: 'https://open-meteo.com/', target: '_blank', rel: 'noopener' }, t('wxSrc'))));
      }).catch(function () { /* погода необязательна */ });
    langHooks.push(draw);
  }

  // ---------- камеры города и ближайшие ----------
  function nearestInto(box, lat, lon, excludePaths) {
    if (isNaN(lat)) return;
    loadIndex().then(function (idx) {
      var list = idx.cams.filter(function (c) { return c.lat != null && excludePaths.indexOf(pathOf(c.href)) < 0; })
        .map(function (c) { return Object.assign({}, c, { km: dist(lat, lon, c.lat, c.lon) }); })
        .sort(function (a, b) { return a.km - b.km; }).slice(0, 6);
      if (list.length) box.append(h('h3', { 'data-i18n': 'nearest' }, t('nearest')), cardsGrid(list));
    });
    if (CFG.windyKey) windyInto(box, lat, lon);
  }
  function windyInto(box, lat, lon) {
    getJSON('https://api.windy.com/webcams/api/v3/webcams?nearby=' + lat + ',' + lon + ',50&limit=6&include=images,player,location&lang=' + lang,
      { headers: { 'X-WINDY-API-KEY': CFG.windyKey } })
      .then(function (j) {
        var ws = (j && j.webcams) || [];
        if (!ws.length) return;
        var grid = h('div', { class: 'cards' });
        ws.forEach(function (w) {
          var img = w.images && w.images.current && (w.images.current.preview || w.images.current.thumbnail);
          var url = w.player && (w.player.live || w.player.day);
          var c = h('a', { class: 'card', href: url || ('https://www.windy.com/webcams/' + w.webcamId), target: '_blank', rel: 'noopener' },
            h('span', { class: 'th' }, img ? h('img', { src: img, alt: '', loading: 'lazy' }) : null),
            h('span', { class: 'nm' }, w.title || '', w.location && w.location.city ? h('small', {}, w.location.city) : null));
          if (url) c.addEventListener('click', function (e) {
            e.preventDefault();
            $('.th', c).replaceChildren(h('iframe', { src: url, allowfullscreen: true, loading: 'lazy', style: 'position:absolute;inset:0;width:100%;height:100%;border:0' }));
          });
          grid.append(c);
        });
        box.append(h('h3', { 'data-i18n': 'windy' }, t('windy')), grid,
          h('p', { class: 'srcnote' }, h('a', { href: 'https://www.windy.com/webcams', target: '_blank', rel: 'noopener' }, 'Webcams provided by windy.com')));
      }).catch(function () { /* без ключа или лимит */ });
  }
  function camsBlock(el, label, lat, lon, opt) {
    if (!el) return;
    opt = opt || {};
    var here = location.pathname;
    el.replaceChildren(h('p', { class: 'ld' }, t('loading')));
    feed(label, 150).catch(function () { return []; }).then(function (es) {
      var items = es.filter(function (e) { return pathOf(e.href) !== here; }).map(function (e) {
        var n = camTitle(e.title);
        return { ru: n.ru, en: n.en, href: e.href, yt: ytFromHtml(e.html), rt: rtFromHtml(e.html) };
      });
      if (items.length) { azList(el, items, { cards: true }); return; }
      if (opt.hideEmpty) { el.replaceChildren(); var hd = el.previousElementSibling; if (hd && hd.tagName === 'H2') hd.hidden = true; return; }
      var box = h('div', {}, h('p', { class: 'stub', 'data-i18n': 'noCams' }, t('noCams')));
      el.replaceChildren(box);
      nearestInto(box, lat, lon, [here]);
    });
  }

  // ---------- карты (Leaflet и подложка грузятся только при первом открытии блока) ----------
  var leafletP = null;
  function loadLeaflet() {
    if (window.L && window.L.map) return Promise.resolve();
    if (!leafletP) {
      D.head.append(h('link', { rel: 'stylesheet', href: CFG.leafletCss }));
      leafletP = loadScript(CFG.leafletJs);
    }
    return leafletP;
  }
  // points() → Promise<[{lat, lon, ru, en, href, sub, n, approx}]>
  function mapBlock(before, key, points) {
    if (!before) return;
    var box = h('div', { class: 'map-box' });
    var det = h('details', { class: 'wp-map' },
      h('summary', {}, h('span', { class: 'ic', html: ICON.map }), h('span', { 'data-i18n': key }, t(key))), box);
    before.before(det);
    var started = false;
    det.addEventListener('toggle', function () {
      if (!det.open || started) return;
      started = true;
      function msg(el) { box.classList.add('msg'); box.replaceChildren(el); }
      msg(h('p', { class: 'ld' }, t('loading')));
      Promise.all([loadLeaflet(), points()]).then(function (r) {
        var pts = r[1].filter(function (p) { return p.lat != null && !isNaN(p.lat) && !isNaN(p.lon); });
        if (!pts.length) { msg(h('p', { class: 'stub' }, t('mapEmpty'))); return; }
        box.classList.remove('msg');
        box.replaceChildren();
        var map = L.map(box, { scrollWheelZoom: false, worldCopyJump: true });
        L.tileLayer(CFG.tiles, { maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>' }).addTo(map);
        var color = getComputedStyle(R).getPropertyValue('--pr').trim() || '#4353FF';
        pts.forEach(function (p) {
          L.circleMarker([p.lat, p.lon], {
            radius: p.n ? Math.min(16, 6 + Math.sqrt(p.n) * 1.6) : 7, color: '#fff', weight: 2,
            fillColor: color, fillOpacity: p.approx ? 0.45 : 0.95, dashArray: p.approx ? '3 3' : null
          }).addTo(map).bindPopup(function () {
            if (p.many) return h('div', { class: 'map-pop' }, h('a', { href: p.href }, t('approxMany', { n: p.n })));
            return h('div', { class: 'map-pop' }, h('a', { href: p.href }, nm(p)),
              p.n ? h('small', {}, t('camsN', { n: p.n })) : null, p.approx ? h('small', {}, t('approx')) : null);
          });
        });
        var b = pts.map(function (p) { return [p.lat, p.lon]; });
        if (b.length === 1) map.setView(b[0], 12); else map.fitBounds(b, { padding: [28, 28], maxZoom: 15 });
        // колесо мыши масштабирует карту только после щелчка по ней, чтобы не мешать прокрутке страницы
        map.once('click', function () { map.scrollWheelZoom.enable(); });
        setTimeout(function () { map.invalidateSize(); }, 60);
      }).catch(function () { started = false; leafletP = null; msg(h('p', { class: 'stub' }, t('mapFail'))); });
    });
  }
  // Страна: города с камерами — из поста (координаты в строках списка) и из индекса (города новых камер)
  function countryPoints(fromPost, cc) {
    return function () {
      return loadIndex().then(function (idx) {
        var byPath = {}, out = [];
        fromPost.forEach(function (c) { var p = Object.assign({}, c); byPath[pathOf(c.href)] = p; out.push(p); });
        var groups = {};
        idx.cams.forEach(function (c) {
          if (c.iso !== cc || !c.cityHref || c.lat == null) return;
          var k = pathOf(c.cityHref);
          (groups[k] = groups[k] || []).push(c);
        });
        Object.keys(groups).forEach(function (k) {
          var g = groups[k], p = byPath[k];
          if (p) { p.n = Math.max(p.n || 0, g.length); return; }
          out.push({ ru: g[0].sub.ru, en: g[0].sub.en, href: g[0].cityHref, n: g.length,
            lat: g.reduce(function (s, c) { return s + c.lat; }, 0) / g.length,
            lon: g.reduce(function (s, c) { return s + c.lon; }, 0) / g.length });
        });
        return out;
      });
    };
  }
  // Город: камеры из индекса. У камер без своих координат стоит центр города: если их немного, раскладываем
  // их колечком вокруг центра, если много — одна метка «ещё N камер», иначе круг из точек расползается на море и окраины
  function cityPoints(lat, lon) {
    return function () {
      return loadIndex().then(function (idx) {
        var here = location.pathname, exact = [], approx = [];
        idx.cams.forEach(function (c) {
          if (!c.cityHref || pathOf(c.cityHref) !== here) return;
          var near = c.lat == null || (Math.abs(c.lat - lat) < 2e-4 && Math.abs(c.lon - lon) < 2e-4);
          (near ? approx : exact).push(Object.assign({}, c));
        });
        if (approx.length > 12) return exact.concat({ lat: lat, lon: lon, n: approx.length, many: true, approx: true, href: '#cams' });
        return exact.concat(approx.map(function (c, k) {
          var a = k * 2.4, r = 0.0012 * Math.sqrt(k + 1);
          return Object.assign(c, { approx: true, lat: lat + r * Math.sin(a), lon: lon + r * Math.cos(a) / Math.cos(lat * Math.PI / 180) });
        }));
      });
    };
  }

  // ---------- плеер ----------
  var ytReady = null;
  function loadYT() {
    if (window.YT && window.YT.Player) return Promise.resolve();
    if (!ytReady) ytReady = new Promise(function (res, rej) {
      var prev = window.onYouTubeIframeAPIReady;
      window.onYouTubeIframeAPIReady = function () { if (prev) prev(); res(); };
      loadScript('https://www.youtube.com/iframe_api').catch(rej);
      setTimeout(function () { rej(new Error('YouTube API timeout')); }, 15000);
    });
    return ytReady;
  }
  function player(box, links) {
    if (!box || !links.length) return;
    var srcs = links.map(function (a) {
      // innerText учитывает скрытые .ru/.en, поэтому подпись будет на текущем языке
      return { a: a, type: a.getAttribute('data-type') || 'link', id: a.getAttribute('data-id'), src: a.getAttribute('data-src') || a.href, label: (a.innerText || a.textContent).trim() };
    });
    var cur = -1, yt = null, hls = null, hint = null, refresh = null;
    box.classList.add('player');
    links.forEach(function (a, i) {
      a.addEventListener('click', function (e) {
        if (srcs[i].type === 'link') return;
        e.preventDefault();
        play(i);
        box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      });
    });
    function msg(text, ms) {
      var m = h('div', { class: 'msg', role: 'status' }, text);
      box.append(m);
      if (ms) setTimeout(function () { m.remove(); }, ms);
    }
    function clear() {
      clearTimeout(hint); clearInterval(refresh);
      if (yt && yt.destroy) { try { yt.destroy(); } catch (e) { /* уже удалён */ } }
      if (hls) { hls.destroy(); }
      yt = hls = null;
      box.replaceChildren();
    }
    function fail(i) {
      if (i !== cur) return;
      var next = -1;
      for (var k = i + 1; k < srcs.length; k++) if (srcs[k].type !== 'link') { next = k; break; }
      if (next < 0) { msg(t('allFailed')); return; }
      play(next);
      msg(t('switching'), 5000);
    }
    function facade(i) {
      var s = srcs[i];
      var bg = s.type === 'yt' && s.id ? 'url(https://i.ytimg.com/vi/' + s.id + '/hqdefault.jpg)' : 'none';
      box.replaceChildren(h('button', {
        type: 'button', class: 'facade', style: 'background-image:' + bg, 'aria-label': t('watch'),
        onclick: function () { play(i); }
      }, h('span', { class: 'pl', html: ICON.play }), h('span', { class: 'live', style: 'left:12px;top:12px' }, 'LIVE'), h('span', { class: 'cap' }, s.label)));
      mark(i);
    }
    function mark(i) { links.forEach(function (a, k) { a.classList.toggle('on', k === i); }); }
    function play(i) {
      clear();
      cur = i;
      mark(i);
      var s = srcs[i], stage = h('div', { class: 'stage' });
      box.append(stage);
      var playing = false;
      hint = setTimeout(function () { if (!playing && cur === i) msg(t('notLoading'), 9000); }, 20000);
      if (s.type === 'yt' || s.type === 'ytc') {
        loadYT().then(function () {
          if (cur !== i) return;
          var ev = {
            onError: function () { fail(i); },
            onStateChange: function (e) { if (e.data === 1 || e.data === 3) playing = true; }
          };
          if (s.type === 'yt') {
            yt = new YT.Player(stage.appendChild(h('div')), {
              host: 'https://www.youtube-nocookie.com', videoId: s.id,
              playerVars: { autoplay: 1, mute: 1, playsinline: 1, rel: 0 }, events: ev
            });
          } else {
            var fr = h('iframe', {
              src: 'https://www.youtube.com/embed/live_stream?channel=' + enc(s.id) + '&autoplay=1&mute=1&playsinline=1&enablejsapi=1&origin=' + enc(location.origin),
              allow: 'autoplay; fullscreen; picture-in-picture; encrypted-media', allowfullscreen: true
            });
            stage.append(fr);
            yt = new YT.Player(fr, { events: ev });
          }
        }).catch(function () { fail(i); });
      } else if (s.type === 'hls') {
        var v = h('video', { controls: true, autoplay: true, muted: true, playsinline: true });
        v.muted = true;
        v.addEventListener('playing', function () { playing = true; });
        stage.append(v);
        // hls.js везде, где есть MSE: новые Chromium отвечают canPlayType «maybe», но сами играют HLS не всегда.
        // Встроенный плеер — только без MSE (iPhone, где Safari играет HLS сам)
        var native = function () {
          if (!v.canPlayType('application/vnd.apple.mpegurl')) { fail(i); return; }
          v.src = s.src;
          v.addEventListener('error', function () { fail(i); });
        };
        var mse = window.MediaSource || window.ManagedMediaSource;
        if (!mse) native();
        else (window.Hls ? Promise.resolve() : loadScript(CFG.hlsUrl)).then(function () {
          if (cur !== i) return;
          if (!window.Hls || !Hls.isSupported()) { native(); return; }
          hls = new Hls();
          hls.on(Hls.Events.ERROR, function (e, d) { if (d && d.fatal) fail(i); });
          hls.loadSource(s.src);
          hls.attachMedia(v);
        }).catch(native);
        var p = v.play();
        if (p && p.catch) p.catch(function () { /* автозапуск запрещён — есть кнопка в плеере */ });
      } else if (s.type === 'img') {
        var img = h('img', { class: 'still', src: s.src, alt: s.label, onload: function () { playing = true; }, onerror: function () { fail(i); } });
        stage.append(img);
        var sec = +(s.a.getAttribute('data-refresh') || 30);
        refresh = setInterval(function () { img.src = s.src + (s.src.indexOf('?') < 0 ? '?' : '&') + '_=' + Date.now(); }, sec * 1000);
      } else {
        stage.append(h('iframe', {
          src: s.src, allow: 'autoplay; fullscreen; picture-in-picture', allowfullscreen: true,
          referrerpolicy: 'strict-origin-when-cross-origin', onload: function () { playing = true; }
        }));
      }
    }
    var first = 0;
    while (first < srcs.length && srcs[first].type === 'link') first++;
    if (first < srcs.length) { facade(first); return; }
    // Только внешние ссылки: владелец не разрешает встраивание, смотреть можно на его сайте
    box.replaceChildren(h('a', { class: 'facade', href: srcs[0].src, target: '_blank', rel: 'noopener nofollow' },
      h('span', { class: 'pl', html: ICON.play }), h('span', { class: 'cap', 'data-i18n': 'onSource' }, t('onSource'))));
  }

  // ---------- избранное и «поделиться» ----------
  function favs() { return store.get('wp-fav', []); }
  function itemTools(kind) {
    var h1 = $('.post-title');
    if (!h1) return;
    var me = { u: location.pathname, t: h1.getAttribute('data-title') || h1.textContent, k: kind };
    var fb = h('button', { type: 'button', class: 'btn', html: ICON.star }), fl = h('span');
    fb.append(fl);
    function sync() {
      var on = favs().some(function (f) { return f.u === me.u; });
      fb.classList.toggle('on', on);
      fl.textContent = t(on ? 'inFav' : 'addFav');
    }
    fb.addEventListener('click', function () {
      var list = favs(), i = -1;
      list.forEach(function (f, k) { if (f.u === me.u) i = k; });
      if (i >= 0) list.splice(i, 1); else list.unshift(me);
      store.set('wp-fav', list.slice(0, 200));
      sync(); renderFavs();
    });
    var sb = h('button', { type: 'button', class: 'btn', html: ICON.share });
    sb.append(h('span', { 'data-i18n': 'share' }, t('share')));
    var bar = h('div', { class: 'itools' }, fb, sb);
    var shareBox = null;
    sb.addEventListener('click', function () {
      var url = location.origin + location.pathname + (lang === 'en' ? '?lang=en' : '');
      var title = h1.textContent;
      if (navigator.share) { navigator.share({ title: title, url: url }).catch(function () { /* закрыто */ }); return; }
      if (shareBox) { shareBox.remove(); shareBox = null; return; }
      var u = enc(url), tt = enc(title);
      shareBox = h('div', { class: 'sharebox' },
        h('a', { class: 'btn', href: 'https://t.me/share/url?url=' + u + '&text=' + tt, target: '_blank', rel: 'noopener' }, 'Telegram'),
        h('a', { class: 'btn', href: 'https://vk.com/share.php?url=' + u + '&title=' + tt, target: '_blank', rel: 'noopener' }, 'VK'),
        h('a', { class: 'btn', href: 'https://wa.me/?text=' + tt + '%20' + u, target: '_blank', rel: 'noopener' }, 'WhatsApp'),
        h('button', {
          type: 'button', class: 'btn', onclick: function () {
            (navigator.clipboard ? navigator.clipboard.writeText(url) : Promise.reject()).then(function () { toast(t('copied')); }, function () { prompt(t('copy'), url); });
          }
        }, t('copy')));
      bar.after(shareBox);
    });
    h1.after(bar);
    sync();
    langHooks.push(sync);
  }
  function renderFavs() {
    var ul = $('#wp-favs');
    if (!ul) return;
    var list = favs();
    ul.replaceChildren();
    if (!list.length) { ul.append(h('li', { class: 'empty' }, t('favEmpty'))); return; }
    list.forEach(function (f, i) {
      ul.append(h('li', {}, h('a', { href: f.u }, pick(f.t)), ' ',
        h('button', {
          type: 'button', class: 'x', 'aria-label': '×', onclick: function () {
            var l = favs(); l.splice(i, 1); store.set('wp-fav', l); renderFavs();
          }
        }, '×')));
    });
  }

  // ---------- меню и поиск ----------
  function initMenu() {
    var side = $('.side');
    if (!side) return;
    var burger = $('.burger', side);
    if (burger) burger.addEventListener('click', function () { side.classList.toggle('open'); });
    var here = location.pathname;
    $$('.menu-nav a', side).forEach(function (a) { if (pathOf(a.href) === here) a.classList.add('on'); });
    var tb = $('#wp-theme'), lb = $('#wp-lang'), fb = $('#wp-fav-btn'), fl = $('#wp-favs');
    if (tb) tb.addEventListener('click', toggleTheme);
    if (lb) {
      var syncL = function () { lb.textContent = lang === 'en' ? 'RU' : 'EN'; };
      lb.addEventListener('click', function () { setLang(lang === 'en' ? 'ru' : 'en'); });
      langHooks.push(syncL); syncL();
    }
    if (fb && fl) fb.addEventListener('click', function () { fl.hidden = !fl.hidden; if (!fl.hidden) renderFavs(); });
    langHooks.push(renderFavs);
    $$('.search input').forEach(initSearch);
  }
  function searchUrl(q) { return (CFG.static ? CFG.base + 'search.html' : '/search') + '?q=' + enc(q); }
  function initSearch(input) {
    var box = h('ul', { class: 'sugg', role: 'listbox', hidden: true });
    input.parentNode.append(box);
    var res = [], sel = -1;
    function score(it, q) {
      var best = 0;
      [it.ru, it.en].forEach(function (n) {
        var s = norm(n);
        if (s.indexOf(q) === 0) best = Math.max(best, 3);
        else if (s.indexOf(' ' + q) >= 0 || s.indexOf('-' + q) >= 0) best = Math.max(best, 2);
        else if (s.indexOf(q) >= 0) best = Math.max(best, 1);
      });
      return best ? best + ({ country: 0.3, city: 0.2, cam: 0 })[it.type] : 0;
    }
    function hide() { box.hidden = true; sel = -1; }
    function draw(q) {
      box.replaceChildren();
      res.forEach(function (it, i) {
        var kind = it.type === 'country' ? t('countries') : it.type === 'city' ? (it.sub ? nm(it.sub) : t('cities')) : (it.sub ? nm(it.sub) : '');
        box.append(h('li', { role: 'option' }, h('a', { href: it.href, class: i === sel ? 'sel' : '' }, nm(it), h('small', {}, kind))));
      });
      box.append(h('li', {}, h('a', { class: 'all', href: searchUrl(q) }, t('searchAll', { q: q }))));
      box.hidden = false;
    }
    input.addEventListener('focus', loadIndex);
    input.addEventListener('input', function () {
      var raw = input.value.trim(), q = norm(raw);
      if (q.length < 2) { hide(); return; }
      loadIndex().then(function (idx) {
        if (input.value.trim() !== raw) return;
        var coll = new Intl.Collator(lang);
        res = idx.countries.concat(idx.cities, idx.cams)
          .map(function (it) { return { it: it, s: score(it, q) }; })
          .filter(function (x) { return x.s > 0; })
          .sort(function (a, b) { return b.s - a.s || coll.compare(nm(a.it), nm(b.it)); })
          .slice(0, 8).map(function (x) { return x.it; });
        sel = -1;
        draw(raw);
      });
    });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        sel = Math.max(-1, Math.min(res.length - 1, sel + (e.key === 'ArrowDown' ? 1 : -1)));
        draw(input.value.trim());
      } else if (e.key === 'Enter') {
        e.preventDefault();
        location.href = sel >= 0 ? res[sel].href : searchUrl(input.value.trim());
      } else if (e.key === 'Escape') hide();
    });
    input.addEventListener('blur', function () { setTimeout(hide, 180); });
  }

  // ---------- типы страниц ----------
  function popularLinks(sel) {
    var w = $(sel), seen = {}, out = [];
    if (!w) return out;
    $$('a[href]', w).forEach(function (a) {
      var p = pathOf(a.href);
      if (!/\/\d{4}\/\d{2}\/[^/]+\.html$/.test(p) || seen[p]) return;
      var title = a.getAttribute('data-title') || a.textContent.trim();
      if (!title) return;
      seen[p] = 1;
      out.push({ href: a.href, title: title });
    });
    return out;
  }
  function topList(items) {
    var ol = h('ol', { class: 'toplist' });
    function draw() {
      ol.replaceChildren();
      items.forEach(function (x) { ol.append(h('li', {}, h('a', { href: x.href }, pick(x.title)))); });
    }
    draw(); langHooks.push(draw);
    return ol;
  }
  var PAGES = {
    home: function (wp) {
      var stats = h('div', { class: 'stats' });
      var pop = popularLinks('#PopularPosts2').slice(0, 10);
      var fresh = h('div', {}, h('p', { class: 'ld' }, t('loading')));
      var search = $('.side .search');
      // add() пропускает null и раскрывает массивы; replaceChildren превратил бы их в текст «null» и «[object …]»
      wp.replaceChildren();
      add(wp, [
        h('h1', { 'data-i18n': 'site' }, t('site')),
        h('p', { class: 'lead', 'data-i18n': 'homeLead' }, t('homeLead')),
        search ? h('div', { class: 'search', style: 'max-width:560px;margin:14px 0', html: search.innerHTML.replace(/id="[^"]*"/g, '') }) : null,
        stats,
        pop.length ? [h('h2', { 'data-i18n': 'popular' }, t('popular')), topList(pop)] : null,
        h('h2', { 'data-i18n': 'newCams' }, t('newCams')), fresh]);
      $$('.search input', wp).forEach(initSearch);
      loadIndex().then(function (idx) {
        function s() {
          stats.replaceChildren(h('span', {}, t('statC', { n: idx.countries.length })), h('span', {}, t('statK', { n: idx.cams.length })));
        }
        s(); langHooks.push(s);
      });
      feed('cam', 12).then(function (es) {
        var items = es.map(function (e) { var n = camTitle(e.title); return { ru: n.ru, en: n.en, href: e.href, yt: ytFromHtml(e.html), sub: { ru: split2(e.title).ru.split(' — ')[1] || '', en: split2(e.title).en.split(' — ')[1] || '' } }; });
        function draw() { fresh.replaceChildren(items.length ? cardsGrid(items) : h('p', { class: 'stub' }, t('noCams'))); }
        draw(); langHooks.push(draw);
      }).catch(function () { fresh.replaceChildren(); });
    },
    countries: function (wp, body) { azFromPost($('ul.az', body)); },
    cities: function (wp, body) {
      var list = azFromPost($('ul.az', body));
      if (wp.getAttribute('data-all')) mergeIndexCities(list, function () { return true; });
    },
    cams: function (wp, body) {
      // Индекс камер разбит на несколько постов: показываем все части сразу
      var list = azFromPost($('ul.az', body), { cards: true });
      if (list) loadIndex().then(function (idx) { list.add(idx.cams); });
    },
    // Страница результатов поиска статичного сайта (в Blogger ищет сам Blogger): страны, города и камеры из индекса
    search: function (wp) {
      var q = new URLSearchParams(location.search).get('q') || '', nq = norm(q.trim());
      $$('.search input').forEach(function (i) { i.value = q; });
      var box = h('div', {}, h('p', { class: 'ld' }, t('loading')));
      wp.after(box);
      function links(items) {
        return h('ul', { class: 'plist' }, items.map(function (it) {
          return h('li', {}, h('a', { href: it.href }, nm(it)), it.sub ? h('small', {}, ' · ' + nm(it.sub)) : null);
        }));
      }
      loadIndex().then(function (idx) {
        function draw() {
          if (nq.length < 2) { box.replaceChildren(h('p', { class: 'stub' }, t('searchHint'))); return; }
          var hit = function (it) { return norm(it.ru).indexOf(nq) >= 0 || norm(it.en).indexOf(nq) >= 0; };
          var co = idx.countries.filter(hit), ci = idx.cities.filter(hit), ca = idx.cams.filter(hit).slice(0, 120);
          box.replaceChildren();
          if (!co.length && !ci.length && !ca.length) { box.append(h('p', { class: 'stub' }, t('nothingFound', { q: q }))); return; }
          if (co.length) box.append(h('h2', {}, t('countries')), links(co));
          if (ci.length) box.append(h('h2', {}, t('cities')), links(ci));
          if (ca.length) box.append(h('h2', {}, t('allCams')), cardsGrid(ca));
        }
        draw(); langHooks.push(draw);
      });
    },
    top: function (wp, body) {
      var week = popularLinks('#PopularPosts1'), all = popularLinks('#PopularPosts2');
      var box = h('div');
      if (!week.length && !all.length) box.append(h('p', { class: 'stub', 'data-i18n': 'noTop' }, t('noTop')));
      if (week.length) box.append(h('h2', { 'data-i18n': 'weekTop' }, t('weekTop')), topList(week));
      if (all.length) box.append(h('h2', { 'data-i18n': 'allTop' }, t('allTop')), topList(all));
      wp.after(box);
    },
    country: function (wp, body) {
      var d = wp.dataset, ul = $('ul.az', body);
      descBlock(body, { qid: d.qid });
      var geo = ul ? $$(':scope > li > a[data-lat]', ul).map(itemFromA) : [];
      if (geo.length) mapBlock(ul, 'mapCities', countryPoints(geo, d.cc));
      var list = azFromPost(ul, {
        empty: function () {
          var box = h('div', {}, h('p', { class: 'stub', 'data-i18n': 'noCams' }, t('noCams')));
          nearestInto(box, +d.lat, +d.lon, []);
          return box;
        }
      });
      mergeIndexCities(list, function (c) { return c.iso === d.cc; });
      itemTools('country');
    },
    city: function (wp, body) {
      var d = wp.dataset, lat = +d.lat, lon = +d.lon;
      weatherBlock($('.wp-wx', body), lat, lon, d.tz);
      slidesBlock($('.wp-slides', body), lat, lon, { ru: d.ru, en: d.en }, d.qid);
      descBlock(body, { qid: d.qid, wen: d.wen, gid: d.gid });
      var camsEl = $('.wp-cams', body);
      if (camsEl) camsEl.id = 'cams';  // сюда ведёт метка «ещё N камер» на карте
      if (+d.n) mapBlock(camsEl, 'mapCams', cityPoints(lat, lon));  // data-n — камер в городе при сборке
      camsBlock($('.wp-cams', body), d.slug, lat, lon);
      itemTools('city');
    },
    cam: function (wp, body) {
      var d = wp.dataset, lat = +d.lat, lon = +d.lon;
      player($('.wp-player', body), $$('.wp-src a', body));
      weatherBlock($('.wp-wx', body), lat, lon, d.tz);
      if (d.wen || d.wru) descBlock(body, { wen: d.wen, noVoyage: true });
      camsBlock($('.wp-more', body), d.city, lat, lon, { hideEmpty: true });
      itemTools('cam');
    }
  };

  // ---------- запасной вывод страницы ----------
  // Blogger иногда не выводит виджеты темы (так бывает после смены темы, когда у блога остались виджеты старой).
  // Тогда меню и пост скрипт достраивает сам: меню встроено при сборке темы, пост берётся из ленты блога по его номеру.
  var MENU_HTML = /*@@MENU_JS@@*/'';
  function rescueMenu() {
    var sec = $('#menu');
    if (sec && MENU_HTML && !$('a', sec)) sec.innerHTML = MENU_HTML;
  }
  function rescuePost() {
    if ($('.wp[data-t]') || $('.post')) return Promise.resolve();
    var host = $('#main') || $('main');
    if (!host) return Promise.resolve();
    var src = R.innerHTML, path = location.pathname;
    var type = (/'pageType': '(\w+)'/.exec(src) || [])[1];
    var link = $('link[rel="alternate"][href$="/comments/default"]');
    var id = (link && /\/feeds\/(\d+)\/comments\/default/.exec(link.href) || [])[1] || (/'postId': '(\d+)'/.exec(src) || [])[1];
    function entryHtml(e) {
      return h('article', { class: 'post' },
        h('h1', { class: 'post-title', 'data-title': e.title.$t }, e.title.$t),
        h('div', { class: 'post-body', html: (e.content || e.summary || {}).$t || '' }));
    }
    if (path === '/' || type === 'index' && !/\/search/.test(path)) {
      host.append(h('div', { class: 'post' }, h('div', { class: 'wp', 'data-t': 'home' })));
      return Promise.resolve();
    }
    if (id && (type === 'item' || type === 'static_page' || !type)) {
      return getJSON('/feeds/' + (type === 'static_page' ? 'pages' : 'posts') + '/default/' + id + '?alt=json', { cache: false })
        .then(function (j) { host.append(entryHtml(j.entry)); })
        .catch(function () { host.append(h('div', { class: 'post' }, h('h1', { 'data-i18n': 'notFound' }, t('notFound')))); });
    }
    if (/^\/search/.test(path)) {
      var label = /^\/search\/label\/([^/?]+)/.exec(path), q = new URLSearchParams(location.search).get('q');
      var url = '/feeds/posts/default' + (label ? '/-/' + label[1] : '') + '?alt=json&max-results=50' + (q ? '&q=' + enc(q) : '');
      var ul = h('ul', { class: 'plist' });
      host.append(h('div', { class: 'post' }, h('h1', {}, label ? decodeURIComponent(label[1]) : (q || '')), ul));
      return getJSON(url).then(function (j) {
        ((j.feed && j.feed.entry) || []).forEach(function (e) {
          var alt = (e.link || []).filter(function (l) { return l.rel === 'alternate'; })[0];
          ul.append(h('li', {}, h('a', { href: alt ? alt.href : '#', 'data-title': e.title.$t }, e.title.$t)));
        });
      }).catch(function () { /* пустой список */ });
    }
    host.append(h('div', { class: 'post' }, h('h1', { 'data-i18n': 'notFound' }, t('notFound'))));
    return Promise.resolve();
  }

  function boot() {
    rescueMenu();
    initMenu();
    rescuePost().then(start, start);
  }
  function start() {
    var crumbs = $('.post-body > .crumbs'), title = $('.post-title');
    if (crumbs && title) title.before(crumbs);
    var wp = $('.post-body .wp[data-t]') || $('.wp[data-t]');
    if (wp && PAGES[wp.getAttribute('data-t')]) {
      try { PAGES[wp.getAttribute('data-t')](wp, wp.closest('.post-body') || wp.parentNode); }
      catch (e) { console.error(e); }
    }
    applyLang();
  }
  if (D.readyState === 'loading') D.addEventListener('DOMContentLoaded', boot); else boot();
})();
