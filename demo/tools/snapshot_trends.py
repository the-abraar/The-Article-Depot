"""Usage: python demo/tools/snapshot_trends.py demo/data/bd_trends.json

Fetch one Google Trends RSS for geo=BD with the repo's PoliteFetcher; keep titles + source URLs only."""
import json, sys, dataclasses, datetime
from pipeline import config, fetcher, trends
cfg = dataclasses.replace(config.load(), geo="BD")
f = fetcher.PoliteFetcher(cfg)
url = trends.feed_url(cfg)
ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
res = f.get(url)
items = trends.parse_feed(res.text)
out = {"fetched_at": ts, "feed": url, "status": res.status,
       "trends": [{"title": t.title, "traffic": t.traffic,
                   "sources": [{"publisher": n.publisher, "title": n.title, "url": n.url} for n in t.news]} for t in items]}
json.dump(out, open(sys.argv[1], "w"), ensure_ascii=False, indent=1)
print(res.status, len(items), "trends", sum(len(t.news) for t in items), "links")
