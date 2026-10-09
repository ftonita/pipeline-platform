"""Tiny GitLab CI config reader for tests: merges the templates and resolves `extends`."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "gitlab"
RESERVED = {
    "include",
    "stages",
    "variables",
    "default",
    "workflow",
    "image",
    "cache",
    "before_script",
    "after_script",
}


def load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def merge(base: dict, over: dict) -> dict:
    """GitLab's rule: hashes merge recursively, everything else (lists, scalars) is replaced."""
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = (
            merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
        )
    return out


def combine(*docs: dict) -> dict:
    """Included files first, then the including file (which wins)."""
    out: dict = {}
    for d in docs:
        out = merge(out, d)
    return out


def resolve(name: str, doc: dict, _seen: tuple = ()) -> dict:
    """The job `name` with `extends` applied (later entries win, the job itself wins last)."""
    if name in _seen:
        raise ValueError(f"extends loop: {' -> '.join((*_seen, name))}")
    job = doc[name]
    parents = job.get("extends", [])
    parents = [parents] if isinstance(parents, str) else parents
    merged: dict = {}
    for p in parents:
        if p not in doc:
            raise KeyError(f"{name}: extends unknown job {p!r}")
        merged = merge(merged, resolve(p, doc, (*_seen, name)))
    return merge(merged, {k: v for k, v in job.items() if k != "extends"})


def jobs(doc: dict) -> list[str]:
    return [k for k, v in doc.items() if k not in RESERVED and not k.startswith(".") and isinstance(v, dict)]


def template_files(*names: str) -> dict:
    return combine(*(load(TEMPLATES / n) for n in names))


def consumer(example_dir: Path) -> dict:
    """Example `.gitlab-ci.yml` with its `include: project/file` entries replaced by gitlab/*.yml."""
    cfg = load(example_dir / ".gitlab-ci.yml")
    files: list[str] = []
    for inc in cfg.pop("include", []):
        f = inc["file"]
        files += [f] if isinstance(f, str) else f
    return combine(*(load(ROOT / f) for f in files), cfg)
