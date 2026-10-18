# Page views from GA4, shown by Hugo

One file, `data/pageviews.json`, and two pieces around it. Neither needs hugo-writer:

| File | What it does |
|------|--------------|
| [`scripts/ga4_pageviews.py`](../scripts/ga4_pageviews.py) | Asks Google Analytics 4 for each page's views and merges them into `data/pageviews.json` |
| [`pageviews.html`](../examples/demo-site/layouts/partials/pageviews.html) | A Hugo partial that shows them: `1,245 次瀏覽（3 天前統計）` / `1,245 views (as of 3d ago)` |

hugo-writer shows the same numbers in its post list, and sorts by them. The site never calls
Google: the numbers are in the JSON, and Hugo writes them into the pages. The demo site has an
example: [`data/pageviews.json`](../examples/demo-site/data/pageviews.json), shown by its
`single.html`.

The file, written by the script or by anything else:

```json
{"updated": "2026-10-18T03:00:00+08:00", "years": 5, "pages": {
 "/2024/05/hello-markdown": {"views": 1245, "at": "2026-10-18T03:00:00+08:00"}
}}
```

A key is the page's URL path (as `.RelPermalink` writes it, percent-encoded) in lower case, without
the query, `index.html`, `.html` or a trailing slash. `at` is when that count was taken.

## 1. Google side (once)

1. In Google Cloud, create a project (or use one), enable the **Google Analytics Data API**, create a
   **service account** and a JSON key for it.
2. In GA4, **Admin › Property access management**, add the service account's e-mail as **Viewer**.
3. Note the **Property ID** (Admin › Property details), a number like `123456789`.

## 2. Fetch

Copy `ga4_pageviews.py` into the site's `scripts/` and run it in the site folder with
[uv](https://docs.astral.sh/uv/), which installs its one dependency (`google-auth`) by itself:

```bash
curl -fsSLo scripts/ga4_pageviews.py https://raw.githubusercontent.com/changyy/py-hugo-writer/main/scripts/ga4_pageviews.py
uv run scripts/ga4_pageviews.py --property 123456789 --credentials key.json --top 2000
```

| Option | |
|--------|---|
| `--property` | GA4 property ID (or `GA4_PROPERTY`) |
| `--years N` | count the last N years (default 5) |
| `--top N` | fetch only the N most viewed pages (default: all) |
| `--host` | count only this host name, if the property covers other sites too |
| `--output` | default `data/pageviews.json` |
| `--credentials` / `GA4_CREDENTIALS_JSON` / `--token` | a key file, the key's content, or an access token; with none, Application Default Credentials (`gcloud auth application-default login`, `GOOGLE_APPLICATION_CREDENTIALS`) |

**Each run merges into the file.** Pages GA4 returns get the new count and today's date; pages it
leaves out (beyond `--top`, or not viewed in the window) keep the count and date they had, and the
partial says how old that count is. Changing `--years` starts the file over.

Notes:

- `/a/b.html`, `/a/b`, `/a/b/` and `/a/b/index.html` count as one page, so Hugo's `.html` URLs
  match what GA4 sees on Cloudflare Pages (which serves `/a/b.html` at `/a/b`), before and after.

- GA4 counts only since its tag was on the site; Universal Analytics history is gone.
- Ad blockers hide many visits, so the numbers are low; fine for which posts are read most.
- GA4 needs a day or two to process a day's data.

## 3. Show

Copy `pageviews.html` to `layouts/partials/` and call it where the count should show, e.g. in
`layouts/_default/single.html`:

```go-html-template
{{ partial "pageviews.html" . }}
```

It prints nothing for a page the file doesn't have. To hide small counts:

```toml
[params.pageviews]
  min = 50
```

Wording is Chinese for a `zh*` site language, English otherwise; edit the two `dict`s in the
partial to change it. "N 小時 / 天 / 個月 / 年前" is worked out at build time and again in the browser,
so it stays right between builds. Style it with `.pageviews`.

Hugo 0.156+ notes `site.Data` as deprecated; on those versions it can be `hugo.Data`.

## 4. Every day, on GitHub Actions

Because each run builds on the last, keep `data/pageviews.json` in the repository and let a
scheduled workflow commit it. Add the property ID as the variable `GA4_PROPERTY` and the key file's
content as the secret `GA4_CREDENTIALS_JSON` (Settings › Secrets and variables › Actions).

```yaml
# .github/workflows/pageviews.yml
name: Page views
on:
  schedule:
    - cron: '0 19 * * *'   # 03:00 in Taipei
  workflow_dispatch:
permissions:
  contents: write
jobs:
  fetch:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - uses: astral-sh/setup-uv@v6
      - run: uv run scripts/ga4_pageviews.py --top 2000
        env:
          GA4_PROPERTY: ${{ vars.GA4_PROPERTY }}
          GA4_CREDENTIALS_JSON: ${{ secrets.GA4_CREDENTIALS_JSON }}
      - run: |
          git diff --quiet -- data/pageviews.json && exit 0
          git config user.name 'github-actions[bot]'
          git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
          git add data/pageviews.json
          git commit -m 'chore: page views from GA4'
          git push
```

A host that builds on every push (Cloudflare Pages, Netlify, Vercel) then publishes the new numbers.
A deploy workflow in the same repository is not started by this push (GitHub doesn't chain
`GITHUB_TOKEN` pushes); run the fetch as a step before `hugo` in that workflow instead.
