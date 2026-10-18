# Changelog

## 1.20261018.1203114

- Page views from a report downloaded from GA4, no Google sign-in needed: "Show page views?" /
  "Update page views…" under the site name takes the Pages and screens report as CSV (chosen or
  dropped) and merges it into `data/pageviews.json` (POST /api/pageviews). Counts are dated by the
  report's last day; pages it leaves out keep their last count.
- `scripts/ga4_pageviews.py --csv report.csv` does the same from the command line.
- `docs/pageviews.md` starts with the report download; the GA4 API setup follows.

## 1.20261018.1195452

- Page views in the post list, from the site's `data/pageviews.json`: `👁 1,245` next to each post
  (when it was counted on hover), and a "By views" sort. Without the file, "Show page views?" under
  the site name explains how to make one.
- `scripts/ga4_pageviews.py`, standalone (`uv run scripts/ga4_pageviews.py --property … --top 2000`):
  each page's views of the last 5 years from Google Analytics 4, merged into `data/pageviews.json`;
  pages left out keep their last count and its date. `/a/b.html` and `/a/b` (Cloudflare Pages) are
  one page.
- A Hugo partial showing them on the pages, "1,245 次瀏覽（3 天前統計）" (hours, days, months, years,
  kept current in the browser), used by the demo site with an example `data/pageviews.json`.
  Guide: `docs/pageviews.md`.

## 1.20261018.1195423

- The toolbar keeps when the open post was saved: "Saved · 5 seconds ago" (minutes, hours; the date
  and time after a day), the exact `YYYY-MM-DD HH:mm:ss` on hover. On opening a post it is the file's
  modification time; it shows "not committed" when git has changes to the post.
- Under the site name: whether the site is in git, how many of its files are not committed (the list
  on hover) and when the last commit was. Refreshed after saves, on coming back to the window and
  every 30 seconds (GET /api/git). A site inside a larger repository counts only its own files.

## 1.20261018.1102747

- Tags above the editor: the post's tags as chips; type one and press Enter or a comma (`，` and `、`
  too) to add it, ✕ or Backspace to remove one. The site's tags are suggested, the most used first.
  Written to the front matter in YAML, TOML or JSON; a list written one `- item` per line stays that way.
- Tags are trimmed and kept once, whatever their case (Hugo puts `Go` and `go` on one page).

## 1.20261017.1220639

- Edits made elsewhere (another editor, an AI tool, git) are no longer overwritten: every save carries
  the version it was based on, and if the file changed on disk, autosave pauses and asks whether to
  load the file or keep your version. Coming back to the window loads outside changes when there are
  no unsaved edits here.
- Fix: after a post's `url` / `slug` changed outside the editor, the Hugo preview kept opening the old
  address (an empty page `hugo server` still had in memory). Opening a post, and saving a change to
  its front matter, now use the permalink Hugo gives it at that moment.
- The URL field notes when the site's new posts get a `url` (setting `url`) but this post has none,
  and where Hugo will publish it instead.

## 1.20261017.1114705

- Post info above the editor: title, URL and featured image (none, one of the post's images, or an
  upload just for it), written to the front matter in YAML, TOML or JSON with the rest kept.
- Settings `featured_field` (`images` by default, `cover.image` for PaperMod, any key) and
  `featured_default` (`first`: the first image dropped into a post becomes its featured image).

## 1.20261017.1103937

- Clicking a link lets you edit its text and URL (filled in with the current ones; Enter or Apply),
  remove the link while keeping the text, or open it. Its target / rel and other text marks stay.

## 1.20261017.1103544

- Click a link for "Open in a new tab" and rel (nofollow, sponsored, ugc, noopener, noreferrer); such
  links are written as HTML `<a>`, and go back to Markdown when cleared.
- Fix: editing a post in the WYSIWYG view dropped `target` and `rel` from its HTML `<a>` links
  (TOAST UI neither reads nor writes them); they are now kept.

## 1.20261017.1102721

- Click an image for a display width too: Full size / Large 512 / Medium 256 / Small 128, kept with the
  alignment in the URL fragment (`#w256`, `#right-w256`); the file is not resized. Site CSS: see README.

## 1.20261017.1102445

- Rename any image to a name you type (URL-safe, extension kept), or leave it empty for `{slug}-NN`,
  from the image panel or the library; images in `static/images/` too.
- Fix: renaming an image that was already renamed (e.g. a double click) crashed the request; renames
  now run one at a time, buttons are disabled meanwhile, and a missing image is reported.

## 1.20261017.1102122

- The logo moves from the site name (it read as the site's own logo) to the version line at the
  bottom left, which links to PyPI.

## 1.20261017.1101806

- Image library: every image of the site, the posts linking to it (click to edit), filters for unused
  images and names not following the post; deleting lists the posts it would break.
- Rename images to `{slug}-NN` from the panel or the library; every Markdown post linking to the
  image, by file name or by published URL, is updated.
- Privacy: SVGs lose metadata, editor data (e.g. Inkscape's absolute export path) and scripts;
  animated GIF / WebP lose comments and EXIF.
- Settings dialog (gear, bottom left) for image naming, width, format, quality and metadata,
  saved to `.hugo-writer.toml` with its comments kept.
- Images in the editor, the panel and the library come from disk, so unused (unpublished with
  `publishResources = false`) and just-dropped images show.
- The sidebar shows the hugo-writer version, linking to PyPI.

## 1.20261017.1093729

- A logo: a fountain-pen nib whose slit forms an "H". Favicon for the editor tab, next to the site name,
  and at the top of the README.

## 1.20261017.1093336

- Drag images to move them in the WYSIWYG view (TOAST UI blocks this on its own); dropped at the start or
  end of a paragraph an image becomes a paragraph of its own.
- Click an image for Left / Center / Right / Default, kept as `#left` / `#center` / `#right` in its URL.
- Files dropped in from outside go where they were dropped, not where the cursor was.
- Image panel: the post's images with thumbnails, in use / unused, insert, delete, delete all unused.
- The status line says what metadata a photo carried and was removed (GPS, camera, time, …);
  `strip_metadata = false` keeps EXIF.
- The open post stays in the address (`?open=…`), so a reload comes back to it.
- Demo site: alignment CSS, `publishResources = false`, and its own image render hook (works on Hugo 0.128+).

## 1.20261017.1091404

- Image size menu in the toolbar: Original (max 1024) | Large 512 | Medium 256 | Small 128. New images
  are saved at that width (and so shown at it); the choice is remembered.

## 1.20261017.1090629

- Dropped images are named after the post (my-post-01.webp, my-post-02.webp, …) instead of the
  uploaded file, and their alt text starts as the post title: screenshot and camera file names
  carry dates and more. `image_name = "{original}"` brings the old behaviour back.

## 1.20261017.1090054

- Images dropped or pasted into the WYSIWYG view no longer show as broken: they are inserted once the
  preview serves the new file (right after saving, hugo needs a moment and answered 404).

## 1.20261017.1084122

- Markdown posts with HTML blocks (iframes, tables, scripts) open in the source view with a note,
  so the WYSIWYG editor can't drop that HTML.
- JSON front matter is kept out of the WYSIWYG editor, like YAML and TOML (it used to land in the
  editor body, and saving from there would have broken it).
- The view you picked comes back for the next ordinary post.
- Demo site: a post with an embedded video.

## 1.20261017.1000000

- First release: post list via `hugo list all`, Markdown / WYSIWYG editor (TOAST UI Editor 3.2.2, bundled),
  live preview through a private `hugo server`, draft toggle, image drop / paste with resize to 1024px,
  WebP re-encoding and EXIF removal, autosave.
