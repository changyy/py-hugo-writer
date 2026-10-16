import os
import shutil
from pathlib import Path

import pytest

from hugo_writer.site import Site

DEMO = Path(__file__).resolve().parent.parent / 'examples' / 'demo-site'

# The hugo to test against: HUGO=/path/to/hugo-0.128.0 uv run pytest
HUGO = os.environ.get('HUGO', 'hugo')
needs_hugo = pytest.mark.skipif(shutil.which(HUGO) is None, reason=f'{HUGO} is not installed')

BUNDLE = 'content/posts/2024/05/hello-markdown/index.md'
DRAFT = 'content/posts/2024/06/a-draft.md'
LEGACY = 'content/posts/2010/01/legacy.html'


@pytest.fixture
def site_dir(tmp_path):
    """A fresh copy of examples/demo-site; tests may change it freely."""
    return Path(shutil.copytree(DEMO, tmp_path / 'demo-site'))


@pytest.fixture
def site(site_dir):
    return Site(site_dir, HUGO)
