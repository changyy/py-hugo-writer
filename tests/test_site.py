import os
from datetime import datetime, timezone

import pytest

from hugo_writer.site import Settings, Site, SiteError, front_matter_end, has_raw_html, set_draft

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
