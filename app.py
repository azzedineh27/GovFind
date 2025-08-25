#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse, csv, json, re, sys
from detail_parser import hydrate_ad
from pagination import fetch_all_listings
from scraper_core.core import scrape

BASE_FIELDS = ["title","price_text","price","location","date","url","image"]
EXTRA_FIELDS = ["year","mileage_km","fuel","gearbox","brand","model","_errors"]

def save_csv(rows, path):
    fields = BASE_FIELDS + EXTRA_FIELDS
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fields})

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
