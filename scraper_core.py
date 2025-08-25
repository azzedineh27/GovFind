# scraper_core.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re, time, json
from urllib.parse import urlparse, urljoin
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8",
}

def clean_int(x):
    if x is None: return None
    m = re.search(r"(\d[\d\s\u202f]*)", str(x))
    if not m: return None
    return int(m.group(1).replace(" ", "").replace("\u202f", ""))

def absolute_url(href):
    if not href: return None
    if href.startswith("http"): return href
    if href.startswith("//"): return "https:" + href
    return "https://www.leboncoin.fr" + (href if href.startswith("/") else "/" + href)

def looks_like_ad_url(u):
    if not u: return False
    if "#footer" in u or "#mainContent" in u: return False
    return re.search(r"/ad/[^/]+/\d+", u) is not None

def fetch(url, timeout=25):
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.text

# ---------- LIST PAGE ----------
def extract_from_next_data(obj):
    rows = []
    def walk(node):
        if isinstance(node, dict):
            for key in ("ads","adList","items","searchResults","search_result","list"):
                val = node.get(key)
                if isinstance(val, list):
                    for it in val:
                        r = normalize_next_ad(it)
                        if r: rows.append(r)
            for v in node.values(): walk(v)
        elif isinstance(node, list):
            for v in node: walk(v)

    def normalize_next_ad(d):
        if not isinstance(d, dict): return None
        url = d.get("url") or d.get("permalink") or d.get("webUrl") or d.get("landingUrl")
        if not url and d.get("id"):
            cat = d.get("categorySlug") or "annonces"
            url = f"/ad/{cat}/{d['id']}"
        title = d.get("subject") or d.get("title") or d.get("name")
        return {"title": title, "price_text": None, "price": None, "location": None,
                "date": None, "url": absolute_url(url) if url else None, "image": None}

    walk(obj)
    rows = [r for r in rows if r.get("url") and looks_like_ad_url(r["url"])]
    out, seen = [], set()
    for r in rows:
        if r["url"] in seen: continue
        seen.add(r["url"]); out.append(r)
    return out

def extract_from_apollo_state(state):
    rows = []
    if not isinstance(state, dict): return rows
    for v in state.values():
        if isinstance(v, dict) and v.get("__typename") in ("Ad","Listing","AdCard"):
            url = v.get("url") or v.get("permalink")
            title = v.get("subject") or v.get("title") or v.get("name")
            rows.append({"title": title, "price_text": None, "price": None,
                         "location": None, "date": None, "url": absolute_url(url), "image": None})
    rows = [r for r in rows if r.get("url") and looks_like_ad_url(r["url"])]
    out, seen = [], set()
    for r in rows:
        if r["url"] in seen: continue
        seen.add(r["url"]); out.append(r)
    return out

def _cards_from_dom(soup):
    rows, cards = [], []
    cards += soup.select('a[data-qa-id="aditem_container"], a[data-test-id="aditem_container"]')
    cards += soup.select('article a[data-qa-id="aditem_container"], article a[data-test-id="aditem_container"]')
    cards += soup.select('article a[href^="/ad/"], a[href^="/ad/"]')
    for a in cards:
        href = a.get("href"); 
        if not href: continue
        url = absolute_url(href)
        if not looks_like_ad_url(url): continue
        title = a.get("title") or a.get("aria-label") or a.get_text(" ", strip=True) or None
        img = None
        img_el = a.find("img")
        if img_el:
            img = img_el.get("src") or img_el.get("data-src")
        rows.append({"title": title, "price_text": None, "price": None,
                     "location": None, "date": None, "url": url, "image": img})
    out, seen = [], set()
    for r in rows:
        if r["url"] in seen: continue
        seen.add(r["url"]); out.append(r)
    return out

def parse_recherche(html):
    soup = BeautifulSoup(html, "lxml")
    rows = []

    # 1) __NEXT_DATA__
    tag = soup.find("script", id="__NEXT_DATA__", type="application/json")
    if tag and tag.string:
        try: rows.extend(extract_from_next_data(json.loads(tag.string)))
        except: pass

    # 2) __APOLLO_STATE__
    if not rows:
        for s in soup.find_all("script"):
            if not s.string: continue
            m = re.search(r"__APOLLO_STATE__\s*=\s*({.*?})\s*;?\s*</", s.decode(), re.DOTALL)
            if m:
                try: rows.extend(extract_from_apollo_state(json.loads(m.group(1))))
                except: continue

    # 3) DOM
    if not rows:
        rows.extend(_cards_from_dom(soup))
    else:
        extra = _cards_from_dom(soup)
        have = {r["url"] for r in rows if r.get("url")}
        rows.extend([r for r in extra if r["url"] not in have])

    # dedup
    out, seen = [], set()
    for r in rows:
        if not r.get("url"): continue
        if r["url"] in seen: continue
        seen.add(r["url"]); out.append(r)
    return out

def parse_list_page(source_url, html):
    if "/recherche" in urlparse(source_url).path:
        return parse_recherche(html)
    return []

# ---------- AD PAGE ----------
def json_ld_blocks(soup):
    out = []
    for tag in soup.find_all("script", type="application/ld+json"):
        if not tag.string: continue
        try: out.append(json.loads(tag.string))
        except: continue
    return out

def first_meta(soup, name, prop=False):
    el = soup.find("meta", property=name) if prop else soup.find("meta", attrs={"name": name})
    return el.get("content") if el and el.has_attr("content") else None

def parse_car_from_jsonld(data):
    if isinstance(data, list):
        for d in data:
            r = parse_car_from_jsonld(d)
            if r: return r
        return None
    if not isinstance(data, dict): return None

    at = data.get("@type")
    if at not in ("Car","Vehicle","Product"):
        for k in ("@graph","itemListElement","hasPart"):
            if isinstance(data.get(k), list):
                for d in data[k]:
                    r = parse_car_from_jsonld(d)
                    if r: return r

    out = {k: None for k in ("title","price_text","price","location","date","image",
                             "year","mileage_km","fuel","gearbox","brand","model")}
    out["title"] = data.get("name") or data.get("headline") or data.get("title")

    offers = data.get("offers") or {}
    if isinstance(offers, list) and offers: offers = offers[0]
    if isinstance(offers, dict):
        out["price"] = clean_int(offers.get("price"))
        if offers.get("price"):
            out["price_text"] = f"{offers.get('price')} {offers.get('priceCurrency','')}".strip()

    addr = data.get("address") or {}
    if isinstance(addr, dict):
        city = addr.get("addressLocality") or addr.get("addressRegion")
        zipcode = addr.get("postalCode")
        loc = " ".join([p for p in (city, zipcode) if p]).strip()
        out["location"] = loc or None

    out["date"] = data.get("datePublished") or data.get("availabilityStarts") or None

    imgs = data.get("image") or data.get("images")
    if isinstance(imgs, list) and imgs:
        out["image"] = imgs[0] if isinstance(imgs[0], str) else imgs[0].get("url")
    elif isinstance(imgs, str):
        out["image"] = imgs

    if at in ("Car","Vehicle"):
        odo = data.get("mileageFromOdometer")
        if isinstance(odo, dict) and "value" in odo:
            out["mileage_km"] = clean_int(odo.get("value"))
        for key in ("productionDate","releaseDate","modelDate","vehicleModelDate"):
            if data.get(key):
                y = re.search(r"\b(19|20)\d{2}\b", str(data[key])); 
                if y: out["year"] = int(y.group(0)); break
        out["fuel"] = data.get("fuelType") or None
        out["gearbox"] = data.get("vehicleTransmission") or None
        out["brand"] = (data.get("brand") or {}).get("name") if isinstance(data.get("brand"), dict) else data.get("brand")
        out["model"] = (data.get("model") or {}).get("name") if isinstance(data.get("model"), dict) else data.get("model")
        return out

    if at == "Product":
        b = data.get("brand"); m = data.get("model")
        out["brand"] = b.get("name") if isinstance(b, dict) else (b or out["brand"])
        out["model"] = m.get("name") if isinstance(m, dict) else (m or out["model"])
        props = data.get("additionalProperty") or data.get("additionalProperties") or []
        if isinstance(props, dict): props = [props]
        for prop in props:
            if not isinstance(prop, dict): continue
            name = str(prop.get("name") or "").strip().lower()
            val  = str(prop.get("value") or "").strip()
            if not name or not val: continue
            if "marque" in name and not out["brand"]: out["brand"] = val
            elif "mod" in name and not out["model"]: out["model"] = val
            elif "année" in name and not out["year"]:
                y = re.search(r"\b(19|20)\d{2}\b", val); 
                if y: out["year"] = int(y.group(0))
            elif "kilom" in name and not out["mileage_km"]:
                km = re.search(r"(\d[\d\s\u202f]*)", val)
                if km: out["mileage_km"] = clean_int(km.group(1))
            elif ("carburant" in name or "fuel" in name) and not out["fuel"]:
                out["fuel"] = val
            elif ("boite" in name or "boîte" in name or "transmission" in name) and not out["gearbox"]:
                out["gearbox"] = val
        return out

    return out if any(out.values()) else None

def parse_html_fallback(soup):
    out = {}
    price_nodes = soup.select('[data-qa-id="adview_price"], [data-test-id="ad-price"], .Price__amount, [itemprop="price"]')
    if price_nodes:
        pt = price_nodes[0].get_text(" ", strip=True)
        out["price_text"] = pt; out["price"] = clean_int(pt)
    loc_nodes = soup.select('[data-qa-id="adview_location_informations"], [data-test-id="ad-address"], .AdViewBreadcrumbs__item, [itemprop="address"]')
    if loc_nodes:
        out["location"] = re.sub(r"\s+", " ", loc_nodes[0].get_text(" ", strip=True))
    t = soup.select_one('time[datetime]')
    if t and t.has_attr("datetime"):
        out["date"] = t.get("datetime")
    else:
        dn = soup.select('[data-qa-id="adview_date"], [itemprop="datePosted"]')
        if dn: out["date"] = dn[0].get_text(" ", strip=True)
    crits = []
    crits += soup.select('[data-qa-id="criteria_item"]')
    crits += soup.select('.Carac__item, .AdviewCriteria__item, [data-test-id="criteria-item"]')
    for it in crits:
        label_el = it.select_one('[data-qa-id="criteria_item_label"]') or it.find(class_="property") or it.find("span")
        value_el = it.select_one('[data-qa-id="criteria_item_value"]') or it.find(class_="value") or it.find("strong") or it.find("span")
        label = (label_el.get_text(" ", strip=True) if label_el else "").lower()
        value = value_el.get_text(" ", strip=True) if value_el else ""
        if "kilom" in label:
            km = re.search(r"(\d[\d\s\u202f]*)", value); 
            if km: out["mileage_km"] = clean_int(km.group(1))
        elif "ann" in label:
            y = re.search(r"\b(19|20)\d{2}\b", value); 
            if y: out["year"] = int(y.group(0))
        elif "carburant" in label or "fuel" in label:
            out["fuel"] = value
        elif "boite" in label or "boîte" in label or "transmission" in label:
            out["gearbox"] = value
        elif "marque" in label:
            out["brand"] = value
        elif "mod" in label:
            out["model"] = value
    og_img = first_meta(soup, "og:image", prop=True)
    if og_img: out.setdefault("image", og_img)
    og_title = first_meta(soup, "og:title", prop=True)
    if og_title: out.setdefault("title", og_title)
    return out

# ---------- Pagination ----------
def find_next_url(current_url, html):
    soup = BeautifulSoup(html, "lxml")
    tag = soup.find("link", rel="next")
    if tag and tag.get("href"): return urljoin(current_url, tag["href"])
    a = soup.find("a", rel="next")
    if a and a.get("href"): return urljoin(current_url, a["href"])
    a = soup.select_one('a[aria-label*="Suivant" i], a[aria-label*="Page suivante" i], a[href*="&o="], a[href*="?o="]')
    if a and a.get("href"): return urljoin(current_url, a["href"])
    return None

def fetch_all_listings(start_url, max_ads=None, delay=1.0, max_pages=20):
    results, seen = [], set()
    current_url, page, stale, visited = start_url, 1, 0, set()
    while page <= max_pages and current_url and current_url not in visited:
        visited.add(current_url)
        html = fetch(current_url)
        ads = parse_list_page(current_url, html)
        if not ads: break
        added = 0
        for ad in ads:
            u = ad.get("url")
            if u and u not in seen:
                seen.add(u); results.append(ad); added += 1
        stale = stale + 1 if added == 0 else 0
        if max_ads and len(results) >= max_ads:
            return results[:max_ads]
        if stale >= 2: break
        nxt = find_next_url(current_url, html)
        if not nxt:
            m = re.search(r"([?&])o=(\d+)", current_url)
            if m:
                nxt = re.sub(r"([?&])o=(\d+)", lambda mm: f"{mm.group(1)}o={int(mm.group(2))+1}", current_url)
        if not nxt or nxt == current_url: break
        current_url = nxt; page += 1; time.sleep(delay)
    return results

def hydrate_ad(ad, sleep_between=0.0):
    try:
        html = fetch(ad["url"])
        soup = BeautifulSoup(html, "lxml")
        # JSON-LD
        for block in json_ld_blocks(soup):
            r = parse_car_from_jsonld(block)
            if r:
                for k, v in r.items():
                    if ad.get(k) in (None, "", []): ad[k] = v
                break
        # metas
        og_title = first_meta(soup, "og:title", prop=True)
        og_img   = first_meta(soup, "og:image", prop=True)
        art_time = first_meta(soup, "article:published_time", prop=True)
        if not ad.get("title") and og_title: ad["title"] = og_title
        if not ad.get("image") and og_img: ad["image"] = og_img
        if not ad.get("date") and art_time: ad["date"] = art_time
        # fallback html
        fb = parse_html_fallback(soup)
        for k, v in fb.items():
            if ad.get(k) in (None, "", []): ad[k] = v
        if ad.get("location"): ad["location"] = re.sub(r"\s+", " ", ad["location"]).strip()
    except Exception as e:
        ad.setdefault("_errors", []).append(str(e))
    if sleep_between > 0: time.sleep(sleep_between)
    return ad

def scrape(url: str, max_ads: int | None = None, hydrate: bool = True,
           sleep: float = 0.8, pages: int = 10, model_re: str | None = None):
    rows = fetch_all_listings(url, max_ads=max_ads, delay=sleep, max_pages=pages)
    # filtre modèle (regex) côté serveur si fourni
    if model_re:
        try:
            pat = re.compile(model_re, re.I)
            rows = [r for r in rows if pat.search(r.get("title") or "") or
                                  pat.search(((r.get("brand") or "") + " " + (r.get("model") or "")).strip())]
        except re.error:
            pass
    if hydrate:
        out = []
        for r in rows:
            out.append(hydrate_ad(r, sleep_between=max(0.2, sleep)))
        rows = out
    return rows
