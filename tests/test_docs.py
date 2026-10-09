"""The two READMEs stay in sync and their relative links resolve."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = {"en": ROOT / "README.md", "ru": ROOT / "README.ru.md"}


@pytest.mark.parametrize("lang", DOCS)
def test_relative_links_resolve(lang):
    text = DOCS[lang].read_text(encoding="utf-8")
    for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", text):
        if not target.startswith(("http://", "https://", "mailto:")):
            assert (ROOT / target).exists(), f"{DOCS[lang].name}: broken link {target}"


@pytest.mark.parametrize("lang", DOCS)
def test_anchor_links_point_to_headings(lang):
    text = DOCS[lang].read_text(encoding="utf-8")
    slugs = {
        re.sub(r"[^\w\- ]", "", h.lower()).strip().replace(" ", "-")
        for h in re.findall(r"^#{1,4} (.+)$", text, flags=re.M)
    }
    for anchor in re.findall(r"\]\(#([^)]+)\)", text):
        assert anchor in slugs, f"{DOCS[lang].name}: no heading for #{anchor}"


def test_language_switch_and_same_structure():
    en, ru = (DOCS[k].read_text(encoding="utf-8") for k in ("en", "ru"))
    assert en.startswith('<p align="right">') and "README.ru.md" in en.splitlines()[0]
    assert ru.startswith('<p align="right">') and "README.md" in ru.splitlines()[0]
    for pattern in (r"^## ", r"^### ", r"^```", r"^\|---"):
        assert len(re.findall(pattern, en, flags=re.M)) == len(re.findall(pattern, ru, flags=re.M)), pattern
