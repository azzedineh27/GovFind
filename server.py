# server.py
from flask import Flask, request, jsonify
from flask_cors import CORS
from scraper_core import scrape

app = Flask(__name__)
CORS(app)  # Autorise l'appel depuis un fichier index.html ouvert localement

@app.get("/api/scrape")
def api_scrape():
    url = request.args.get("url")
    if not url:
        return jsonify({"error": "paramètre 'url' requis"}), 400

    max_ads = request.args.get("max", type=int, default=None)
    hydrate = request.args.get("hydrate", default="1") not in ("0","false","False")
    sleep   = request.args.get("sleep", type=float, default=0.8)
    pages   = request.args.get("pages", type=int, default=10)
    model_re = request.args.get("model_re")  # regex optionnelle

    try:
        rows = scrape(url=url, max_ads=max_ads, hydrate=hydrate, sleep=sleep, pages=pages, model_re=model_re)
        return jsonify({"count": len(rows), "data": rows})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.get("/health")
def health():
    return {"ok": True}

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=True)
