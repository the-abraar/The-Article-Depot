"""Render the static site.

Templates are plain HTML with {{placeholder}} markers substituted by string
replacement -- no template engine, keeping the install surface to the
Anthropic SDK alone. Every page is rewritten on each publish, so the site is
a pure function of the database: re-running publish after a prune or a
restore produces exactly the right site with no orphaned pages.
"""

from __future__ import annotations

import html
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

SITE_NAME = "The Article Depot"
TAGLINE = "Explainers on what people are searching for, grounded in reporting."


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def render(template: str, **fields: str) -> str:
    out = template
    for key, value in fields.items():
        out = out.replace("{{" + key + "}}", value)
    return out


def _nav(active: str, depth: int = 0) -> str:
    up = "../" * depth
    items = [("Home", f"{up}index.html"), ("Articles", f"{up}articles.html"),
             ("About", f"{up}about.html"), ("Contact", f"{up}contact.html")]
    active_cls = ' class="active"'      # no backslash in the f-string: 3.11 rejects it
    return "\n".join(
        f'        <li{active_cls if name == active else ""}>'
        f'<a href="{href}">{name}</a></li>'
        for name, href in items)


def _article_body(body: dict) -> str:
    parts = []
    for section in body.get("sections", []):
        parts.append(f"      <h2>{_esc(section['heading'])}</h2>")
        for para in section.get("paragraphs", []):
            parts.append(f"      <p>{_esc(para)}</p>")
    return "\n".join(parts)


def _sources_list(sources: list) -> str:
    if not sources:
        return ""
    rows = []
    for s in sources:
        label = _esc(s["publisher"] or s["title"] or s["url"])
        rows.append(f'        <li><a href="{_esc(s["url"])}" rel="nofollow noopener" '
                    f'target="_blank">{label}</a></li>')
    return ("      <section class=\"sources\">\n"
            "        <h2>Sources</h2>\n"
            "        <ul>\n" + "\n".join(rows) + "\n        </ul>\n"
            "      </section>")


def publish_article(article, body: dict, sources: list, cfg, templates) -> Path:
    """Write one article page. Returns its path."""
    cfg.articles_dir.mkdir(parents=True, exist_ok=True)
    published = (article["published_at"] or datetime.now(timezone.utc).isoformat())[:10]
    page = render(
        templates["article"],
        **_chrome("article",
                  title=f'{article["title"]} | {SITE_NAME}',
                  description=body.get("dek", "")[:160]),
        dek=_esc(body.get("dek", "")),
        published=published,
        nav=_nav("Articles", depth=1),
        body=_article_body(body),
        sources=_sources_list(sources),
        year=str(datetime.now(timezone.utc).year),
    )
    # The <h1> uses the bare headline, not the chrome's "| site" title.
    page = page.replace("<h1>" + _esc(f'{article["title"]} | {SITE_NAME}') + "</h1>",
                        "<h1>" + _esc(article["title"]) + "</h1>")
    path = cfg.articles_dir / f"{article['slug']}.html"
    path.write_text(page, encoding="utf-8")
    return path


def _card(article, body: dict) -> str:
    published = (article["published_at"] or "")[:10]
    return (
        '      <li>\n'
        f'        <h3><a href="articles/{_esc(article["slug"])}.html">'
        f'{_esc(article["title"])}</a></h3>\n'
        f'        <p>{_esc(body.get("dek", ""))}</p>\n'
        f'        <p class="meta"><time datetime="{published}">{published}</time></p>\n'
        '      </li>')


def publish_site(store, cfg) -> dict:
    """Rebuild every page from the database. Idempotent by construction."""
    templates = load_templates(cfg)
    cfg.site_dir.mkdir(parents=True, exist_ok=True)
    cfg.articles_dir.mkdir(parents=True, exist_ok=True)

    live = store.published()
    written: list[Path] = []
    cards: list[str] = []

    for article in live:
        body = json.loads(article["body_json"])
        sources = [dict(r) for r in store.sources_for(article["id"])]
        written.append(publish_article(article, body, sources, cfg, templates))
        cards.append(_card(article, body))

    # Any page on disk that no longer corresponds to a live article is removed,
    # which is how a prune actually reaches the public site.
    keep = {p.name for p in written}
    removed = 0
    for existing in cfg.articles_dir.glob("*.html"):
        if existing.name not in keep:
            existing.unlink()
            removed += 1

    year = str(datetime.now(timezone.utc).year)
    listing = "\n".join(cards) if cards else \
        '      <li><p>No articles published yet.</p></li>'

    (cfg.site_dir / "articles.html").write_text(render(
        templates["articles"], **_chrome("articles"), nav=_nav("Articles"),
        listing=listing, year=year, count=str(len(cards))), encoding="utf-8")

    (cfg.site_dir / "index.html").write_text(render(
        templates["index"], **_chrome("index"), nav=_nav("Home"), year=year,
        tagline=TAGLINE, listing="\n".join(cards[:5]) or
        '      <li><p>No articles published yet.</p></li>'), encoding="utf-8")

    for name in ("about", "contact"):
        (cfg.site_dir / f"{name}.html").write_text(render(
            templates[name], **_chrome(name), nav=_nav(name.capitalize()),
            year=year, contact_email=cfg.contact_email), encoding="utf-8")

    shutil.copyfile(cfg.template_dir / "style.css", cfg.site_dir / "style.css")
    _write_robots_and_sitemap(cfg, live)

    return {"articles": len(written), "removed": removed}


def _write_robots_and_sitemap(cfg, live) -> None:
    (cfg.site_dir / "robots.txt").write_text(
        "User-agent: *\nAllow: /\nSitemap: /sitemap.xml\n", encoding="utf-8")
    urls = ["index.html", "articles.html", "about.html", "contact.html"]
    urls += [f"articles/{a['slug']}.html" for a in live]
    entries = "\n".join(f"  <url><loc>/{u}</loc></url>" for u in urls)
    (cfg.site_dir / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}\n</urlset>\n", encoding="utf-8")


def load_templates(cfg) -> dict[str, str]:
    """Compose each page from the shared head/foot and its own <main>."""
    head = (cfg.template_dir / "_head.part").read_text(encoding="utf-8")
    foot = (cfg.template_dir / "_foot.part").read_text(encoding="utf-8")
    names = ("index", "articles", "article", "about", "contact")
    return {
        n: head + (cfg.template_dir / f"{n}.html").read_text(encoding="utf-8") + foot
        for n in names
    }


# Per-page head metadata. `depth` is how far the page sits below the site root,
# which decides whether asset links need a "../" prefix.
PAGE_META = {
    "index":    {"title": SITE_NAME, "description": TAGLINE,
                 "ogtype": "website", "depth": 0},
    "articles": {"title": f"Articles | {SITE_NAME}",
                 "description": f"Every explainer published by {SITE_NAME}.",
                 "ogtype": "website", "depth": 0},
    "about":    {"title": f"About | {SITE_NAME}",
                 "description": "How these articles are produced, grounded and checked.",
                 "ogtype": "website", "depth": 0},
    "contact":  {"title": f"Contact | {SITE_NAME}",
                 "description": f"Report a correction to {SITE_NAME}.",
                 "ogtype": "website", "depth": 0},
    "article":  {"ogtype": "article", "depth": 1},
}


def _chrome(page: str, **over: str) -> dict[str, str]:
    """Head fields every template shares, resolved for this page's depth."""
    meta = dict(PAGE_META[page])
    depth = meta.pop("depth")
    up = "../" * depth
    meta.update(over)
    meta.setdefault("title", SITE_NAME)
    meta.setdefault("description", TAGLINE)
    return {"title": meta["title"], "description": meta["description"],
            "ogtype": meta["ogtype"], "css": f"{up}style.css",
            "home": f"{up}index.html", "site": SITE_NAME}
