"""gitlab/*.yml: structure, GitLab schema, and real execution of the scripts where possible."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

import gitlab_ci as g

ROOT = g.ROOT
EXAMPLES = ROOT / "examples" / "gitlab"
CONSUMERS = sorted(p.parent for p in EXAMPLES.glob("*/.gitlab-ci.yml"))
SCHEMA = os.environ.get("GITLAB_CI_SCHEMA", "")


def ids(paths):
    return [p.name for p in paths]


def run_job(doc: dict, name: str, cwd: Path, env: dict[str, str], tmp: Path):
    """Run before_script + script of a resolved job in bash, like the runner's shell would."""
    job = g.resolve(name, doc)
    lines = [*job.get("before_script", []), *job["script"]]
    e = {**os.environ, **doc.get("variables", {}), **job.get("variables", {}), **env}
    e = {
        k: v for k, v in e.items() if "$" not in str(v)
    }  # unexpanded GitLab references are not testable here
    return subprocess.run(["bash", "-ec", "\n".join(lines)], cwd=cwd, env=e, capture_output=True, text=True)


@pytest.mark.parametrize("example", CONSUMERS, ids=ids(CONSUMERS))
def test_every_job_resolves_and_has_something_to_run(example):
    doc = g.consumer(example)
    assert g.jobs(doc), "example defines no jobs"
    for name in [*g.jobs(doc), *(k for k in doc if k.startswith("."))]:
        job = g.resolve(name, doc)  # raises on unknown / looping extends
        if not name.startswith("."):
            assert "script" in job or "trigger" in job, name


@pytest.mark.skipif(not Path(SCHEMA).is_file(), reason="GITLAB_CI_SCHEMA not set")
@pytest.mark.parametrize("file", sorted(g.TEMPLATES.glob("*.yml")), ids=lambda p: p.name)
def test_templates_are_valid_gitlab_ci(file):
    from jsonschema import Draft7Validator

    v = Draft7Validator(json.loads(Path(SCHEMA).read_text(encoding="utf-8")))
    assert [e.message[:150] for e in v.iter_errors(g.load(file))] == []


@pytest.mark.skipif(not Path(SCHEMA).is_file(), reason="GITLAB_CI_SCHEMA not set")
@pytest.mark.parametrize("example", CONSUMERS, ids=ids(CONSUMERS))
def test_examples_are_valid_gitlab_ci(example):
    from jsonschema import Draft7Validator

    v = Draft7Validator(json.loads(Path(SCHEMA).read_text(encoding="utf-8")))
    for doc in (g.load(example / ".gitlab-ci.yml"), g.consumer(example)):
        assert [e.message[:150] for e in v.iter_errors(doc)] == []


def test_job_variable_defaults_do_not_shadow_consumer_variables():
    # Job-level `variables:` beat the consumer's global ones, so templates may only set the
    # variables that are meant to differ per job (ANSIBLE_MODE, the localhost syntax inventory).
    allowed = {"ANSIBLE_MODE", "ANSIBLE_INVENTORY", "ANSIBLE_FORCE_COLOR", "PIP_CACHE_DIR"}
    doc = g.template_files(*(p.name for p in g.TEMPLATES.glob("*.yml")))
    for name in doc:
        if isinstance(doc[name], dict) and name not in g.RESERVED:
            assert set(doc[name].get("variables", {})) <= allowed, name


def test_vault_export_hands_over_through_a_restricted_short_lived_artifact():
    job = g.resolve(".vault-export", g.template_files("vault.yml"))
    assert job["artifacts"]["access"] == "none" and job["artifacts"]["expire_in"] == "1 hour"
    assert job["artifacts"]["reports"] == {"dotenv": "vault.env"} and job["id_tokens"]["VAULT_ID_TOKEN"]


def test_example_ready_pipelines_gate_apply_on_inventory_and_manual():
    for ex in ("ansible-role", "ansible-playbook"):
        apply_job = g.resolve(f"{ex}:apply", g.consumer(EXAMPLES / ex))
        assert (
            apply_job["rules"][0]["when"] == "manual" and "$ANSIBLE_INVENTORY" in apply_job["rules"][0]["if"]
        )


# ---- real execution -----------------------------------------------------------------

needs_ansible = pytest.mark.skipif(not shutil.which("ansible-playbook"), reason="ansible not installed")


@needs_ansible
def test_role_block_applies_the_role_of_this_repository(tmp_path):
    ex = EXAMPLES / "ansible-role"
    doc = g.consumer(ex)
    target = tmp_path / "motd"
    env = {"CI_PROJECT_NAME": "ansible-role-motd", "ANSIBLE_ARGS": f"-e motd_path={target} -e motd_text=hi"}
    # dry run changes nothing, apply writes the file, a second apply is idempotent
    check = run_job(doc, "ansible-role:check", ex, env, tmp_path)
    assert check.returncode == 0, check.stdout + check.stderr
    assert not target.exists()
    for expected in ("changed=1", "changed=0"):
        applied = run_job(doc, "ansible-role:apply", ex, env, tmp_path)
        assert applied.returncode == 0, applied.stdout + applied.stderr
        assert expected in applied.stdout
    assert target.read_text() == "hi\n"
    assert run_job(doc, "ansible-role:syntax", ex, env, tmp_path).returncode == 0


@needs_ansible
def test_playbook_block_syntax_check(tmp_path):
    ex = EXAMPLES / "ansible-playbook"
    res = run_job(g.consumer(ex), "ansible-playbook:syntax", ex, {}, tmp_path)
    assert res.returncode == 0, res.stdout + res.stderr


def test_nexus_and_artifactory_blocks_pass_the_right_arguments(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "pp"
    stub.write_text('#!/bin/sh\nfor a in "$@"; do printf "[%s]" "$a"; done; echo\n')
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    doc = g.template_files("nexus.yml", "artifactory.yml")
    base = {
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "CI_PROJECT_NAME": "app",
        "CI_COMMIT_SHORT_SHA": "abc123",
        "CI_PIPELINE_ID": "7",
        "CI_COMMIT_SHA": "deadbeef",
    }

    def out(name, **env):
        r = run_job(doc, name, tmp_path, {**base, **env}, tmp_path)
        assert r.returncode == 0, r.stderr
        return r.stdout.strip()

    assert (
        out(".nexus-put", NEXUS_REPO="raw", NEXUS_FILES="a.tgz b.tgz")
        == "[nexus][put][raw][a.tgz][b.tgz][--dest][app/abc123]"
    )
    assert (
        out(".nexus-put", NEXUS_REPO="raw", NEXUS_FILES="a", CI_COMMIT_TAG="v1", NEXUS_DEST="x y")
        == "[nexus][put][raw][a][--dest][x y]"
    )
    assert out(".nexus-get", NEXUS_REPO="raw", NEXUS_PATH="p/f") == "[nexus][get][raw][p/f]"
    assert (
        out(".nexus-get", NEXUS_REPO="raw", NEXUS_PATH="p/f", NEXUS_OUT="o f")
        == "[nexus][get][raw][p/f][--out][o f]"
    )
    assert out(".artifactory-put", ARTIFACTORY_REPO="gen", ARTIFACTORY_FILES="a", CI_COMMIT_TAG="v2") == (
        "[artifactory][put][gen][a][--dest][app/v2][--prop][build=7][--prop][vcs.revision=deadbeef]"
    )
    assert (
        out(".artifactory-get", ARTIFACTORY_REPO="gen", ARTIFACTORY_PATH="p/f")
        == "[artifactory][get][gen][p/f]"
    )
