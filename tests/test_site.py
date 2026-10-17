import os
import time
from datetime import datetime, timezone

import pytest

from hugo_writer.site import Conflict, Settings, Site, SiteError, front_matter_end, has_raw_html, set_draft, version

from .conftest import BUNDLE, DRAFT, HUGO, LEGACY, needs_hugo

NOW = datetime(2026, 10, 17, 9, 30, tzinfo=timezone.utc)


def test_rejects_a_directory_that_is_not_a_hugo_site(tmp_path):
    with pytest.raises(SiteError):
        Site(tmp_path)


def test_create_writes_a_draft_page_bundle(site):
    rel = site.create('Hello "World"', 'hello-world', now=NOW)
    assert rel == 'content/posts/2026/10/hello-world/index.md'
    text = site.read(rel)
    assert text.startswith('---\ntitle: "Hello \\"World\\""\ndate: 2026-10-17T')
    assert 'draft: true' in text
    # examples/demo-site/.hugo-writer.toml sets a Blogger-style url pattern
    assert 'url: /2026/10/hello-world.html' in text


def test_create_without_a_url_pattern(site_dir):
    (site_dir / '.hugo-writer.toml').unlink()
    site = Site(site_dir, HUGO)
    assert 'url:' not in site.read(site.create('A', 'a', now=NOW))


def test_create_refuses_bad_slugs_and_duplicates(site):
    with pytest.raises(SiteError):
        site.create('A', 'Not A Slug', now=NOW)
    site.create('A', 'a', now=NOW)
    with pytest.raises(SiteError):
        site.create('A again', 'a', now=NOW)


def test_paths_must_stay_inside_content(site):
    for bad in ('hugo.toml', '../etc/passwd', 'content/../hugo.toml'):
        with pytest.raises(SiteError):
            site.read(bad)


def test_only_markdown_is_writable(site):
    site.write(BUNDLE, 'new')
    assert site.read(BUNDLE) == 'new'
    with pytest.raises(SiteError):
        site.write(LEGACY, 'nope')


def test_set_draft_on_files(site):
    site.set_draft(DRAFT, False)
    assert 'draft = false' in site.read(DRAFT)
    site.set_draft(BUNDLE, True)
    assert 'draft: true' in site.read(BUNDLE)


@pytest.mark.parametrize('before, after', [
    ('---\ntitle: x\n---\nbody', '---\ntitle: x\ndraft: true\n---\nbody'),
    ('---\ntitle: x\ndraft: false\n---\nbody', '---\ntitle: x\ndraft: true\n---\nbody'),
    ('+++\ntitle = "x"\ndraft = false\n+++\nbody', '+++\ntitle = "x"\ndraft = true\n+++\nbody'),
    ('{\n  "title": "x",\n  "draft": false\n}\nbody', '{\n  "title": "x",\n  "draft": true\n}\nbody'),
    ('just text', '---\ndraft: true\n---\njust text'),
])
def test_set_draft(before, after):
    assert set_draft(before, True) == after


def test_set_draft_leaves_the_body_alone():
    text = '---\ntitle: x\n---\n```yaml\ndraft: false\n```\n'
    assert set_draft(text, True) == '---\ntitle: x\ndraft: true\n---\n```yaml\ndraft: false\n```\n'


def test_image_target(site):
    folder, prefix = site.image_target(BUNDLE)
    assert folder == site.content / 'posts/2024/05/hello-markdown' and prefix == ''
    folder, prefix = site.image_target(DRAFT)
    assert folder == site.root / 'static/images/posts/2024/06/a-draft'
    assert prefix == '/images/posts/2024/06/a-draft/'


def test_settings_ignore_unknown_keys(tmp_path):
    (tmp_path / '.hugo-writer.toml').write_text('image_max_width = 800\nsomething_else = 1\n')
    assert Settings.load(tmp_path).image_max_width == 800


def test_written_files_get_normal_permissions(site):
    rel = site.create('Perm', 'perm', now=NOW)
    umask = os.umask(0)
    os.umask(umask)
    assert (site.root / rel).stat().st_mode & 0o777 == 0o666 & ~umask
    os.chmod(site.root / rel, 0o640)
    site.write(rel, 'x')
    assert (site.root / rel).stat().st_mode & 0o777 == 0o640


@needs_hugo
def test_posts_lists_every_page_with_hugos_metadata(site):
    site.create('Brand new', 'brand-new', now=NOW)
    posts = {p['path']: p for p in site.posts()}
    assert posts[BUNDLE]['title'] == 'Hello, Markdown'
    assert posts[BUNDLE]['url'] == '/2024/05/hello-markdown.html'
    assert posts[BUNDLE]['editable'] and not posts[BUNDLE]['draft']
    assert posts[DRAFT]['draft'] is True
    assert posts[LEGACY]['editable'] is False
    new = posts['content/posts/2026/10/brand-new/index.md']
    assert new['draft'] is True and new['url'] == '/2026/10/brand-new.html'
    listed = [p['date'] for p in site.posts()]
    assert listed == sorted(listed, reverse=True)


@needs_hugo
def test_url_of_follows_a_front_matter_change_made_elsewhere(site):
    assert site.url_of(BUNDLE) == '/2024/05/hello-markdown.html'
    # Another tool swaps url: for slug: after the listing was cached.
    path = site.resolve(BUNDLE)
    text = path.read_text().replace('url: /2024/05/hello-markdown.html', 'slug: renamed')
    time.sleep(0.01)
    path.write_text(text)
    assert site.url_of(BUNDLE) == '/posts/2024/05/renamed/'


def test_write_refuses_a_post_changed_since_it_was_read(site):
    loaded = version(site.read(BUNDLE))
    site.write(BUNDLE, site.read(BUNDLE), loaded)             # unchanged: fine
    site.resolve(BUNDLE).write_text('changed elsewhere\n')
    with pytest.raises(Conflict):
        site.write(BUNDLE, 'mine\n', loaded)
    with pytest.raises(Conflict):
        site.set_draft(BUNDLE, True, loaded)
    with pytest.raises(Conflict):
        site.set_front(BUNDLE, 'title', 'x', loaded)
    assert site.read(BUNDLE) == 'changed elsewhere\n'
    site.write(BUNDLE, 'mine\n')                              # no version: overwrite, as before
    assert site.read(BUNDLE) == 'mine\n'


@pytest.mark.parametrize('text, expected', [
    ('---\ntitle: x\n---\n\n<iframe src="https://example.org"></iframe>\n', True),
    ('Intro\n\n<table><tr><td>a</td></tr></table>\n', True),
    ('Inline <b>bold</b> and <br> are fine for WYSIWYG.\n', False),
    ('```html\n<iframe src="x"></iframe>\n```\n', False),
    ('---\ntitle: "<div>"\n---\nplain\n', False),
])
def test_has_raw_html(text, expected):
    assert has_raw_html(text) is expected


@pytest.mark.parametrize('text, end', [
    ('---\ntitle: x\n---\nbody', len('---\ntitle: x\n---')),
    ('+++\ntitle = "x"\n+++\nbody', len('+++\ntitle = "x"\n+++')),
    ('{\n  "title": "a } b \\" {",\n  "c": {"d": 1}\n}\n\nbody', len('{\n  "title": "a } b \\" {",\n  "c": {"d": 1}\n}')),
    ('no front matter', 0),
])
def test_front_matter_end(text, end):
    assert front_matter_end(text) == end


def test_images_lists_and_deletes_only_image_files(site):
    images = site.images(BUNDLE)
    assert [i['name'] for i in images] == ['sample.webp'] and images[0]['url'] == 'sample.webp'
    assert site.images(DRAFT) == []                         # no static/images/... folder yet
    for bad in ('index.md', '../index.md', '.hidden.png', 'missing.webp', ''):
        with pytest.raises(SiteError):
            site.delete_image(BUNDLE, bad)
    site.delete_image(BUNDLE, 'sample.webp')
    assert site.images(BUNDLE) == [] and site.read(BUNDLE)   # the post itself is untouched


def test_links_to_and_replace_links():
    from hugo_writer.site import links_to, replace_links
    text = '![](a.webp) ![](a.webp#right) ![](/other/a.webp) ![](ba.webp) ![](a.webp2)'
    assert links_to(text, 'a.webp') and links_to(text, '/other/a.webp') and not links_to('![](/x/a.webp)', 'a.webp')
    assert replace_links(text, 'a.webp', 'p-01.webp') == \
        '![](p-01.webp) ![](p-01.webp#right) ![](/other/a.webp) ![](ba.webp) ![](a.webp2)'


@needs_hugo
def test_library_finds_images_and_who_uses_them(site):
    other = site.create('Other', 'other', now=NOW)
    site.write(other, site.read(other) + '\n![](/2024/05/hello-markdown/sample.webp#right)\n')
    (site.root / 'content/posts/2024/05/hello-markdown/unused.png').write_bytes(b'x')
    lib = {i['name']: i for i in site.library()}
    sample = lib['sample.webp']
    assert sample['url'] == '/2024/05/hello-markdown/sample.webp'
    assert sample['owner']['path'] == BUNDLE
    assert sorted(r['path'] for r in sample['refs']) == sorted([BUNDLE, other])
    assert lib['unused.png']['refs'] == []
    assert [i['name'] for i in site.library(only_post=BUNDLE)] == ['sample.webp', 'unused.png']


@needs_hugo
def test_rename_image_updates_every_post(site):
    other = site.create('Other', 'other', now=NOW)
    site.write(other, site.read(other) + '\n![](/2024/05/hello-markdown/sample.webp#right)\n')
    result = site.rename_image(BUNDLE, 'sample.webp')
    assert result['name'] == 'hello-markdown-01.webp' and sorted(result['updated']) == sorted([BUNDLE, other])
    assert '![](hello-markdown-01.webp)' in site.read(BUNDLE)
    assert '![](/2024/05/hello-markdown/hello-markdown-01.webp#right)' in site.read(other)
    assert (site.root / 'content/posts/2024/05/hello-markdown/hello-markdown-01.webp').is_file()


@needs_hugo
def test_delete_file_stays_inside_images(site):
    for bad in ('hugo.toml', BUNDLE, '../x.png', 'content/posts/2024/05/hello-markdown/missing.webp'):
        with pytest.raises(SiteError):
            site.delete_file(bad)
    site.delete_file('content/posts/2024/05/hello-markdown/sample.webp')
    assert site.library() == []


def test_settings_save_keeps_comments_and_validates(site_dir):
    site = Site(site_dir, HUGO)
    site.settings = site.settings.save(site.root, {'image_max_width': '800', 'strip_metadata': False, 'image_name': '{original}'})
    text = (site_dir / '.hugo-writer.toml').read_text()
    assert text.startswith('# New posts get a Blogger-style URL')          # comment kept
    assert 'image_max_width = 800' in text and 'strip_metadata = false' in text and 'image_name = "{original}"' in text
    assert site.settings.image_max_width == 800 and site.settings.strip_metadata is False
    for bad in ({'image_quality': 0}, {'image_format': 'bmp'}, {'image_name': '{nope}'}, {'colour': 'red'}):
        with pytest.raises(SiteError):
            site.settings.save(site.root, bad)


@needs_hugo
def test_rename_to_a_chosen_name(site):
    other = site.create('Other', 'other', now=NOW)
    site.write(other, site.read(other) + '\n![](/2024/05/hello-markdown/sample.webp)\n')
    result = site.rename_image(BUNDLE, 'sample.webp', 'GA4 Traffic Map')
    assert result['name'] == 'ga4-traffic-map.webp'
    assert '![](ga4-traffic-map.webp)' in site.read(BUNDLE)
    assert '![](/2024/05/hello-markdown/ga4-traffic-map.webp)' in site.read(other)
    assert site.rename_image(BUNDLE, 'ga4-traffic-map.webp', 'ga4-traffic-map.webp')['updated'] == []  # unchanged
    (site.root / 'content/posts/2024/05/hello-markdown/taken.webp').write_bytes(b'x')
    with pytest.raises(SiteError):
        site.rename_image(BUNDLE, 'ga4-traffic-map.webp', 'taken')


@needs_hugo
def test_rename_a_static_image(site):
    static = site.root / 'static/images/posts/2024/06/a-draft'
    static.mkdir(parents=True)
    (static / 'Shot 1.png').write_bytes(b'x')
    site.write(DRAFT, site.read(DRAFT) + '\n![](/images/posts/2024/06/a-draft/Shot 1.png)\n')
    result = site.rename_file('static/images/posts/2024/06/a-draft/Shot 1.png')
    assert result['name'] == 'a-draft-01.png' and result['updated'] == [DRAFT]
    assert '/images/posts/2024/06/a-draft/a-draft-01.png' in site.read(DRAFT)


@pytest.mark.parametrize('text, field, image, after', [
    # YAML, Hugo's "images"
    ('---\ntitle: x\n---\nbody', 'images', 'a.webp', '---\ntitle: x\nimages: ["a.webp"]\n---\nbody'),
    ('---\ntitle: x\nimages:\n  - old.png\n  - two.png\ntags: []\n---\nb', 'images', 'a.webp', '---\ntitle: x\nimages: ["a.webp"]\ntags: []\n---\nb'),
    ('---\ntitle: x\nimages: ["a.webp"]\n---\nb', 'images', None, '---\ntitle: x\n---\nb'),
    # YAML, PaperMod's nested "cover.image", other cover keys kept
    ('---\ntitle: x\n---\nb', 'cover.image', 'a.webp', '---\ntitle: x\ncover:\n  image: "a.webp"\n---\nb'),
    ('---\ncover:\n    alt: hi\n    image: old.png\n# note\n---\nb', 'cover.image', 'a.webp', '---\ncover:\n    alt: hi\n    image: "a.webp"\n# note\n---\nb'),
    ('---\ntitle: x\ncover:\n  image: "a.webp"\n---\nb', 'cover.image', None, '---\ntitle: x\n---\nb'),
    ('---\ncover:\n  image: a.webp\n  alt: hi\n---\nb', 'cover.image', None, '---\ncover:\n  alt: hi\n---\nb'),
    # TOML
    ('+++\ntitle = "x"\n+++\nb', 'images', 'a.webp', '+++\ntitle = "x"\nimages = ["a.webp"]\n+++\nb'),
    ('+++\ntitle = "x"\n[cover]\nalt = "hi"\n+++\nb', 'cover.image', 'a.webp', '+++\ntitle = "x"\n[cover]\nalt = "hi"\nimage = "a.webp"\n+++\nb'),
    ('+++\ntitle = "x"\n\n[cover]\nimage = "a.webp"\n+++\nb', 'cover.image', None, '+++\ntitle = "x"\n+++\nb'),
])
def test_set_featured(text, field, image, after):
    from hugo_writer.site import set_featured, get_featured
    assert set_featured(text, field, image) == after
    assert get_featured(after, field) == image


def test_featured_in_json_and_reading_lists():
    from hugo_writer.site import set_featured, get_featured
    text = '{\n  "title": "x"\n}\n\nbody'
    out = set_featured(text, 'cover.image', 'a.webp')
    assert get_featured(out, 'cover.image') == 'a.webp' and out.endswith('\n\nbody')
    assert get_featured(set_featured(out, 'cover.image', None), 'cover.image') is None
    assert get_featured('---\nimages:\n- first.png\n- second.png\n---\n', 'images') == 'first.png'
    with pytest.raises(SiteError):
        set_featured('---\ncover: {image: a}\n---\n', 'cover.image', 'b.webp')
    with pytest.raises(SiteError):
        set_featured('---\n---\n', 'a.b.c', 'x')


def test_site_featured_uses_the_setting(site_dir):
    site = Site(site_dir, HUGO)
    site.settings = site.settings.save(site.root, {'featured_field': 'cover.image'})
    text = site.set_featured(BUNDLE, 'sample.webp')
    assert 'cover:\n  image: "sample.webp"' in text and site.featured(BUNDLE) == 'sample.webp'
    assert 'title: "Hello, Markdown"' in text                     # the rest is untouched


def test_set_front_title_url_featured(site):
    text = site.set_front(BUNDLE, 'title', 'New "title"')
    assert 'title: "New \\"title\\""' in text and site.read(BUNDLE).count('title:') == 1
    assert 'url: "/2024/05/moved.html"' in site.set_front(BUNDLE, 'url', '2024/05/moved.html')
    assert 'url:' not in site.set_front(BUNDLE, 'url', '')
    assert 'images: ["sample.webp"]' in site.set_front(BUNDLE, 'featured', 'sample.webp')
    for field, bad in (('title', '  '), ('url', '/has space'), ('colour', 'red')):
        with pytest.raises(SiteError):
            site.set_front(BUNDLE, field, bad)
    with pytest.raises(SiteError):
        site.settings.save(site.root, {'featured_default': 'sometimes'})
