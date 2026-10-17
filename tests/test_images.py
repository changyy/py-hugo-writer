import io

import pytest
from PIL import Image

from hugo_writer.images import image_path, process, safe_stem, unique_path


def png(width, height, mode='RGB', **info):
    out = io.BytesIO()
    Image.new(mode, (width, height)).save(out, 'PNG', **info)
    return out.getvalue()


def test_wide_images_are_scaled_down_to_the_max_width():
    data, ext, _ = process(png(3000, 1500), 'shot.png', 1024, 'webp', 80)
    img = Image.open(io.BytesIO(data))
    assert ext == '.webp' and img.format == 'WEBP' and img.size == (1024, 512)


def test_small_images_keep_their_size():
    data, _, _ = process(png(300, 200), 'a.png', 1024, 'webp', 80)
    assert Image.open(io.BytesIO(data)).size == (300, 200)


def test_transparency_survives():
    data, _, _ = process(png(10, 10, 'RGBA'), 'a.png', 1024, 'webp', 80)
    assert Image.open(io.BytesIO(data)).mode == 'RGBA'


def photo_with_metadata():
    exif = Image.Exif()
    exif[0x010F] = 'CameraMaker'                 # make
    exif[0x0132] = '2026:10:17 09:30:00'         # date and time
    exif.get_ifd(0x8825)[2] = (25.0, 2.0, 0.0)   # GPS latitude
    out = io.BytesIO()
    Image.new('RGB', (20, 20)).save(out, 'JPEG', exif=exif)
    return out.getvalue()


def test_metadata_is_dropped_and_reported():
    data, ext, removed = process(photo_with_metadata(), 'p.jpg', 1024, 'jpeg', 80)
    assert ext == '.jpg' and removed == ['gps', 'camera', 'time']
    assert not Image.open(io.BytesIO(data)).getexif()


def test_metadata_can_be_kept():
    data, _, removed = process(photo_with_metadata(), 'p.jpg', 1024, 'webp', 80, strip=False)
    assert removed == [] and Image.open(io.BytesIO(data)).getexif().get(0x010F) == 'CameraMaker'


def test_nothing_reported_for_a_clean_image():
    assert process(png(10, 10), 'a.png', 1024, 'webp', 80)[2] == []


def animated_gif(comment=b''):
    frames = [Image.new('RGB', (10, 10), (i * 100, 0, 0)).convert('P') for i in range(3)]
    out = io.BytesIO()
    frames[0].save(out, 'GIF', save_all=True, append_images=frames[1:], duration=120, loop=0, comment=comment)
    return out.getvalue()


def test_animated_gifs_stay_animated_but_lose_their_comment():
    data, ext, removed = process(animated_gif(b'made by Jane on her laptop'), 'a.gif', 5, 'webp', 80)
    img = Image.open(io.BytesIO(data))
    assert ext == '.gif' and img.is_animated and img.n_frames == 3 and img.size == (10, 10)
    assert 'comment' not in img.info and removed == ['comment']


def test_animated_gifs_untouched_when_keeping_metadata():
    original = animated_gif(b'note')
    assert process(original, 'a.gif', 5, 'webp', 80, strip=False)[0] == original


INKSCAPE_SVG = b"""<?xml version="1.0" encoding="UTF-8"?>
<!-- Created with Inkscape -->
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
     xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
     xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"
     xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
     width="10" height="10" sodipodi:docname="secret-plan.svg"
     inkscape:export-filename="/Users/jane/Desktop/secret-plan.png" onload="alert(1)">
  <metadata><rdf:RDF><dc:creator>Jane Doe</dc:creator></rdf:RDF></metadata>
  <sodipodi:namedview id="base" inkscape:window-width="1440"/>
  <script>alert('x')</script>
  <a xlink:href="javascript:alert(2)"><rect width="10" height="10" fill="#0a66c2" inkscape:label="box"/></a>
</svg>"""


def test_svgs_lose_editor_paths_author_and_scripts():
    data, ext, removed = process(INKSCAPE_SVG, 'plan.svg', 1024, 'webp', 80)
    text = data.decode()
    assert ext == '.svg' and removed == ['metadata', 'script']
    for gone in ('/Users/jane', 'secret-plan', 'Jane Doe', 'Inkscape', 'namedview', 'alert', 'onload', 'inkscape:'):
        assert gone not in text, gone
    assert '<rect' in text and 'fill="#0a66c2"' in text and text.count('<svg') == 1 and 'ns0:' not in text


def test_svg_scripts_go_even_when_keeping_metadata():
    data, _, removed = process(INKSCAPE_SVG, 'plan.svg', 1024, 'webp', 80, strip=False)
    assert 'alert' not in data.decode() and 'script' in removed


def test_non_images_are_rejected():
    with pytest.raises(ValueError):
        process(b'not an image', 'x.png', 1024, 'webp', 80)


def test_safe_stem_and_unique_path(tmp_path):
    assert safe_stem('截圖 2026-10-17 上午10.30.12.png') == '2026-10-17-10.30.12'
    assert safe_stem('圖.png') == 'image'
    (tmp_path / 'a.webp').write_bytes(b'')
    assert unique_path(tmp_path, 'a', '.webp').name == 'a-2.webp'


def test_image_path_patterns(tmp_path):
    assert image_path(tmp_path, '{slug}-{n}', 'my-post', 'Screenshot 1.png', '.webp').name == 'my-post-01.webp'
    (tmp_path / 'my-post-01.webp').write_bytes(b'')
    assert image_path(tmp_path, '{slug}-{n}', 'my-post', 'x.png', '.webp').name == 'my-post-02.webp'
    assert image_path(tmp_path, '{original}', 'my-post', 'Cat Photo.PNG', '.webp').name == 'cat-photo.webp'
    (tmp_path / 'cat-photo.webp').write_bytes(b'')
    assert image_path(tmp_path, '{original}', 'my-post', 'Cat Photo.PNG', '.webp').name == 'cat-photo-2.webp'
