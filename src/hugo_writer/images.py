"""Images dropped or pasted into the editor: shrink, re-encode, strip metadata, store."""
import io
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageOps, ImageSequence

MAX_UPLOAD = 50 * 1024 * 1024


def metadata(img) -> list[str]:
    """What an image carries about where, when, with what and by whom it was taken."""
    exif = img.getexif()
    sub = exif.get_ifd(0x8769)                                  # the Exif sub-IFD
    found = []
    if exif.get_ifd(0x8825):
        found.append('gps')
    if exif.get(0x010F) or exif.get(0x0110) or sub.get(0xA431) or sub.get(0xA434):
        found.append('camera')                                  # make, model, body serial, lens
    if exif.get(0x0132) or sub.get(0x9003) or sub.get(0x9004):
        found.append('time')
    if exif.get(0x013B) or exif.get(0x8298):
        found.append('author')                                  # artist, copyright
    if any('xmp' in str(key).lower() for key in img.info):
        found.append('xmp')
    if exif and not found:
        found.append('exif')
    return found


SVG = 'http://www.w3.org/2000/svg'
# Editor namespaces: Inkscape keeps the absolute path of the file it exported to, the document
# name and window layout; Sodipodi, Illustrator and Sketch similar things.
EDITOR_NS = ('sodipodi', 'inkscape', 'adobe', 'illustrator', 'sketch', 'figma')


def sanitize_svg(data: bytes) -> tuple[bytes, list[str]]:
    """An SVG without what it says about its author and computer, and without scripts.

    Removes <metadata> (author, licence, RDF), editor elements and attributes (e.g.
    inkscape:export-filename="/Users/you/…"), comments, and — since the SVG is served from the
    site — <script>, <foreignObject> and on… event attributes.
    """
    for prefix, uri in (('', SVG), ('xlink', 'http://www.w3.org/1999/xlink')):
        ET.register_namespace(prefix, uri)
    try:
        root = ET.fromstring(data)                    # comments and the XML prolog are dropped here
    except ET.ParseError as e:
        raise ValueError('not a valid SVG') from e
    found = set()

    def editor(name):
        return name.startswith('{') and any(ns in name.split('}')[0].lower() for ns in EDITOR_NS)

    for parent in list(root.iter()):
        for child in list(parent):
            tag = child.tag if isinstance(child.tag, str) else ''
            local = tag.split('}')[-1]
            if local == 'metadata' or editor(tag):
                parent.remove(child); found.add('metadata')
            elif local in ('script', 'foreignObject'):
                parent.remove(child); found.add('script')
    for el in root.iter():
        for attr in list(el.attrib):
            if editor(attr):
                del el.attrib[attr]; found.add('metadata')
            elif attr.lower().startswith('on') or (attr.endswith('href') and el.attrib[attr].strip().lower().startswith('javascript:')):
                del el.attrib[attr]; found.add('script')
    return ET.tostring(root, encoding='utf-8', xml_declaration=True), sorted(found)


def strip_animation(img, fmt: str) -> bytes:
    """Save an animated GIF / WebP again, frame by frame, without comments, EXIF or XMP."""
    frames, durations = [], []
    for frame in ImageSequence.Iterator(img):
        durations.append(frame.info.get('duration', 100))
        clean = frame.convert('RGBA')
        clean.info = {}                               # convert() copies comment / exif / xmp along
        frames.append(clean)
    out = io.BytesIO()
    frames[0].save(out, fmt, save_all=True, append_images=frames[1:], duration=durations,
                   loop=img.info.get('loop', 0), disposal=2 if fmt == 'GIF' else 0)
    return out.getvalue()


def process(data: bytes, filename: str, max_width: int, fmt: str, quality: int,
            strip: bool = True) -> tuple[bytes, str, list[str]]:
    """Return (image bytes, file extension, metadata removed).

    Raster images wider than `max_width` are scaled down, then re-encoded as `fmt`.
    Re-encoding drops EXIF and XMP (GPS position, camera, time taken, …) unless strip is False.
    SVGs are cleaned (see sanitize_svg), animated images saved again without their metadata;
    both keep their format and size.
    """
    if Path(filename).suffix.lower() == '.svg':
        clean, found = sanitize_svg(data)             # scripts go even when keeping metadata
        return (clean, '.svg', found) if strip or 'script' in found else (data, '.svg', [])
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        raise ValueError(f'not an image: {filename}') from e
    if getattr(img, 'is_animated', False):
        kind = (img.format or 'GIF').upper()
        if not strip:
            return data, '.' + kind.lower(), []
        found = metadata(img) + (['comment'] if img.info.get('comment') else [])
        return strip_animation(img, kind), '.' + kind.lower(), found

    found = metadata(img)
    img = ImageOps.exif_transpose(img)                          # applies and drops the rotation tag
    keep = {} if strip or not img.info.get('exif') else {'exif': img.info['exif']}
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
    img.save(out, fmt, quality=quality, **keep)
    return out.getvalue(), ext, (found if strip else [])


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
