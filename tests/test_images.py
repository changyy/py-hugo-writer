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


def test_animated_gifs_pass_through():
    frames = [Image.new('RGB', (10, 10), (i * 100, 0, 0)).convert('P') for i in range(3)]
    out = io.BytesIO()
    frames[0].save(out, 'GIF', save_all=True, append_images=frames[1:])
    data, ext, _ = process(out.getvalue(), 'a.gif', 5, 'webp', 80)
    assert data == out.getvalue() and ext == '.gif'


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
