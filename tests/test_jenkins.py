"""vars/*.groovy executed by real Groovy against a fake Vault/Nexus and real ansible.

The pipeline DSL (sh, withEnv, withCredentials, ...) is mocked by tests/jenkins/harness.groovy, so
this proves the commands, quoting and secret handling, not Jenkins' CPS/sandbox behaviour.
Set GROOVY_CP to the groovy-4.x and groovy-json-4.x jars (joined with os.pathsep) to run these tests.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from test_pp import Fake, server  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "tests" / "jenkins" / "harness.groovy"
CP = os.environ.get("GROOVY_CP", "")  # groovy + groovy-json jars, joined with os.pathsep
pytestmark = [
    pytest.mark.skipif(
        not CP or not all(Path(j).is_file() for j in CP.split(os.pathsep)), reason="GROOVY_CP not set"
    ),
    pytest.mark.skipif(not shutil.which("java"), reason="java not installed"),
]


def groovy(scenario: str, tmp: Path, *, secrets=None, env=None, extra_env=None):
    f = tmp / "scenario.groovy"
    f.write_text(scenario, encoding="utf-8")
    e = {
        **os.environ,
        "HARNESS_SECRETS": json.dumps(secrets or {}),
        "HARNESS_ENV": json.dumps(env or {}),
        **(extra_env or {}),
    }
    return subprocess.run(
        ["java", "-cp", CP, "groovy.ui.GroovyMain", str(HARNESS), str(ROOT), str(f)],
        cwd=tmp, env=e, capture_output=True, text=True, timeout=120,
    )  # fmt: skip


def test_vault_secrets_exposes_env_and_files_then_cleans_up(server, tmp_path):  # noqa: F811
    res = groovy(
        f"""
        vaultSecrets(url: '{server}', credentialsId: 'vault-approle',
                     env: [NEXUS_USER: 'ci/nexus/docker#user', NEXUS_PASSWORD: 'ci/nexus/docker#password'],
                     files: [SSH_KEY: 'ci/nexus/docker#key']) {{
            sh 'printf "%s:%s" "$NEXUS_USER" "$NEXUS_PASSWORD" > got.txt; cp "$SSH_KEY" key.txt; echo "$SSH_KEY" > keypath.txt'
        }}
        """,
        tmp_path,
        secrets={"vault-approle": {"username": "rid", "password": "sid"}},
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert (tmp_path / "got.txt").read_text() == "u1:p1"
    assert (tmp_path / "key.txt").read_text().startswith("-----BEGIN\nabc")
    assert not Path((tmp_path / "keypath.txt").read_text().strip()).exists()  # removed after the block
    login = next(e for e in Fake.log if e[1].endswith("/login"))
    assert json.loads(login[3]) == {"role_id": "rid", "secret_id": "sid"}
    assert ":p1" not in res.stdout and "=p1" not in res.stdout  # the password never reaches the console


def test_vault_secrets_cleans_up_when_the_body_fails_and_needs_credentials(server, tmp_path):  # noqa: F811
    res = groovy(
        f"vaultSecrets(url: '{server}', tokenId: 'tok', files: [K: 'ci/nexus/docker#key']) {{ sh 'echo $K > p.txt; exit 3' }}",
        tmp_path,
        secrets={"tok": "s.abc"},
    )
    assert res.returncode != 0
    assert not Path((tmp_path / "p.txt").read_text().strip()).exists()
    res = groovy(f"vaultSecrets(url: '{server}', env: [A: 'ci/x#y']) {{ }}", tmp_path)
    assert "credentialsId (AppRole) or tokenId" in res.stdout + res.stderr


def test_nexus_and_artifactory_steps(server, tmp_path, monkeypatch):  # noqa: F811
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "a b.tgz").write_bytes(b"AAA")
    res = groovy(
        f"""
        nexusPut(url: '{server}', repo: 'raw', files: 'dist/*.tgz', dest: "app/${{env.BUILD_NUMBER}}", credentialsId: 'nx')
        artifactoryPut(url: '{server}', repo: 'gen', files: ['dist/*.tgz'], props: [build: env.BUILD_NUMBER], tokenId: 'af')
        nexusGet(url: '{server}', repo: 'raw', path: 'app/7/a b.tgz', out: 'got.bin')
        """,
        tmp_path,
        env={"BUILD_NUMBER": "7"},
        secrets={"nx": {"username": "ci", "password": "pw"}, "af": "tok"},
    )
    assert res.returncode == 0, res.stdout + res.stderr
    puts = [e for e in Fake.log if e[0] == "PUT"]
    assert puts[0][1] == "/repository/raw/app/7/a%20b.tgz" and puts[0][2]["Authorization"].startswith(
        "Basic "
    )
    assert puts[1][1] == "/artifactory/gen/a%20b.tgz;build=7"
    assert puts[1][2]["Authorization"] == "Bearer tok" and puts[1][1].endswith(";build=7")
    assert (tmp_path / "got.bin").read_bytes() == b"file-bytes"


def test_docker_build_push_uses_stdin_password_and_cleans_up(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "docker.log"
    stub = bindir / "docker"
    stub.write_text(
        f'#!/bin/sh\necho "$@" >> {log}\nif [ "$1" = login ]; then cat > {tmp_path}/stdin.txt; fi\n'
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    res = groovy(
        "dockerBuildPush(registry: 'nexus.example.com:8082', image: 'shop/app', credentialsId: 'reg', context: 'ctx dir')",
        tmp_path,
        env={"GIT_COMMIT": "0123456789abcdef", "TAG_NAME": "v1.2"},
        secrets={"reg": {"username": "ci", "password": "s3cret"}},
        extra_env={"PATH": f"{bindir}:{os.environ['PATH']}"},
    )
    assert res.returncode == 0, res.stdout + res.stderr
    lines = log.read_text().splitlines()
    assert lines[0] == "login nexus.example.com:8082 -u ci --password-stdin"
    assert (tmp_path / "stdin.txt").read_text() == "s3cret" and "s3cret" not in res.stdout
    assert (
        lines[1]
        == "build -f Dockerfile -t nexus.example.com:8082/shop/app:01234567 -t nexus.example.com:8082/shop/app:v1.2 ctx dir"
    )
    assert lines[2:] == [
        "push nexus.example.com:8082/shop/app:01234567",
        "push nexus.example.com:8082/shop/app:v1.2",
    ]


@pytest.mark.skipif(not shutil.which("ansible-playbook"), reason="ansible not installed")
def test_ansible_role_and_run_steps_with_real_ansible(tmp_path):
    role = tmp_path / "role"
    shutil.copytree(ROOT / "examples" / "gitlab" / "ansible-role", role)
    target = tmp_path / "out file"
    extra = json.dumps({"motd_path": str(target), "motd_text": "it works"})  # JSON: the path has a space
    args = f"args: ['-e', '{extra}']"
    res = groovy(f"ansibleRole(role: 'motd', inventory: 'tests/inventory', check: true, {args})", role)
    assert res.returncode == 0, res.stdout + res.stderr
    assert not target.exists()
    res = groovy(f"ansibleRole(role: 'motd', inventory: 'tests/inventory', {args})", role)
    assert res.returncode == 0, res.stdout + res.stderr
    assert target.read_text() == "it works\n"
    assert groovy("ansibleRole(role: 'motd', syntax: true)", role).returncode == 0
    missing = groovy("ansibleRole(inventory: 'x')", role)
    assert "role is required" in missing.stderr
    pb = tmp_path / "pb"
    shutil.copytree(ROOT / "examples" / "gitlab" / "ansible-playbook", pb)
    assert groovy("ansibleRun(playbook: 'site.yml', syntax: true)", pb).returncode == 0
    assert groovy("ansibleRun(playbook: 'nope.yml', syntax: true)", pb).returncode != 0


def test_ssh_key_credentials_become_ansible_environment(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "ansible-playbook"
    stub.write_text('#!/bin/sh\necho "$ANSIBLE_PRIVATE_KEY_FILE|$ANSIBLE_REMOTE_USER|$*" > seen.txt\n')
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    res = groovy(
        "ansibleRun(playbook: 'site.yml', inventory: 'inv', sshKeyId: 'k', args: ['-e', 'a=b c', '--tags', 'x'])",
        tmp_path,
        secrets={"k": {"file": "/tmp/keyfile", "user": "deploy"}},
        extra_env={"PATH": f"{bindir}:{os.environ['PATH']}"},
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert (tmp_path / "seen.txt").read_text() == "/tmp/keyfile|deploy|-i inv site.yml -e a=b c --tags x\n"


def test_every_groovy_file_compiles():
    files = [
        *sorted((ROOT / "vars").glob("*.groovy")),
        *sorted((ROOT / "examples" / "jenkins").glob("Jenkinsfile.*")),
    ]
    assert len(files) >= 16
    res = subprocess.run(
        ["java", "-cp", CP, "groovy.ui.GroovyMain", str(ROOT / "tests/jenkins/syntax.groovy"), *map(str, files)],
        capture_output=True, text=True, timeout=120,
    )  # fmt: skip
    assert res.returncode == 0, res.stdout
