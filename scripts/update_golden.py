"""Regenerate the golden files (consumer-app pipelines, access output). Review the diff before committing."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from golden import CONTEXTS, VARIANTS, golden_path, render  # noqa: E402

for variant in VARIANTS:
    for ctx_name in CONTEXTS:
        path = golden_path(variant, ctx_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(variant, ctx_name), encoding="utf-8")
        print("wrote", path.relative_to(ROOT))

# examples/access/expected
import shutil  # noqa: E402

import yaml  # noqa: E402

from pipeline_platform import access  # noqa: E402

acc = ROOT / "examples" / "access"
shutil.rmtree(acc / "expected", ignore_errors=True)
for rel, text in access.render(yaml.safe_load((acc / "access.yml").read_text(encoding="utf-8"))).items():
    (acc / "expected" / rel).parent.mkdir(parents=True, exist_ok=True)
    (acc / "expected" / rel).write_text(text, encoding="utf-8")
    if rel.endswith(".sh"):
        (acc / "expected" / rel).chmod(0o755)
    print("wrote", (acc / "expected" / rel).relative_to(ROOT))
