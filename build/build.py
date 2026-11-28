#!/usr/bin/env python3
"""Morocco Adventures Tour — rebuild the static pages from the files in content/.

Pages CMS (app.pagescms.org) edits the JSON files in content/. This script turns them
into the HTML pages GitHub Pages serves. It runs automatically on GitHub (see
.github/workflows/build-site.yml); you can also run it by hand:  python3 build/build.py

It never touches CNAME, the design files (style.css) or pages it does not manage.
"""
import glob
import html
import json
import os
import re
import sys
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
L3 = ('en', 'es', 'it')
STATE_FILE = 'build/applied.json'
log = []


# ------------------------------------------------------------------ helpers
def read(p):
    with open(p, encoding='utf-8') as f:
        return f.read()


def write(p, s):
    if p.endswith(('.html', '.js')) and 'apply_photos' in globals():
        s = apply_photos(s)
    old = read(p) if os.path.exists(p) else None
    if old != s:
        with open(p, 'w', encoding='utf-8') as f:
            f.write(s)
        log.append(p)


def load_json(p):
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def esc(s):
    return html.escape(str(s or ''), quote=True)


def tr(v):
    """Normalise a translatable value to {en, es, it}; empty languages fall back to English."""
    if isinstance(v, dict):
        en = (v.get('en') or '').strip()
        return {L: ((v.get(L) or '').strip() or en) for L in L3}
    s = (v or '').strip() if isinstance(v, str) else ''
    return {L: s for L in L3}


def has(v):
    return bool(tr(v)['en'])


def tattr(v):
    """data-t attribute (read by script.js to switch language) + English text."""
    t = tr(v)
    return f' data-t="{esc(json.dumps(t, ensure_ascii=False))}"'


def t_el(tag, v, cls='', extra=''):
    c = f' class="{cls}"' if cls else ''
    return f'<{tag}{c}{extra}{tattr(v)}>{esc(tr(v)["en"])}</{tag}>'


def img_path(p):
    p = (p or '').strip().lstrip('/')
    if p and not p.startswith('images/'):
        p = 'images/' + p
    return p


def lst(v):
    return v if isinstance(v, list) else []


def find_block_end(s, start):
    """Given the index of an opening <div ...> tag, return the index just after its matching </div>."""
    depth = 0
    for m in re.finditer(r'<div\b|</div>', s[start:]):
        depth += 1 if m.group(0) == '<div' else -1
        if depth == 0:
            return start + m.end()
    raise ValueError('unbalanced div')


def replace_div_inner(s, open_tag_regex, inner, nth=0):
    """Replace the content of the nth <div> whose opening tag matches the regex."""
    ms = list(re.finditer(open_tag_regex, s))
    if len(ms) <= nth:
        return s, False
    m = ms[nth]
    a = m.start()
    b = find_block_end(s, a)
    open_end = s.index('>', a) + 1
    indent = re.search(r'([ \t]*)$', s[:a]).group(1)
    return s[:open_end] + '\n' + inner + indent + '</div>' + s[b:], True


# ------------------------------------------------------------------ images
try:
    from PIL import Image  # installed by the GitHub workflow; optional when run by hand
except Exception:
    Image = None


def prepare_image(p):
    """Shrink very large uploads and make the 760px thumbnail used on cards."""
    if not p or not os.path.exists(p):
        if p:
            print('  ! image not found:', p)
        return
    thumb = 'images/thumbs/' + os.path.basename(p)
    if Image is None:
        return
    try:
        im = Image.open(p)
        if im.width > 1800 or os.path.getsize(p) > 900_000:
            im2 = im.convert('RGB') if p.lower().endswith(('.jpg', '.jpeg')) else im
            if im2.width > 1600:
                im2 = im2.resize((1600, round(im2.height * 1600 / im2.width)), Image.LANCZOS)
            kw = {'quality': 80, 'optimize': True, 'progressive': True} if p.lower().endswith(('.jpg', '.jpeg')) else {'optimize': True}
            im2.save(p, **kw)
            log.append(p)
            im = Image.open(p)
        if not os.path.exists(thumb):
            os.makedirs('images/thumbs', exist_ok=True)
            t = im.convert('RGB') if thumb.lower().endswith(('.jpg', '.jpeg')) else im
            if t.width > 760:
                t = t.resize((760, round(t.height * 760 / t.width)), Image.LANCZOS)
            kw = {'quality': 76, 'optimize': True} if thumb.lower().endswith(('.jpg', '.jpeg')) else {'optimize': True}
            t.save(thumb, **kw)
            log.append(thumb)
    except Exception as e:
        print('  ! could not process image', p, e)


def thumb(p):
    t = 'images/thumbs/' + os.path.basename(p)
    return t if os.path.exists(t) else p


# ------------------------------------------------------------------ content
S = load_json('content/settings.json')
HOME = load_json('content/home.json')
WA = re.sub(r'\D', '', S.get('whatsapp', ''))


def load_collection(folder):
    items = []
    for f in sorted(glob.glob(f'content/{folder}/*.json')):
        try:
            d = load_json(f)
        except Exception as e:
            print('  ! skipped (invalid JSON):', f, e)
            continue
        if d.get('draft'):
            continue
        d['_slug'] = re.sub(r'[^a-z0-9-]+', '-', os.path.basename(f)[:-5].lower()).strip('-')
        d['_file'] = d['_slug'] + '.html'
        items.append(d)
    def _ord(d):
        try:
            return float(d.get('order'))
        except (TypeError, ValueError):
            return 999.0
    items.sort(key=lambda d: (_ord(d), d['_slug']))
    return items


TOURS = load_collection('tours')
POSTS = load_collection('blog')
for t in TOURS:
    t['_img'] = img_path(t.get('image'))
    prepare_image(t['_img'])
for p in POSTS:
    p['_img'] = img_path(p.get('image'))
    prepare_image(p['_img'])

# ------------------------------------------------------------------ site photos (Pages CMS "Site photos")
import hashlib
PHOTO_KEYS = load_json('build/photo-keys.json') if os.path.exists('build/photo-keys.json') else {}
PHOTOS = load_json('content/photos.json') if os.path.exists('content/photos.json') else {}
PHOTO_MAP = {}   # original path -> (new path with ?v=, new thumb with ?v=)


def _file_version(p):
    with open(p, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()[:8]


def setup_photos(state):
    applied = state.setdefault('photo_versions', {})   # version of the photo currently on the site
    first = state.setdefault('photo_first', {})         # version of the original photo (never changed)
    for g, items in PHOTO_KEYS.items():
        for key, info in items.items():
            orig = info['original']
            new = img_path(((PHOTOS.get(g) or {}).get(key)) or orig)
            if not os.path.exists(new):
                if new != orig:
                    print('  ! photo not found, keeping the old one:', new)
                continue
            ver = _file_version(new)
            if new == orig and key not in first:
                first[key] = ver
            if new == orig and ver == first.get(key):
                applied[key] = ver
                continue                                   # unchanged: keep the original links
            if applied.get(key) != ver:
                t = 'images/thumbs/' + os.path.basename(new)
                if os.path.exists(t):
                    os.remove(t)                           # make a fresh thumbnail for the new photo
                prepare_image(new)
                ver = _file_version(new)
            elif not os.path.exists('images/thumbs/' + os.path.basename(new)):
                prepare_image(new)
            applied[key] = ver
            th = 'images/thumbs/' + os.path.basename(new)
            PHOTO_MAP[orig] = (f'{new}?v={ver}', f'{th}?v={ver}' if os.path.exists(th) else f'{new}?v={ver}')
    # blog photos always carry ?v=, so a replaced photo shows at once
    bv = state.setdefault('blog_versions', {})
    for p in POSTS:
        img = p['_img']
        if not img or not os.path.exists(img):
            continue
        ver = _file_version(img)
        th = 'images/thumbs/' + os.path.basename(img)
        if bv.get(img) not in (None, ver) and os.path.exists(th):
            os.remove(th)
            prepare_image(img)
            ver = _file_version(img)
        bv[img] = ver
        PHOTO_MAP[img] = (f'{img}?v={ver}', f'{th}?v={ver}' if os.path.exists(th) else f'{img}?v={ver}')


def apply_photos(s):
    for orig, (new, new_th) in PHOTO_MAP.items():
        base = os.path.basename(orig)
        s = re.sub(r'images/thumbs/' + re.escape(base) + r'(?:\?v=[0-9a-f]+)?(?![\w.-])', lambda m: new_th, s)
        s = re.sub(r'(?<![\w.-])' + re.escape(orig) + r'(?:\?v=[0-9a-f]+)?(?![\w.-])', lambda m: new, s)
    return s


def photo_thumb_name(p):
    """File name used by the trip planner (it adds images/thumbs/ in front)."""
    if p in PHOTO_MAP:
        return PHOTO_MAP[p][1].split('images/thumbs/', 1)[-1] if 'images/thumbs/' in PHOTO_MAP[p][1] else os.path.basename(p)
    return os.path.basename(p)


CAT = {
    'marrakech': ('tours-marrakech.html', 'nav_t_marrakech', 'Tours from Marrakech', 'tour-grid'),
    'fes': ('tours-fes.html', 'nav_t_fes', 'Tours from Fes', 'fes-tours-grid'),
    'tangier': ('tours-tangier.html', 'nav_t_tangier', 'Tours from Tangier', 'tangier-tours-grid'),
    'casablanca': ('tours-casablanca.html', 'nav_t_casablanca', 'Tours from Casablanca', 'casablanca-tours-grid'),
    'errachidia': ('tours-errachidia.html', 'nav_t_errachidia', 'Tours from Errachidia', 'errachidia-tours-grid'),
    'ouarzazate': ('tours-ouarzazate.html', 'nav_t_ouarzazate', 'Tours from Ouarzazate', 'ouarzazate-tours-grid'),
    'daytrip': ('day-trips.html', 'nav_daytrips', 'Day Trips', None),
    'special': ('special-tours.html', 'nav_special', 'Special Tours', None),
}


def cat_of(t):
    c = t.get('category') or 'marrakech'
    return c if c in CAT else 'marrakech'


WA_SVG = '<svg viewBox="0 0 24 24"><path d="M12 2a10 10 0 0 0-8.6 15.1L2 22l5-1.3A10 10 0 1 0 12 2zm5.3 14.1c-.2.6-1.3 1.2-1.8 1.2-.5.1-1 .2-3.3-.7-2.8-1.1-4.5-3.9-4.7-4.1-.1-.2-1.1-1.5-1.1-2.9s.7-2 1-2.3c.2-.3.5-.3.7-.3h.5c.2 0 .4 0 .6.5l.8 2c.1.2.1.4 0 .5l-.3.5-.4.4c-.1.1-.3.3-.1.6.2.3.8 1.3 1.7 2.1 1.2 1 2.1 1.3 2.4 1.5.3.1.5.1.6-.1l.8-1c.2-.3.4-.2.6-.1l1.9.9c.3.1.5.2.5.3.1.2.1.7-.1 1.3z"/></svg>'
ARROW = '<svg viewBox="0 0 24 24"><path d="M5 12h13M13 6l6 6-6 6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>'


def price_html(t, cls='b'):
    if (t.get('price') or '').strip():
        return f'<{cls}>{esc(t["price"].strip())}</{cls}>'
    return f'<{cls} data-i18n="td_onreq">On request</{cls}>'


# ------------------------------------------------------------------ cards
def card_lx(t, indent='        '):
    c = t.get('card') or {}
    route = [r for r in lst(c.get('route')) or lst(t.get('route')) if r]
    route_html = ' <i class="lx-dot"></i> '.join(esc(r) for r in route)
    title = c.get('title') if has(c.get('title')) else (t.get('title') or '')
    hl = c.get('highlights')
    price = f'\n{indent}      <p class="lx-price">{esc(t["price"].strip())}</p>' if (t.get('price') or '').strip() else ''
    alt = c.get('image_alt') or t.get('title') or ''
    return f'''{indent}<article class="tour-card reveal">
{indent}  <a href="{t['_file']}" class="lx">
{indent}    <div class="lx-media"><img src="{thumb(t['_img'])}" alt="{esc(alt)}" loading="lazy"></div>
{indent}    <span class="lx-shade"></span>
{indent}    <span class="lx-frame"></span>
{indent}    <span class="lx-glare"></span>
{indent}    <div class="lx-content">
{indent}      <p class="lx-route">{route_html}</p>
{indent}      {t_el('h3', title)}
{indent}      <span class="lx-rule"></span>
{indent}      {t_el('p', hl, 'lx-hl') if has(hl) else ''}{price}
{indent}      <span class="lx-cta"><span data-i18n="tr_btn">Book now</span><i></i></span>
{indent}    </div>
{indent}  </a>
{indent}</article>
'''


def card_dc(t, indent='        '):
    c = t.get('card') or {}
    title = c.get('title') if has(c.get('title')) else (t.get('title') or '')
    alt = c.get('image_alt') or t.get('title') or ''
    return f'''{indent}<a href="{t['_file']}" class="dc reveal">
{indent}  <img src="{thumb(t['_img'])}" alt="{esc(alt)}" loading="lazy">
{indent}  <span class="dc-body">
{indent}    <span class="dc-text">
{indent}      <span class="dc-from" data-i18n="dc_from">From Marrakech</span>
{indent}      {t_el('span', title, 'dc-title')}
{indent}    </span>
{indent}    <span class="dc-go" aria-hidden="true">{ARROW}</span>
{indent}  </span>
{indent}</a>
'''


# ------------------------------------------------------------------ tour page
def maps_links(t):
    mr = [x for x in lst(t.get('map_route')) if x] or [x for x in lst(t.get('route')) if x]
    if not mr:
        return '', ''
    gm = 'https://www.google.com/maps/dir/' + '/'.join(urllib.parse.quote_plus(x) for x in mr)
    emb = ('https://maps.google.com/maps?saddr=' + urllib.parse.quote_plus(mr[0]) + '&amp;daddr=' +
           '+to:'.join(urllib.parse.quote_plus(x) for x in (mr[1:] or mr)) + '&amp;output=embed')
    return gm, emb


def tour_main(t):
    typ = t.get('type') or 'tour'
    title = t.get('title') or ''
    lp, lk, ln, _ = CAT[cat_of(t)]
    pos = esc(t.get('image_position') or 'center')
    route = ' · '.join(esc(r) for r in lst(t.get('route')) if r)
    start, end = esc(t.get('start') or ''), esc(t.get('end') or '')
    over = ''.join(f'          <p{" class=\"td-lead\"" if i == 0 else ""}{tattr(o)}>{esc(tr(o)["en"])}</p>\n'
                   for i, o in enumerate(x for x in lst(t.get('overview')) if has(x)))
    hls = ''
    for i, h in enumerate(x for x in lst(t.get('highlights')) if has((x or {}).get('title'))):
        if has(h.get('detail')):
            hls += f'            <li><span>{i+1:02d}</span><b>{t_el("em", h["title"])} {t_el("small", h["detail"])}</b></li>\n'
        else:
            hls += f'            <li><span>{i+1:02d}</span>{t_el("b", h["title"])}</li>\n'
    hlblock = (f'        <div class="td-block reveal">\n          <p class="eyebrow" data-i18n="td_hl">Highlights</p>\n'
               f'          <ul class="td-hl">\n{hls}          </ul>\n        </div>\n') if hls else ''
    days = ''
    for i, d in enumerate(lst(t.get('days'))):
        d = d or {}
        if typ == 'daytrip' or has(d.get('label')):
            label = t_el('span', d.get('label'), 'td-dn') if has(d.get('label')) else f'<span class="td-dn"><span data-i18n="td_day">Day</span> {i+1}</span>'
        else:
            label = f'<span class="td-dn"><span data-i18n="td_day">Day</span> {i+1}</span>'
        meta = ''
        if typ != 'daytrip':
            night = t_el('b', d.get('overnight')) if has(d.get('overnight')) else '<b>—</b>'
            meals = [m for m, k in (('td_breakfast', 'breakfast'), ('td_dinner', 'dinner')) if d.get(k)]
            mh = ' · '.join(f'<span data-i18n="{m}">{"Breakfast" if m == "td_breakfast" else "Dinner"}</span>' for m in meals) or '—'
            meta = (f'\n                <div class="td-meta"><span><small data-i18n="td_night">Overnight</small>{night}</span>'
                    f'<span><small data-i18n="td_meals">Meals</small><b>{mh}</b></span></div>')
        days += f'''            <details class="td-day"{" open" if i == 0 else ""}>
              <summary>{label}{t_el('span', d.get('title'), 'td-dt')}<i></i></summary>
              <div class="td-day-body">
                {t_el('p', d.get('text'))}{meta}
              </div>
            </details>
'''
    if has(t.get('itinerary_note')):
        days += f'            {t_el("p", t["itinerary_note"], "td-itin-note")}\n'
    inc = ''.join(f'                {t_el("li", x)}\n' for x in lst(t.get('included')) if has(x))
    if any(has(x) for x in lst(t.get('not_included'))):
        exc = ''.join(f'                {t_el("li", x)}\n' for x in lst(t.get('not_included')) if has(x))
    else:
        exc = ''.join(f'                {t_el("li", x)}\n' for x in lst(t.get('not_included_extra')) if has(x))
        exc += ('                <li data-i18n="td_x1">Drinks</li>\n                <li data-i18n="td_x2">Lunches</li>\n'
                '                <li data-i18n="td_x3">Entrance fees (optional)</li>\n                <li data-i18n="td_x4">Tips (optional)</li>\n')
    stops = ''.join(f'          <li><span>{i+1}</span><div><b>{esc(s.get("name"))}</b>{t_el("small", s.get("description"))}</div></li>\n'
                    for i, s in enumerate(x for x in lst(t.get('stops')) if (x or {}).get('name')))
    gm, emb = maps_links(t)
    wa = urllib.parse.quote(f'Hello Morocco Adventures Tour! I would like to book: {title}')
    map_section = ''
    if emb:
        map_section = f'''  <!-- ===== ROUTE MAP ===== -->
  <section class="td-map" id="map">
    <div class="container td-map-grid">
      <div class="td-map-text reveal">
        <p class="eyebrow" data-i18n="td_map_e">Route map</p>
        <h2 class="c-h2" data-i18n="td_map_t">Your journey on the map</h2>
        <p class="td-gal-sub"><span>{route}.</span> <span data-i18n="td_map_s">Every stop of the journey, at a glance.</span></p>
        <ol class="td-stops">
{stops}        </ol>
        <a class="td-btn-q td-maps-link" href="{gm}" target="_blank" rel="noopener" data-i18n="td_gmaps">Open in Google Maps</a>
      </div>
      <div class="td-map-frame reveal" data-label="{start} → {end}">
        <iframe title="Route map: {esc(title)}" loading="lazy" referrerpolicy="no-referrer-when-downgrade" src="{emb}"></iframe>
      </div>
    </div>
  </section>

'''
    # related: same category first, then the rest
    same = [x for x in TOURS if x is not t and cat_of(x) == cat_of(t)]
    other = [x for x in TOURS if x is not t and cat_of(x) != cat_of(t) and (x.get('type') or 'tour') != 'daytrip']
    rel = ''.join(card_lx(x) for x in (same + other)[:3])
    dur = t.get('duration')
    dur2 = t.get('duration_short') if has(t.get('duration_short')) else dur
    return f'''  <!-- ===== TOUR HERO ===== -->
  <section class="c-hero td-hero">
    <div class="c-hero-bg" style="background-image:url('{t['_img']}'); background-position: {pos}"></div>
    <div class="c-hero-overlay"></div>
    <div class="container c-hero-content">
      <nav class="crumbs reveal" aria-label="Breadcrumb"><a href="index.html" data-i18n="cr_home">Home</a><span>/</span><a href="{lp}" data-i18n="{lk}">{ln}</a></nav>
      {t_el('p', t.get('eyebrow'), 'c-eyebrow reveal')}
      <h1 class="c-title td-title">
        <span class="c-line">{t_el('span', t.get('title_line1'), 'c-line-in')}</span>
        <span class="c-line">{t_el('span', t.get('title_line2'), 'c-line-in c-gold')}</span>
      </h1>
      <ul class="td-facts reveal">
        <li><small data-i18n="td_duration">Duration</small>{t_el('b', dur)}</li>
        <li><small data-i18n="td_start">Start</small><b>{start}</b></li>
        <li><small data-i18n="td_end">End</small><b>{end}</b></li>
        <li><small data-i18n="td_price">Price</small>{price_html(t)}</li>
      </ul>
    </div>
  </section>

  <!-- ===== TOUR BODY ===== -->
  <section class="td-main">
    <div class="container td-grid">
      <article class="td-content">
        <nav class="td-tabs" aria-label="Sections">
          <a href="#overview" data-i18n="td_tab1">Overview</a>
          <a href="#itinerary" data-i18n="td_tab2">Itinerary</a>
          <a href="#included" data-i18n="td_tab3">Included</a>
          {'<a href="#map" data-i18n="td_tab5">Map</a>' if emb else ''}
          <a href="#book" data-i18n="td_tab6">Book</a>
        </nav>
        <div class="td-block reveal" id="overview">
          <p class="eyebrow" data-i18n="td_tab1">Overview</p>
{over}        </div>
{hlblock}        <div class="td-block reveal" id="itinerary">
          <p class="eyebrow" data-i18n="td_tab2">Itinerary</p>
          <p class="td-route">{route}</p>
          <div class="td-days">
{days}          </div>
        </div>
        <div class="td-block reveal" id="included">
          <div class="td-inc">
            <div>
              <p class="eyebrow" data-i18n="td_inc">Included</p>
              <ul class="td-list td-yes">
{inc}              </ul>
            </div>
            <div>
              <p class="eyebrow" data-i18n="td_exc">Not included</p>
              <ul class="td-list td-no">
{exc}              </ul>
            </div>
          </div>
        </div>
      </article>

      <aside class="td-side">
        <div class="td-book">
          <p class="td-book-eyebrow" data-i18n="td_book">Book this trip</p>
          <p class="td-book-text" data-i18n="td_book_t">Tell us your date and group size. We confirm availability and price quickly.</p>
          <ul class="td-book-facts">
            <li><small data-i18n="td_duration">Duration</small>{t_el('b', dur2)}</li>
            <li><small data-i18n="td_start">Start</small><b>{start}</b></li>
            <li><small data-i18n="td_end">End</small><b>{end}</b></li>
            <li><small data-i18n="td_price">Price</small>{price_html(t)}</li>
          </ul>
          <a class="td-btn-wa" target="_blank" rel="noopener" href="https://wa.me/{WA}?text={wa}">
            {WA_SVG}
            <span data-i18n="td_wa">Book on WhatsApp</span>
          </a>
          <a class="td-btn-q" href="contact.html" data-i18n="td_ask">Ask a question</a>
          <div class="td-why">
            <p data-i18n="td_why">Why travel with us</p>
            <ul>
              <li data-i18n="td_w1">Private tours, adapted to your wishes</li>
              <li data-i18n="td_w2">Fair prices and clear quotes</li>
              <li data-i18n="td_w3">A local team that knows every road</li>
              <li data-i18n="td_w4">Free quote, no obligation</li>
            </ul>
          </div>
        </div>
      </aside>
    </div>
  </section>

{map_section}  <!-- ===== BOOKING FORM ===== -->
  <section class="tb-section" id="book">
    <div class="container tb-grid">
      <aside class="tb-side reveal">
        <div class="tb-side-bg" style="background-image:url('{t['_img']}'); background-position:{pos}"></div>
        <div class="tb-side-in">
          <p class="tb-eyebrow" data-i18n="tb_eyebrow">Reserve your journey</p>
          <h2 class="tb-title">{t_el('span', t.get('title_line1'))} {t_el('em', t.get('title_line2'))}</h2>
          <p class="tb-dur">{t_el('span', dur)} · <span>{start} → {end}</span></p>
          <ol class="tb-steps">
            <li><b>01</b><span data-i18n="tb_s1">Send your request. It’s free and without commitment.</span></li>
            <li><b>02</b><span data-i18n="tb_s2">We reply within 24 hours with price and availability.</span></li>
            <li><b>03</b><span data-i18n="tb_s3">We shape every detail with you, then you confirm.</span></li>
          </ol>
          <p class="tb-note" data-i18n="tb_note">No payment needed to send a request.</p>
        </div>
      </aside>

      <div class="c-form-wrap tb-card reveal">
        <form class="c-form tb-form" data-tour="{esc(title)}" data-dur="{esc(tr(dur)['en'])}" novalidate>
          <div class="tb-tour"><small data-i18n="tb_your">Your tour</small><b>{esc(title)}</b></div>
          <div class="cf-row">
            <div class="cf-field"><input type="text" id="tb-name" name="name" placeholder=" " required><label for="tb-name" data-i18n="cf_name">Full name *</label><span class="cf-bar"></span></div>
            <div class="cf-field"><input type="email" id="tb-email" name="email" placeholder=" " required><label for="tb-email" data-i18n="cf_email">Email *</label><span class="cf-bar"></span></div>
          </div>
          <div class="cf-row">
            <div class="cf-field"><input type="tel" id="tb-phone" name="phone" placeholder=" "><label for="tb-phone" data-i18n="cf_phone">WhatsApp number</label><span class="cf-bar"></span></div>
            <div class="cf-field"><input type="text" id="tb-country" name="country" placeholder=" "><label for="tb-country" data-i18n="cf_country">Country</label><span class="cf-bar"></span></div>
          </div>
          <div class="cf-row cf-row-3">
            <div class="cf-field cf-static"><input type="date" id="tb-date" name="date"><label for="tb-date" data-i18n="tb_date">Start date</label><span class="cf-bar"></span></div>
            <div class="cf-field cf-static tb-count"><label data-i18n="tb_adults">Adults</label><div class="tb-step"><button type="button" data-d="-1" aria-label="−">−</button><input type="number" name="adults" value="2" min="1" max="30" readonly><button type="button" data-d="1" aria-label="+">+</button></div></div>
            <div class="cf-field cf-static tb-count"><label data-i18n="tb_kids">Children</label><div class="tb-step"><button type="button" data-d="-1" aria-label="−">−</button><input type="number" name="kids" value="0" min="0" max="20" readonly><button type="button" data-d="1" aria-label="+">+</button></div></div>
          </div>
          <div class="cf-chips" role="radiogroup" aria-label="Accommodation">
            <span class="cf-chips-label" data-i18n="tb_acc">Accommodation</span>
            <label class="chip"><input type="radio" name="acc" value="Standard"><span data-i18n="tb_acc1">Standard</span></label>
            <label class="chip"><input type="radio" name="acc" value="Superior" checked><span data-i18n="tb_acc2">Superior</span></label>
            <label class="chip"><input type="radio" name="acc" value="Luxury"><span data-i18n="tb_acc3">Luxury</span></label>
          </div>
          <div class="cf-field cf-area"><textarea id="tb-msg" name="msg" rows="3" placeholder=" "></textarea><label for="tb-msg" data-i18n="tb_msg">Special requests (diet, celebration, pace…)</label><span class="cf-bar"></span></div>
          <p class="cf-error" hidden data-i18n="cf_err">Please add your name and a valid email.</p>
          <div class="cf-actions">
            <button type="submit" class="cf-btn cf-btn-wa" data-send="wa"><span data-i18n="tb_send_wa">Request on WhatsApp</span></button>
            <button type="submit" class="cf-btn cf-btn-mail" data-send="mail"><span data-i18n="cf_send_mail">Send by email</span></button>
          </div>
        </form>
      </div>
    </div>
  </section>

  <!-- ===== RELATED ===== -->
  <section class="tours td-related">
    <div class="container">
      <div class="adv-head">
        <p class="eyebrow reveal" data-i18n="td_rel_e">Keep exploring</p>
        <h2 class="section-title reveal" data-i18n="td_rel_t">Popular destinations</h2>
        <p class="sec-short reveal" data-i18n="td_rel_s">Other private journeys our travellers love.</p>
      </div>
      <div class="t-grid" id="relGrid">
{rel}      </div>
    </div>
  </section>

'''


def set_head(h, title, desc, image, url):
    h = re.sub(r'<title>.*?</title>', lambda m: f'<title>{esc(title)}</title>', h, count=1, flags=re.S)
    for pat, val in [(r'(<meta name="description" content=")[^"]*', desc),
                     (r'(<meta property="og:title" content=")[^"]*', title),
                     (r'(<meta property="og:description" content=")[^"]*', desc),
                     (r'(<meta property="og:image" content=")[^"]*', image),
                     (r'(<meta property="og:url" content=")[^"]*', url),
                     (r'(<link rel="canonical" href=")[^"]*', url)]:
        h = re.sub(pat, lambda m: m.group(1) + esc(val), h, count=1)
    return h


def site_url():
    return (S.get('site_url') or 'https://www.moroccoadventurestour.com').rstrip('/')


def shell_for(file, ref_glob, start_marker, end_marker):
    """Head/header/footer of a page: its own if it exists, otherwise copied from a sibling page."""
    src = file if os.path.exists(file) else next((f for f in sorted(glob.glob(ref_glob)) if start_marker in read(f)), None)
    if not src:
        raise SystemExit(f'no template page found for {file}')
    h = read(src)
    a, b = h.index(start_marker), h.index(end_marker)
    return h[:a], h[b:], src != file


def build_tour(t):
    head, tail, is_new = shell_for(t['_file'], '*.html', '  <!-- ===== TOUR HERO ===== -->', '  <!-- ===== FOOTER ===== -->')
    title = t.get('title') or t['_slug']
    head = set_head(head, f'{title} | Morocco Adventures Tour', t.get('meta_description') or title,
                    f'{site_url()}/{t["_img"]}', f'{site_url()}/{t["_file"]}')
    wa_txt = urllib.parse.quote(f"Hello Morocco Adventures Tour, I'm interested in: {title}")
    tail = re.sub(r'(class="m-book".*?href="https://wa\.me/)\d+\?text=[^"]*', lambda m: m.group(1) + f'{WA}?text={wa_txt}', tail, count=1, flags=re.S)
    if 'id="mBook"' not in tail:
        mb = read(next(f for f in sorted(glob.glob('tour-*.html')) if 'id="mBook"' in read(f)))
        block = re.search(r'  <div class="m-book" id="mBook".*?\n  </div>\n', mb, re.S)
        if block:
            tail = tail.replace('  <script src="script.js"></script>', block.group(0) + '\n  <script src="script.js"></script>', 1)
    write(t['_file'], head + tour_main(t) + tail)


# ------------------------------------------------------------------ grids on list pages
def fill_grids():
    by_cat = {c: [t for t in TOURS if cat_of(t) == c] for c in CAT}
    # home page
    if os.path.exists('index.html'):
        h = read('index.html')
        for c, (_, _, _, gid) in CAT.items():
            if gid:
                items = [t for t in by_cat[c] if t.get('show_on_home', True)]
                h, _ = replace_div_inner(h, rf'<div class="tour-grid" id="{gid}">', ''.join(card_lx(t, '          ') for t in items))
        for sec in ('shared', 'authentic'):
            items = [t for t in TOURS if sec in lst(t.get('home_extra'))]
            h, _ = replace_div_inner(h, rf'<div class="tour-grid" id="{sec}-tours-grid">', ''.join(card_lx(t, '          ') for t in items))
        a = h.find('id="day-trips"')
        if a > 0:
            m = re.compile(r'<div class="dc-grid">').search(h, a)
            if m:
                b = find_block_end(h, m.start())
                items = [t for t in by_cat['daytrip'] if t.get('show_on_home', True)]
                h = h[:m.end()] + '\n' + ''.join(card_dc(t) for t in items) + '      </div>' + h[b:]
        h = apply_home(h)
        write('index.html', h)
    for c, (page, *_rest) in CAT.items():
        if not os.path.exists(page):
            continue
        h = read(page)
        if c == 'daytrip':
            h, ok = replace_div_inner(h, r'<div class="dc-grid">', ''.join(card_dc(t) for t in by_cat[c]))
        elif c == 'special':
            h, ok = replace_div_inner(h, r'<div class="t-grid sp-grid">', ''.join(card_lx(t) for t in by_cat[c]))
        else:
            h, ok = replace_div_inner(h, r'<div class="t-grid">', ''.join(card_lx(t) for t in by_cat[c]))
        if ok:
            write(page, h)


def apply_home(h):
    hero = HOME.get('hero') or {}
    pairs = [(r'<p class="hero-eyebrow reveal"[^>]*>.*?</p>', lambda v: t_el('p', v, 'hero-eyebrow reveal'), hero.get('eyebrow')),
             (r'<span(?: data-i18n="hero_title1"| data-t="[^"]*")>.*?</span>', lambda v: t_el('span', v), hero.get('title_line1')),
             (r'<span class="gold"[^>]*>.*?</span>', lambda v: t_el('span', v, 'gold'), hero.get('title_line2')),
             (r'<p class="hero-text reveal"[^>]*>.*?</p>', lambda v: t_el('p', v, 'hero-text reveal'), hero.get('text')),
             (r'<a href="#tours" class="btn-gold reveal"[^>]*>.*?</a>', lambda v: f'<a href="#tours" class="btn-gold reveal"{tattr(v)}>{esc(tr(v)["en"])}</a>', hero.get('button'))]
    a = h.find('<!-- ===== HERO ===== -->')
    b = h.find('<nav class="hero-strip"', a)
    if a < 0 or b < 0:
        return h
    seg = h[a:b]
    for pat, fn, v in pairs:
        if has(v):
            seg = re.sub(pat, lambda m: fn(v), seg, count=1, flags=re.S)
    return h[:a] + seg + h[b:]


# ------------------------------------------------------------------ planner (script.js)
def planner_js(js):
    tag = {'desert': 'd', 'cities': 'c', 'mountains': 'm'}
    rows = []
    for t in TOURS:
        p = t.get('planner') or {}
        if not p.get('show', True) or (t.get('type') == 'special' and not p.get('show')):
            continue
        days = int(p.get('days') or len(lst(t.get('days'))) or 1)
        start = p.get('start') or (cat_of(t) if cat_of(t) in CAT and cat_of(t) not in ('daytrip', 'special') else 'marrakech')
        tags = ''.join(tag[x] for x in lst(p.get('tags')) if x in tag) or 'd'
        rows.append('    [' + ','.join([json.dumps(t['_file']), json.dumps(t.get('title') or '', ensure_ascii=False), str(days),
                                         json.dumps(start), json.dumps(photo_thumb_name(t['_img'])), json.dumps(tags)]) + ']')
    new = '  const T = [\n' + ',\n'.join(rows) + '\n  ];'
    return re.sub(r'  const T = \[\n.*?\n  \];', lambda m: new, js, count=1, flags=re.S)


# ------------------------------------------------------------------ blog
BCAT = {'desert': ('bc_desert', 'Desert'), 'tips': ('bc_tips', 'Travel tips'),
        'daytrips': ('bc_daytrips', 'Day trips'), 'culture': ('bc_culture', 'Culture')}


def post_cat(p):
    return p.get('category') if p.get('category') in BCAT else 'desert'


def card_post(p):
    k, n = BCAT[post_cat(p)]
    return f'''        <a href="{p['_file']}" class="bp reveal" data-cat="{post_cat(p)}" data-tilt>
          <div class="bp-img"><img src="{thumb(p['_img'])}" alt="{esc(tr(p.get('headline'))['en'])}" loading="lazy"><span class="bp-cat-tag" data-i18n="{k}">{n}</span></div>
          <div class="bp-body">
            <span class="bp-meta"><span>{int(p.get('minutes') or 5)} <span data-i18n="bl_min">min read</span></span></span>
            {t_el('h3', p.get('headline'))}
            {t_el('p', p.get('excerpt'))}
            <span class="bp-more"><span data-i18n="bl_read">Read the story</span><i></i></span>
          </div>
        </a>
'''


def paras(text):
    return [x.strip() for x in re.split(r'\n\s*\n', text or '') if x.strip()]


def post_main(p, i):
    k, n = BCAT[post_cat(p)]
    head = tr(p.get('headline')); ex = tr(p.get('excerpt'))
    bodies = []
    for L in L3:
        s = [f'      <div class="lg-body lg-{L} ar-text">']
        if tr(p.get('intro'))[L]:
            s.append(f'        <p class="ar-lead">{esc(tr(p.get("intro"))[L])}</p>')
        for j, sec in enumerate(lst(p.get('sections'))):
            sec = sec or {}
            if tr(sec.get('heading'))[L]:
                s.append(f'        <h2>{esc(tr(sec.get("heading"))[L])}</h2>')
            for para in paras(tr(sec.get('text'))[L]):
                s.append(f'        <p>{esc(para)}</p>')
            if j == 1:
                s.append(f'        <figure class="ar-fig"><img src="{thumb(p["_img"])}" alt="{esc(head[L])}" loading="lazy"></figure>')
        s.append('      </div>')
        bodies.append('\n'.join(s))
    facts = ''.join(f'<ul class="lg-body lg-{L} ar-facts">' + ''.join(
        f'<li><span>{esc(tr(f.get("label"))[L])}</span><b>{esc(tr(f.get("value"))[L])}</b></li>' for f in lst(p.get('facts')) if f)
        + '</ul>' for L in L3)
    rel = ''.join(f'<li><a href="{esc(r.get("page"))}">' + ''.join(f'<span class="lg-body lg-{L}">{esc(tr(r.get("label"))[L])}</span>' for L in L3)
                  + '<i>→</i></a></li>' for r in lst(p.get('related')) if r and r.get('page'))
    wa = f'https://wa.me/{WA}?text=' + urllib.parse.quote(f'Hello, I read your article "{head["en"]}" and I would like to plan a trip')
    more = [POSTS[(i + k2) % len(POSTS)] for k2 in (1, 2, 3) if len(POSTS) > 1][:3]
    more = [m for m in more if m is not p]
    return f'''  <!-- ===== ARTICLE HERO ===== -->
  <section class="ar-hero">
    <div class="ar-hero-bg" style="background-image:url('{p['_img']}')"></div>
    <div class="ar-hero-ov"></div>
    <div class="container ar-hero-in">
      <nav class="crumbs" aria-label="Breadcrumb"><a href="index.html" data-i18n="cr_home">Home</a><span>/</span><a href="blog.html" data-i18n="nav_blog">Blog</a></nav>
      <span class="ar-cat" data-i18n="{k}">{n}</span>
      {t_el('h1', p.get('headline'))}
      {t_el('p', p.get('excerpt'), 'ar-ex')}
      <div class="ar-meta"><span data-i18n="ba_by">By the Morocco Adventures Tour team</span><span class="bp-dot"></span><span>{int(p.get('minutes') or 5)} <span data-i18n="bl_min">min read</span></span></div>
    </div>
  </section>

  <!-- ===== ARTICLE ===== -->
  <section class="ar-wrap">
    <div class="container ar-grid">
      <article class="ar-main">
{chr(10).join(bodies)}
        <a href="blog.html" class="ar-back">← <span data-i18n="ba_back">All stories</span></a>
      </article>
      <aside class="ar-side">
        <div class="ar-box">
          <h3 data-i18n="ba_facts">Quick facts</h3>
          {facts}
        </div>
        <div class="ar-box">
          <h3 data-i18n="ba_rel">Related tours</h3>
          <ul class="ar-rel">{rel}</ul>
        </div>
        <div class="ar-box ar-cta">
          <h3 data-i18n="ba_cta_t">Plan this trip with a local</h3>
          <p data-i18n="ba_cta_p">Tell us your dates and what you love — we reply with a private programme made for you.</p>
          <a href="{esc(wa)}" target="_blank" rel="noopener" class="ar-wa" data-i18n="ba_cta_b">Ask us on WhatsApp</a>
        </div>
      </aside>
    </div>
  </section>

  <!-- ===== MORE STORIES ===== -->
  <section class="ar-more">
    <div class="container">
      <h2 class="ar-more-t" data-i18n="ba_more">More stories</h2>
      <div class="bp-grid">
{''.join(card_post(m) for m in more)}      </div>
    </div>
  </section>

'''


def build_post(p, i):
    head, tail, _ = shell_for(p['_file'], 'blog-*.html', '  <!-- ===== ARTICLE HERO ===== -->', "  <!-- ===== CTA: LET'S DESIGN YOUR MOROCCO ===== -->")
    t = tr(p.get('headline'))['en']
    desc = tr(p.get('excerpt'))['en']
    head = set_head(head, f'{t} | Morocco Adventures Tour', desc, f'{site_url()}/{p["_img"]}', f'{site_url()}/{p["_file"]}')
    ld = {"@context": "https://schema.org", "@type": "BlogPosting", "headline": t, "description": desc,
          "image": f'{site_url()}/{p["_img"]}', "inLanguage": "en",
          "author": {"@type": "Organization", "name": "Morocco Adventures Tour"},
          "publisher": {"@type": "Organization", "name": "Morocco Adventures Tour",
                        "logo": {"@type": "ImageObject", "url": f"{site_url()}/images/logo.png"}}}
    head = re.sub(r'  <script type="application/ld\+json">.*?</script>\n', '', head, flags=re.S)
    head = head.replace('</head>', '  <script type="application/ld+json">' + json.dumps(ld, ensure_ascii=False) + '</script>\n</head>', 1)
    write(p['_file'], head + post_main(p, i) + tail)


def build_blog_list():
    if not os.path.exists('blog.html') or not POSTS:
        return
    h = read('blog.html')
    feat = next((p for p in POSTS if p.get('featured')), POSTS[0])
    rest = [p for p in POSTS if p is not feat]
    k, n = BCAT[post_cat(feat)]
    block = f'''      <a href="{feat['_file']}" class="bf reveal" data-cat="{post_cat(feat)}">
        <div class="bf-img"><img src="{thumb(feat['_img'])}" alt="{esc(tr(feat.get('headline'))['en'])}" loading="lazy"></div>
        <div class="bf-body">
          <span class="bp-meta"><span class="bp-cat" data-i18n="{k}">{n}</span><span class="bp-dot"></span><span>{int(feat.get('minutes') or 5)} <span data-i18n="bl_min">min read</span></span></span>
          <span class="bf-label" data-i18n="bl_feat">Featured story</span>
          {t_el('h2', feat.get('headline'), 'bf-title')}
          {t_el('p', feat.get('excerpt'), 'bf-ex')}
          <span class="bp-more"><span data-i18n="bl_read">Read the story</span><i></i></span>
        </div>
      </a>

      <div class="bp-grid">
{''.join(card_post(p) for p in rest)}      </div>
'''
    a = h.rfind('\n', 0, h.index('class="bf reveal"')) + 1
    g = h.index('<div class="bp-grid">', a)
    b = find_block_end(h, g)
    if h[b:b + 1] == '\n':
        b += 1
    write('blog.html', h[:a] + block + h[b:])


# ------------------------------------------------------------------ settings everywhere
def apply_settings(state):
    old = state.get('settings', {})
    new = {
        'whatsapp': WA,
        'phone_display': (S.get('phone_display') or '').strip(),
        'email': (S.get('email') or '').strip(),
        'instagram_url': (S.get('instagram_url') or '').strip(),
        'site_url': site_url(),
    }
    handle = lambda u: ('@' + u.rstrip('/').split('/')[-1]) if u else ''
    reps = []
    for k in ('whatsapp', 'phone_display', 'email', 'instagram_url', 'site_url'):
        if old.get(k) and new[k] and old[k] != new[k]:
            reps.append((old[k], new[k]))
    if old.get('instagram_url') and new['instagram_url'] and old['instagram_url'] != new['instagram_url']:
        reps.append((handle(old['instagram_url']), handle(new['instagram_url'])))
    ta = (S.get('tripadvisor_url') or '').strip() or '#'
    files = [f for f in glob.glob('*.html')] + ['script.js', 'robots.txt']
    for f in files:
        if not os.path.exists(f):
            continue
        s = read(f)
        o = s
        for a, b in reps:
            s = s.replace(a, b)
        s = re.sub(r'<a href="[^"]*"( target="_blank" rel="noopener"[^>]*aria-label="Tripadvisor")', lambda m: f'<a href="{esc(ta)}"' + m.group(1), s)
        if s != o:
            write(f, s)
    state['settings'] = new


# ------------------------------------------------------------------ sitemap
def build_sitemap():
    pages = sorted(f for f in glob.glob('*.html') if f not in ('404.html',) and not f.startswith('google'))
    rows = []
    for f in pages:
        u = site_url() + '/' + ('' if f == 'index.html' else f)
        pr = '1.0' if f == 'index.html' else ('0.8' if f.startswith(('tour', 'day', 'special', 'activities', 'agafay')) else '0.6')
        rows.append(f'  <url><loc>{u}</loc><priority>{pr}</priority></url>')
    write('sitemap.xml', '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + '\n'.join(rows) + '\n</urlset>\n')
    if os.path.exists('robots.txt'):
        r = read('robots.txt')
        r = re.sub(r'(?im)^#?\s*Sitemap:.*$', f'Sitemap: {site_url()}/sitemap.xml', r)
        write('robots.txt', r)


# ------------------------------------------------------------------ run
def main():
    state = load_json(STATE_FILE) if os.path.exists(STATE_FILE) else {}
    setup_photos(state)
    for t in TOURS:
        build_tour(t)
    for i, p in enumerate(POSTS):
        build_post(p, i)
    fill_grids()
    build_blog_list()
    if os.path.exists('script.js'):
        write('script.js', planner_js(read('script.js')))
    # pages that were generated before but whose content file was deleted
    now = sorted({t['_file'] for t in TOURS} | {p['_file'] for p in POSTS})
    for f in state.get('generated', []):
        if f not in now and os.path.exists(f) and f not in ('index.html', 'blog.html'):
            os.remove(f)
            log.append('deleted ' + f)
    state['generated'] = now
    apply_settings(state)
    for f in sorted(glob.glob('*.html')) + ['script.js']:   # put the current site photos on every page
        if os.path.exists(f):
            write(f, read(f))
    build_sitemap()
    os.makedirs('build', exist_ok=True)
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    try:
        print(f'built {len(TOURS)} tours, {len(POSTS)} blog posts; {len(log)} files changed')
        for x in log:
            print('  -', x)
    except BrokenPipeError:
        pass


if __name__ == '__main__':
    main()
