# Changelog

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
