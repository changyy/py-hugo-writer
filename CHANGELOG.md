# Changelog

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
