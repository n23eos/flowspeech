"""Integrity checks for the static FlowSpeech product pages."""

import re
from pathlib import Path


SITE_ROOT = Path(__file__).parent.parent / "website"
PAGES = ("index.html", "daily.html", "privacy.html", "download.html")


def _local_paths(html: str) -> set[str]:
    return {
        path
        for path in re.findall(r'(?:href|src)="([^"]+)"', html)
        if not path.startswith(("http://", "https://", "#"))
    }


def test_every_page_has_a_title_main_content_and_navigation():
    for name in PAGES:
        html = (SITE_ROOT / name).read_text(encoding="utf-8")

        assert "<title" in html
        assert "<main>" in html
        assert "href=\"index.html\"" in html
        assert "href=\"daily.html\"" in html
        assert "href=\"privacy.html\"" in html
        assert "href=\"download.html\"" in html


def test_every_local_page_link_and_shared_asset_exists():
    for name in PAGES:
        html = (SITE_ROOT / name).read_text(encoding="utf-8")
        for path in _local_paths(html):
            clean_path = path.split("#", maxsplit=1)[0]
            assert (SITE_ROOT / clean_path).is_file(), f"{name} links to missing {path}"


def test_daily_page_explains_the_default_dated_file():
    html = (SITE_ROOT / "daily.html").read_text(encoding="utf-8")

    assert "YYYY-MM-DD.md" in html


def test_english_is_default_and_six_locales_are_available():
    home = (SITE_ROOT / "index.html").read_text(encoding="utf-8")
    script = (SITE_ROOT / "assets" / "app.js").read_text(encoding="utf-8")

    assert '<html lang="en">' in home
    for locale in ("en", "ru", "es", "pt", "de", "fr"):
        assert f'value="{locale}"' in home
        assert f"translations.{locale}" in script or f"{locale}: {{" in script
