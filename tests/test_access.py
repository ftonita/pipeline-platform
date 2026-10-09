"""access.yml: validation, rendered Vault policies / roles / RBAC (golden), `who`."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from pipeline_platform import access
from pipeline_platform.cli import main

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "access"


@pytest.fixture
def doc():
    return yaml.safe_load((EXAMPLE / "access.yml").read_text(encoding="utf-8"))


def problems(d):
    return [str(i) for i in access.validate(d)]


def test_example_is_valid(doc):
    assert problems(doc) == []


def test_rendered_files_match_golden(doc):
    expected = EXAMPLE / "expected"
    rendered = access.render(doc)
    assert sorted(rendered) == sorted(
        str(p.relative_to(expected)) for p in expected.rglob("*") if p.is_file()
    )
    for rel, text in rendered.items():
        assert (expected / rel).read_text(encoding="utf-8") == text, f"{rel}: run scripts/update_golden.py"


def test_narrow_rule_keeps_the_rights_of_the_wide_glob(doc):
    # Vault applies only the most specific path: ci/platform/* must repeat read from ci/*.
    hcl = access.render(doc)["vault/policies/team-platform.hcl"]
    assert 'path "ci/data/platform/*" {\n  capabilities = ["create", "read", "update", "delete"]' in hcl
    assert 'path "ci/data/*" {\n  capabilities = ["read"]' in hcl


def test_teams_cannot_see_each_others_sections(doc):
    orders = access.render(doc)["vault/policies/team-orders.hcl"]
    assert (
        "payments" not in orders
        and 'path "ci/data/orders/*"' in orders
        and 'path "ci/metadata/orders/*"' in orders
    )
    assert access.who(doc, "dave") == [
        "user dave: vault ci/orders/* [kv2] read",
        "user dave: k8s orders-dev view",
    ]


def test_group_team_binds_group_and_member_team_binds_users(doc):
    rbac = [d for d in yaml.safe_load_all(access.render(doc)["k8s/rbac.yaml"].split("\n", 1)[1])]
    by_name = {(d["metadata"]["namespace"], d["metadata"]["name"]): d for d in rbac}
    assert by_name["payments-dev", "pp-team-payments-edit"]["subjects"][0]["kind"] == "Group"
    assert [s["name"] for s in by_name["orders-dev", "pp-team-orders-edit"]["subjects"]] == ["alice", "bob"]
    assert by_name["payments-prod", "pp-team-payments-view"]["roleRef"]["name"] == "view"


def test_ci_role_is_bound_to_project_and_protected_refs(doc):
    role = json.loads(access.render(doc)["vault/roles/ci-shop-orders-api.json"])
    assert role["policies"] == ["team-orders"]
    assert role["bound_claims"] == {"project_path": "shop/orders-api", "ref_protected": "true"}
    assert role["token_explicit_max_ttl"] == 900


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda d: d["grants"][0].update(team="nope"), "unknown team 'nope'"),
        (lambda d: d["grants"][0]["vault"][0].update(path="sys/policies/*"), "administrative or root-wide"),
        (lambda d: d["grants"][0]["vault"][0].update(path="auth/jwt/role/*"), "administrative or root-wide"),
        (lambda d: d["grants"][0]["vault"][0].update(path="*"), "administrative or root-wide"),
        (lambda d: d["grants"][0]["vault"][0].update(path="onlymount"), "need mount/path"),
        (lambda d: d["grants"][0]["vault"][0].update(can=["sudo"]), "is not one of"),
        (lambda d: d["grants"][0]["k8s"][0].update(role="cluster-admin"), "cluster-admin is not granted"),
        (lambda d: d["grants"][0]["k8s"][0].update(namespace="*"), "does not match"),
        (lambda d: d["grants"][0].update(user="x"), "valid under each of"),
        (lambda d: d["teams"].update(empty={}), "is not valid under any"),
        (lambda d: d["ci"].append(copy.deepcopy(d["ci"][0])), "duplicate project"),
        (lambda d: d.update(surprise=1), "surprise"),
    ],
)
def test_invalid_access_is_rejected(doc, mutate, expected):
    mutate(doc)
    assert any(expected in p for p in problems(doc)), problems(doc)


def test_cli_validate_render_who(tmp_path, capsys):
    cfg = str(EXAMPLE / "access.yml")
    assert main(["access", "validate", "--config", cfg]) == 0
    assert main(["access", "render", "--config", cfg, "--out", str(tmp_path)]) == 0
    assert (tmp_path / "vault" / "apply.sh").stat().st_mode & 0o111
    capsys.readouterr()
    assert main(["access", "who", "--config", cfg, "alice"]) == 0
    assert "team orders: k8s orders-prod view" in capsys.readouterr().out
    assert main(["access", "who", "--config", cfg, "nobody"]) == 0
    assert "no rights granted" in capsys.readouterr().out
    bad = tmp_path / "bad.yml"
    bad.write_text("version: 1\nteams: {}\n")
    assert main(["access", "validate", "--config", str(bad)]) == 2
    assert main(["access", "validate", "--config", str(tmp_path / "missing.yml")]) == 2
