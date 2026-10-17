"""Images dropped or pasted into the editor: shrink, re-encode, strip metadata, store."""
import io
import re
from pathlib import Path

from PIL import Image, ImageOps

MAX_UPLOAD = 50 * 1024 * 1024


def process(data: bytes, filename: str, max_width: int, fmt: str, quality: int) -> tuple[bytes, str]:
    """Return (image bytes, file extension).

    Raster images wider than `max_width` are scaled down, then re-encoded as `fmt`.
    Re-encoding drops EXIF, so camera GPS coordinates never reach the site.
    SVGs and animated images are stored as they are.
    """
    if Path(filename).suffix.lower() == '.svg':
        return data, '.svg'
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        raise ValueError(f'not an image: {filename}') from e
    if getattr(img, 'is_animated', False):
        return data, '.' + (img.format or 'gif').lower()

    img = ImageOps.exif_transpose(img)
    if img.width > max_width:
        img = img.resize((max_width, max(1, round(img.height * max_width / img.width))), Image.LANCZOS)

    fmt = fmt.lower()
    if fmt in ('jpg', 'jpeg'):
        fmt, ext = 'JPEG', '.jpg'
        img = img.convert('RGB')
    else:
        ext = '.' + fmt
        fmt = fmt.upper()
        has_alpha = img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info)
        img = img.convert('RGBA' if has_alpha else 'RGB')
    out = io.BytesIO()
    img.save(out, fmt, quality=quality)
    return out.getvalue(), ext


def safe_stem(filename: str) -> str:
    # macOS names screenshots like "截圖 2026-10-17 上午10.30.12.png"; keep what is URL-safe.
    stem = re.sub(r'[^A-Za-z0-9._-]+', '-', Path(filename).stem).strip('-.').lower()
    return stem or 'image'


def image_path(folder: Path, pattern: str, slug: str, original: str, ext: str) -> Path:
    """A free file name from the image_name pattern, e.g. "{slug}-{n}" -> my-post-01.webp."""
    def name(n):
        # safe_stem() drops a file extension; the '.x' keeps a slug like "v1.2" whole.
        return safe_stem(pattern.format(slug=slug, n=f'{n:02d}', original=safe_stem(original)) + '.x')
    if '{n}' not in pattern:
        return unique_path(folder, name(1), ext)
    n = 1
    while (folder / f'{name(n)}{ext}').exists():
        n += 1
    return folder / f'{name(n)}{ext}'


def unique_path(folder: Path, stem: str, ext: str) -> Path:
    path, n = folder / f'{stem}{ext}', 2
    while path.exists():
        path, n = folder / f'{stem}-{n}{ext}', n + 1
    return path
