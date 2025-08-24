#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse, csv, json, re, sys, time
from urllib.parse import urlparse, urljoin
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8",
}

# --------- utils ---------
def clean_int(x):
    if x is None: return None
    if isinstance(x, (int, float)): return int(x)
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
    # accepte /ad/<categorie>/<id>
    return re.search(r"/ad/[^/]+/\d+", u) is not None

def fetch(url, timeout=25):
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} on {url}")
    return resp.text

# --------- extraction LISTE (/recherche) ---------
def extract_from_next_data(obj):
    rows = []

    def walk(node):
        if isinstance(node, dict):
            for key in ("ads","adList","items","searchResults","search_result","list"):
                val = node.get(key)
                if isinstance(val, list) and val:
                    for it in val:
                        r = normalize_next_ad(it)
                        if r: rows.append(r)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    def normalize_next_ad(d):
        if not isinstance(d, dict): return None
        url = d.get("url") or d.get("permalink") or d.get("webUrl") or d.get("landingUrl")
        if not url and d.get("id"):
            cat = d.get("categorySlug") or "annonces"
            url = f"/ad/{cat}/{d['id']}"
        title = d.get("subject") or d.get("title") or d.get("name")
        return {
            "title": title,
            "price_text": None, "price": None,
            "location": None, "date": None,
            "url": absolute_url(url) if url else None,
            "image": None
        }

    walk(obj)

    rows = [r for r in rows if r.get("url") and looks_like_ad_url(r["url"])]
    dedup, seen = [], set()
    for r in rows:
        if r["url"] in seen: continue
        seen.add(r["url"]); dedup.append(r)
    return dedup

def extract_from_apollo_state(state):
    rows = []
    if not isinstance(state, dict): return rows
    for v in state.values():
        if isinstance(v, dict) and v.get("__typename") in ("Ad","Listing","AdCard"):
            url = v.get("url") or v.get("permalink")
            title = v.get("subject") or v.get("title") or v.get("name")
            rows.append({
                "title": title,
                "price_text": None, "price": None,
                "location": None, "date": None,
                "url": absolute_url(url), "image": None
            })
    rows = [r for r in rows if r.get("url") and looks_like_ad_url(r["url"])]
    dedup, seen = [], set()
    for r in rows:
        if r["url"] in seen: continue
        seen.add(r["url"]); dedup.append(r)
    return dedup

def _cards_from_dom(soup):
    """
    Fallback DOM robuste : essaye plusieurs variantes de cartes/lien d'annonce.
    """
    rows = []
    cards = []
    # cartes/boutons courantes
    cards += soup.select('a[data-qa-id="aditem_container"]')
    cards += soup.select('a[data-test-id="aditem_container"]')
    cards += soup.select('article a[data-qa-id="aditem_container"]')
    cards += soup.select('article a[data-test-id="aditem_container"]')
    # fallback : tous liens /ad/
    cards += soup.select('article a[href^="/ad/"]')
    cards += soup.select('a[href^="/ad/"]')

    for a in cards:
        href = a.get("href")
        if not href: continue
        url = absolute_url(href)
        if not looks_like_ad_url(url): continue

        title = a.get("title") or a.get("aria-label") or a.get_text(" ", strip=True) or None

        img = None
        img_el = a.find("img")
        if img_el and img_el.get("src"):
            img = img_el["src"]
        elif img_el and img_el.get("data-src"):
            img = img_el["data-src"]

        rows.append({
            "title": title, "price_text": None, "price": None,
            "location": None, "date": None, "url": url, "image": img
        })

    # dédup
    dedup, seen = [], set()
    for r in rows:
        if r["url"] in seen: continue
        seen.add(r["url"]); dedup.append(r)
    return dedup

def parse_recherche(html):
    soup = BeautifulSoup(html, "lxml")
    rows = []

    # 1) __NEXT_DATA__
    next_tag = soup.find("script", id="__NEXT_DATA__", type="application/json")
    if next_tag and next_tag.string:
        try:
            rows.extend(extract_from_next_data(json.loads(next_tag.string)))
        except Exception:
            pass

    # 2) __APOLLO_STATE__
    if not rows:
        for s in soup.find_all("script"):
            if not s.string: continue
            m = re.search(r"__APOLLO_STATE__\s*=\s*({.*?})\s*;?\s*</", s.decode(), re.DOTALL)
            if m:
                try:
                    rows.extend(extract_from_apollo_state(json.loads(m.group(1))))
                except Exception:
                    continue

    # 3) Fallback DOM (et fusion si besoin)
    if not rows:
        rows.extend(_cards_from_dom(soup))
    else:
        extra = _cards_from_dom(soup)
        have = {r["url"] for r in rows if r.get("url")}
        for r in extra:
            if r["url"] not in have:
                rows.append(r)

    # dédup final
    dedup, seen = [], set()
    for r in rows:
        if not r.get("url"): continue
        if r["url"] in seen: continue
        seen.add(r["url"]); dedup.append(r)
    return dedup

def parse_list_page(source_url, html):
    if "/recherche" in urlparse(source_url).path:
        return parse_recherche(html)
    return []

# --------- HYDRATATION (page annonce) ---------
def json_ld_blocks(soup):
    out = []
    for tag in soup.find_all("script", type="application/ld+json"):
        if not tag.string: continue
        try:
            out.append(json.loads(tag.string))
        except Exception:
            continue
    return out

def first_meta(soup, name, prop=False):
    el = soup.find("meta", property=name) if prop else soup.find("meta", attrs={"name": name})
    return el.get("content") if el and el.has_attr("content") else None

def parse_car_from_jsonld(data):
    """
    Essaye d’extraire les attributs véhicule/produit depuis JSON-LD.
    Couvre @type Car/Vehicle/Product et @graph.
    """
    if isinstance(data, list):
        for d in data:
            r = parse_car_from_jsonld(d)
            if r: return r
        return None
    if not isinstance(data, dict):
        return None

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

    # prix
    offers = data.get("offers") or {}
    if isinstance(offers, list) and offers:
        offers = offers[0]
    if isinstance(offers, dict):
        out["price"] = clean_int(offers.get("price"))
        if offers.get("price"):
            out["price_text"] = f"{offers.get('price')} {offers.get('priceCurrency','')}".strip()

    # localisation
    addr = data.get("address") or {}
    if isinstance(addr, dict):
        city = addr.get("addressLocality") or addr.get("addressRegion")
        zipcode = addr.get("postalCode")
        loc = " ".join([p for p in (city, zipcode) if p]).strip()
        out["location"] = loc or None

    out["date"] = data.get("datePublished") or data.get("availabilityStarts") or None

    # images
    imgs = data.get("image") or data.get("images")
    if isinstance(imgs, list) and imgs:
        out["image"] = imgs[0] if isinstance(imgs[0], str) else imgs[0].get("url")
    elif isinstance(imgs, str):
        out["image"] = imgs

    # si Car/Vehicle, on lit les champs dédiés
    if at in ("Car","Vehicle"):
        odo = data.get("mileageFromOdometer")
        if isinstance(odo, dict) and "value" in odo:
            out["mileage_km"] = clean_int(odo.get("value"))

        for key in ("productionDate","releaseDate","modelDate","vehicleModelDate"):
            if data.get(key):
                y = re.search(r"\b(19|20)\d{2}\b", str(data[key]))
                if y: out["year"] = int(y.group(0)); break

        out["fuel"] = data.get("fuelType") or None
        out["gearbox"] = data.get("vehicleTransmission") or None
        out["brand"] = (data.get("brand") or {}).get("name") if isinstance(data.get("brand"), dict) else data.get("brand")
        out["model"] = (data.get("model") or {}).get("name") if isinstance(data.get("model"), dict) else data.get("model")
        return out

    # si Product, tenter additionalProperty
    if at == "Product":
        b = data.get("brand")
        out["brand"] = b.get("name") if isinstance(b, dict) else (b or out["brand"])
        m = data.get("model")
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
                y = re.search(r"\b(19|20)\d{2}\b", val)
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

# --------- Fallback HTML direct ---------
def parse_html_fallback(soup):
    """
    Récupère les infos directement depuis le HTML de la fiche quand JSON-LD est incomplet.
    On couvre plusieurs variantes de sélecteurs.
    """
    out = {}

    # PRIX
    price_nodes = soup.select('[data-qa-id="adview_price"], [data-test-id="ad-price"], .Price__amount, [itemprop="price"]')
    if price_nodes:
        price_text = price_nodes[0].get_text(" ", strip=True)
        out["price_text"] = price_text
        out["price"] = clean_int(price_text)

    # LOCALISATION
    loc_nodes = soup.select('[data-qa-id="adview_location_informations"], [data-test-id="ad-address"], .AdViewBreadcrumbs__item, [itemprop="address"]')
    if loc_nodes:
        loc_text = loc_nodes[0].get_text(" ", strip=True)
        out["location"] = re.sub(r"\s+", " ", loc_text)

    # DATE
    t = soup.select_one('time[datetime]')
    if t and t.has_attr("datetime"):
        out["date"] = t.get("datetime")
    else:
        date_nodes = soup.select('[data-qa-id="adview_date"], [itemprop="datePosted"]')
        if date_nodes:
            out["date"] = date_nodes[0].get_text(" ", strip=True)

    # CARACTÉRISTIQUES
    crit_items = []
    crit_items += soup.select('[data-qa-id="criteria_item"]')
    crit_items += soup.select('.Carac__item, .AdviewCriteria__item, [data-test-id="criteria-item"]')
    for it in crit_items:
        label_el = it.select_one('[data-qa-id="criteria_item_label"]') or it.find(class_="property") or it.find("span")
        value_el = it.select_one('[data-qa-id="criteria_item_value"]') or it.find(class_="value") or it.find("strong") or it.find("span")
        label = label_el.get_text(" ", strip=True) if label_el else ""
        value = value_el.get_text(" ", strip=True) if value_el else ""
        lbl = label.lower()

        if "kilom" in lbl:
            km = re.search(r"(\d[\d\s\u202f]*)", value)
            if km: out["mileage_km"] = clean_int(km.group(1))
        elif "ann" in lbl:
            y = re.search(r"\b(19|20)\d{2}\b", value)
            if y: out["year"] = int(y.group(0))
        elif "carburant" in lbl or "fuel" in lbl:
            out["fuel"] = value
        elif "boite" in lbl or "boîte" in lbl or "transmission" in lbl:
            out["gearbox"] = value
        elif "marque" in lbl:
            out["brand"] = value
        elif "mod" in lbl:
            out["model"] = value

    # IMAGE & TITRE (metas)
    og_image = first_meta(soup, "og:image", prop=True)
    if og_image: out.setdefault("image", og_image)
    og_title = first_meta(soup, "og:title", prop=True)
    if og_title: out.setdefault("title", og_title)

    return out

# --------- Pagination (suivre le vrai lien “next”) ---------
def find_next_url(current_url, html):
    """
    Retourne l'URL absolue de la page suivante si elle existe.
    On suit en priorité <link rel="next">, puis un lien/bouton 'Suivant'.
    """
    soup = BeautifulSoup(html, "lxml")

    # 1) SEO canonical next
    tag = soup.find("link", rel="next")
    if tag and tag.get("href"):
        return urljoin(current_url, tag["href"])

    # 2) A rel=next
    a = soup.find("a", rel="next")
    if a and a.get("href"):
        return urljoin(current_url, a["href"])

    # 3) Boutons/lien “Suivant”
    a = soup.select_one('a[aria-label*="Suivant" i], a[aria-label*="Page suivante" i], a[href*="&o="], a[href*="?o="]')
    if a and a.get("href"):
        return urljoin(current_url, a["href"])

    return None

def fetch_all_listings(start_url, max_ads=None, delay=1.0, max_pages=20):
    results, seen = [], set()
    current_url = start_url
    page = 1
    stale_pages = 0
    visited = set()

    while page <= max_pages and current_url and current_url not in visited:
        visited.add(current_url)
        print(f"[i] Page {page} -> {current_url}")

        html = fetch(current_url)
        ads = parse_list_page(current_url, html)

        if not ads:
            print(f"[i] Aucune annonce sur la page {page}, arrêt.")
            break

        added = 0
        for ad in ads:
            u = ad.get("url")
            if u and u not in seen:
                seen.add(u); results.append(ad); added += 1

        print(f"    - {len(ads)} annonces détectées, {added} nouvelles ajoutées")

        # stop conditions
        stale_pages = stale_pages + 1 if added == 0 else 0
        if max_ads and len(results) >= max_ads:
            results = results[:max_ads]; break
        if stale_pages >= 2:
            print("[i] Deux pages sans nouvelle annonce, arrêt pagination.")
            break

        # lien suivant fiable
        next_url = find_next_url(current_url, html)

        # fallback “&o=N+1” si pas de lien next
        if not next_url:
            m = re.search(r"([?&])o=(\d+)", current_url)
            if m:
                next_url = re.sub(
                    r"([?&])o=(\d+)",
                    lambda mm: f"{mm.group(1)}o={int(mm.group(2))+1}",
                    current_url,
                )

        if not next_url or next_url == current_url:
            break

        current_url = next_url
        page += 1
        time.sleep(delay)

    return results

def hydrate_ad(ad, sleep_between=0.0):
    """Ouvre la page annonce et complète les champs manquants depuis JSON-LD, metas et HTML."""
    try:
        html = fetch(ad["url"])
        soup = BeautifulSoup(html, "lxml")

        # 1) JSON-LD
        data = json_ld_blocks(soup)
        jd = None
        for block in data:
            r = parse_car_from_jsonld(block)
            if r:
                jd = r; break

        if jd:
            for k, v in jd.items():
                if ad.get(k) in (None, "", []):
                    ad[k] = v

        # 2) Metas
        og_title = first_meta(soup, "og:title", prop=True)
        og_image = first_meta(soup, "og:image", prop=True)
        article_published_time = first_meta(soup, "article:published_time", prop=True)
        if not ad.get("title") and og_title:
            ad["title"] = og_title
        if (not ad.get("image")) and og_image:
            ad["image"] = og_image
        if (not ad.get("date")) and article_published_time:
            ad["date"] = article_published_time

        # 3) Fallback HTML
        html_fallback = parse_html_fallback(soup)
        for k, v in html_fallback.items():
            if ad.get(k) in (None, "", []):
                ad[k] = v

        # Nettoyage
        if ad.get("location"):
            ad["location"] = re.sub(r"\s+", " ", ad["location"]).strip()

    except Exception as e:
        ad.setdefault("_errors", []).append(str(e))

    if sleep_between > 0:
        time.sleep(sleep_between)
    return ad

# --------- I/O ---------
BASE_FIELDS = ["title","price_text","price","location","date","url","image"]
EXTRA_FIELDS = ["year","mileage_km","fuel","gearbox","brand","model","_errors"]

def save_csv(rows, path):
    fields = BASE_FIELDS + EXTRA_FIELDS
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fields})

# --------- main ---------
def main():
    ap = argparse.ArgumentParser(description="Leboncoin /recherche -> URLs annonces puis hydratation JSON-LD/HTML")
    ap.add_argument("url")
    ap.add_argument("-o","--output", default="leboncoin.csv")
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--max", type=int, default=None, help="Limiter le nombre de résultats (ex: 36)")
    ap.add_argument("--hydrate", action="store_true", help="Hydrater chaque annonce (prix/ville/date/etc.)")
    ap.add_argument("--no-hydrate", dest="no_hydrate", action="store_true", help="Désactiver l’hydratation")
    ap.add_argument("--sleep", type=float, default=1.0, help="Délai (s) entre pages/fiches")
    ap.add_argument("--pages", type=int, default=20, help="Nombre maximum de pages à explorer")
    args = ap.parse_args()

    rows = fetch_all_listings(args.url, max_ads=args.max, delay=args.sleep, max_pages=args.pages)

    do_hydrate = args.hydrate or not args.no_hydrate
    if do_hydrate:
        print(f"[i] Hydratation de {len(rows)} fiches…")
        out = []
        for i, r in enumerate(rows, 1):
            print(f"  - ({i}/{len(rows)}) {r['url']}")
            out.append(hydrate_ad(r, sleep_between=max(0.2, args.sleep)))
        rows = out

    print(f"[i] Annonces prêtes : {len(rows)}")
    save_csv(rows, args.output)
    print(f"[✓] CSV : {args.output}")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
        print(f"[✓] JSON : {args.json_out}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)
