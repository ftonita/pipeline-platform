"""resources/pp.py against a fake Vault / Nexus / Artifactory (plain HTTP on localhost)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "pp", Path(__file__).resolve().parent.parent / "resources" / "pp.py"
)
pp = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pp)

KV2 = {"data": {"data": {"user": "u1", "password": "p1", "key": "-----BEGIN\nabc\n-----END\n"}}}
RESPONSES = {
    "/v1/ci/data/nexus/docker": KV2,
    "/v1/legacy/nexus": {"data": {"password": "kv1-pass"}},
    "/v1/database/creds/ro": {"data": {"username": "dyn", "password": "dyn-pass"}},
}


class Fake(BaseHTTPRequestHandler):
    log: list[tuple[str, str, dict, bytes]] = []
    reads: dict[str, int] = {}

    def log_message(self, *a):  # keep pytest output quiet
        pass

    def _reply(self, code, obj=None, raw=None, headers=None):
        body = raw if raw is not None else json.dumps(obj or {}).encode()
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def do_POST(self):
        body = self._body()
        Fake.log.append(("POST", self.path, dict(self.headers), body))
        if self.path.endswith("/login"):
            self._reply(200, {"auth": {"client_token": "s.client"}})
        else:
            self._reply(204, raw=b"")

    def do_PUT(self):
        Fake.log.append(("PUT", self.path, dict(self.headers), self._body()))
        self._reply(201 if "fail" not in self.path else 403, raw=b"")

    def do_GET(self):
        Fake.log.append(("GET", self.path, dict(self.headers), b""))
        Fake.reads[self.path] = Fake.reads.get(self.path, 0) + 1
        if self.path in RESPONSES:
            self._reply(200, RESPONSES[self.path])
        elif self.path == "/repository/raw/hop":
            self._reply(
                302, raw=b"", headers={"Location": f"http://localhost:{self.server.server_port}/blob"}
            )
        elif self.path.startswith(("/repository/", "/artifactory/", "/blob")):
            self._reply(200, raw=b"file-bytes")
        else:
            self._reply(404, {"errors": ["nope"]})


@pytest.fixture
def server(monkeypatch, tmp_path):
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.01), daemon=True).start()
    Fake.log, Fake.reads = [], {}
    for k in list(os.environ):
        if k.startswith(("VAULT_", "NEXUS_", "ARTIFACTORY_")):
            monkeypatch.delenv(k)
    monkeypatch.setenv("PP_ALLOW_HTTP", "1")
    monkeypatch.setenv("PP_NO_SLEEP", "1")
    monkeypatch.chdir(tmp_path)
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def run(*argv):
    return pp.main(list(argv))


# ---- spec parsing


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("ci/nexus/docker#user", ("kv2", "ci/data/nexus/docker", "user")),
        ("kv2:ci/a#b", ("kv2", "ci/data/a", "b")),
        ("kv1:legacy/nexus#password", ("kv1", "legacy/nexus", "password")),
        ("raw:database/creds/ro#username", ("raw", "database/creds/ro", "username")),
    ],
)
def test_parse_spec(spec, expected):
    assert pp.parse_spec(spec) == expected


@pytest.mark.parametrize("spec", ["nofield", "kv2:onlymount#f", "#f"])
def test_parse_spec_rejects(spec):
    with pytest.raises(pp.PPError):
        pp.parse_spec(spec)


def test_parse_pairs_skips_comments_and_validates_names():
    assert pp.parse_pairs("# c\n\nA=ci/x#y\n") == [("A", "ci/x#y")]
    with pytest.raises(pp.PPError):
        pp.parse_pairs("1BAD=ci/x#y")


# ---- vault


def test_vault_jwt_exports_env_and_files_and_revokes(server, monkeypatch, tmp_path):
    monkeypatch.setenv("VAULT_ADDR", server)
    monkeypatch.setenv("VAULT_ID_TOKEN", "jwt-abc")
    monkeypatch.setenv("VAULT_AUTH_ROLE", "ci-demo")
    monkeypatch.setenv("VAULT_NAMESPACE", "team-a")
    monkeypatch.setenv(
        "VAULT_ENV", "NEXUS_USER=ci/nexus/docker#user\nNEXUS_PASSWORD=ci/nexus/docker#password"
    )
    assert run("vault", "export", "--file", "SSH_KEY=ci/nexus/docker#key") == 0
    env = dict(line.split("=", 1) for line in Path("vault.env").read_text().splitlines())
    assert env["NEXUS_USER"] == "u1" and env["NEXUS_PASSWORD"] == "p1"
    key = Path(env["SSH_KEY"])
    assert key.read_text().startswith("-----BEGIN\nabc") and stat.S_IMODE(key.stat().st_mode) == 0o600
    assert stat.S_IMODE(Path(".vault-files").stat().st_mode) == 0o700
    assert stat.S_IMODE(Path("vault.env").stat().st_mode) == 0o600
    login = Fake.log[0]
    assert login[1] == "/v1/auth/jwt/login" and json.loads(login[3]) == {"role": "ci-demo", "jwt": "jwt-abc"}
    assert login[2]["X-Vault-Namespace"] == "team-a"
    assert Fake.reads["/v1/ci/data/nexus/docker"] == 1  # three fields, one read
    assert Fake.log[-1][1] == "/v1/auth/token/revoke-self" and Fake.log[-1][2]["X-Vault-Token"] == "s.client"


def test_vault_approle_kv1_and_dynamic_engine(server, monkeypatch):
    monkeypatch.setenv("VAULT_SERVER_URL", server)
    monkeypatch.setenv("VAULT_ROLE_ID", "rid")
    monkeypatch.setenv("VAULT_SECRET_ID", "sid")
    assert (
        run(
            "vault",
            "export",
            "--env",
            "A=kv1:legacy/nexus#password",
            "--env",
            "DB_USER=raw:database/creds/ro#username",
            "--env",
            "DB_PASS=raw:database/creds/ro#password",
        )
        == 0
    )
    assert (
        "A=kv1-pass" in Path("vault.env").read_text() and "DB_PASS=dyn-pass" in Path("vault.env").read_text()
    )
    assert Fake.log[0][1] == "/v1/auth/approle/login"
    assert Fake.reads["/v1/database/creds/ro"] == 1  # dynamic creds must be issued once


def test_vault_token_is_used_as_is_and_not_revoked(server, monkeypatch):
    monkeypatch.setenv("VAULT_ADDR", server)
    monkeypatch.setenv("VAULT_TOKEN", "s.mine")
    assert run("vault", "export", "--env", "A=kv1:legacy/nexus#password") == 0
    assert [m for m, *_ in Fake.log] == ["GET"] and Fake.log[0][2]["X-Vault-Token"] == "s.mine"


def test_vault_errors_are_readable_and_secret_free(server, monkeypatch, capsys):
    monkeypatch.setenv("VAULT_ADDR", server)
    monkeypatch.setenv("VAULT_TOKEN", "s.mine")
    assert run("vault", "export", "--env", "A=kv1:legacy/nexus#missing") == 1
    assert "field 'missing' not found" in capsys.readouterr().err
    assert run("vault", "export", "--env", "A=ci/nope/x#y") == 1  # 404
    err = capsys.readouterr().err
    assert "HTTP 404" in err and "s.mine" not in err
    assert run("vault", "export", "--env", "K=ci/nexus/docker#key") == 1  # multi-line value
    assert "use VAULT_FILES" in capsys.readouterr().err
    assert run("vault", "export") == 1
    assert not Path("vault.env").exists()


def test_http_is_refused_by_default(monkeypatch, capsys):
    monkeypatch.delenv("PP_ALLOW_HTTP", raising=False)
    monkeypatch.setenv("VAULT_ADDR", "http://vault.example.com")
    monkeypatch.setenv("VAULT_TOKEN", "t")
    assert run("vault", "export", "--env", "A=ci/x#y") == 1
    assert "non-https" in capsys.readouterr().err


# ---- nexus / artifactory


def test_nexus_put_globs_and_basic_auth(server, monkeypatch, tmp_path):
    monkeypatch.setenv("NEXUS_URL", server)
    monkeypatch.setenv("NEXUS_USER", "ci")
    monkeypatch.setenv("NEXUS_PASSWORD", "pw")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "app 1.tgz").write_bytes(b"AAA")
    (tmp_path / "dist" / "app2.tgz").write_bytes(b"BB")
    assert run("nexus", "put", "raw-rel", "dist/*.tgz", "--dest", "demo/1.0") == 0
    puts = [e for e in Fake.log if e[0] == "PUT"]
    assert [p[1] for p in puts] == [
        "/repository/raw-rel/demo/1.0/app%201.tgz",
        "/repository/raw-rel/demo/1.0/app2.tgz",
    ]
    assert puts[0][3] == b"AAA" and puts[0][2]["Authorization"].startswith("Basic ")
    assert "X-Checksum-Sha1" not in puts[0][2]


def test_artifactory_put_adds_checksum_properties_and_bearer(server, monkeypatch, tmp_path):
    monkeypatch.setenv("ARTIFACTORY_URL", server)
    monkeypatch.setenv("ARTIFACTORY_TOKEN", "tok")
    (tmp_path / "a.zip").write_bytes(b"zip")
    assert (
        run("artifactory", "put", "generic-local", "a.zip", "--prop", "build=7", "--prop", "branch=main") == 0
    )
    method, path, headers, body = Fake.log[0]
    assert path == "/artifactory/generic-local/a.zip;build=7;branch=main" and body == b"zip"
    assert headers["Authorization"] == "Bearer tok"
    assert headers["X-Checksum-Sha1"] == hashlib.sha1(b"zip").hexdigest()


def test_get_follows_redirect_to_another_host_without_credentials(server, monkeypatch, tmp_path):
    monkeypatch.setenv("NEXUS_URL", server)
    monkeypatch.setenv("NEXUS_USER", "ci")
    monkeypatch.setenv("NEXUS_PASSWORD", "tok")
    assert run("nexus", "get", "raw", "hop", "--out", "out/f.bin") == 0
    assert (tmp_path / "out" / "f.bin").read_bytes() == b"file-bytes"
    first, second = Fake.log
    # the server redirects 127.0.0.1 -> localhost: a different host, so the token must not follow
    assert first[2]["Authorization"].startswith("Basic ") and "Authorization" not in second[2]


def test_redirect_to_other_host_strips_authorization():
    h = pp._SafeRedirect()
    import urllib.request as ur

    req = ur.Request("https://nexus.example.com/a", headers={"Authorization": "Bearer x"})
    new = h.redirect_request(req, None, 302, "Found", {}, "https://s3.example.org/blob")
    assert "Authorization" not in new.headers and "Authorization" not in new.unredirected_hdrs
    same = h.redirect_request(req, None, 302, "Found", {}, "https://nexus.example.com/b")
    assert same.get_header("Authorization") == "Bearer x"


def test_put_errors(server, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("NEXUS_URL", server)
    assert run("nexus", "put", "r", "nothing-*.zip") == 1
    assert "no files match" in capsys.readouterr().err
    (tmp_path / "fail.bin").write_bytes(b"x")
    assert run("nexus", "put", "r", "fail.bin", "--dest", "fail") == 1  # server answers 403
    assert "HTTP 403" in capsys.readouterr().err
    monkeypatch.delenv("NEXUS_URL")
    assert run("nexus", "put", "r", "fail.bin") == 1
    assert "NEXUS_URL" in capsys.readouterr().err


def test_approle_beats_an_ambient_vault_token(server, monkeypatch):
    monkeypatch.setenv("VAULT_ADDR", server)
    monkeypatch.setenv("VAULT_TOKEN", "s.ambient")
    monkeypatch.setenv("VAULT_ROLE_ID", "rid")
    monkeypatch.setenv("VAULT_SECRET_ID", "sid")
    assert run("vault", "export", "--env", "A=kv1:legacy/nexus#password") == 0
    assert Fake.log[0][1] == "/v1/auth/approle/login"


def test_redirect_downgrade_to_http_strips_credentials():
    import urllib.request as ur

    req = ur.Request("https://nexus.example.com/a", headers={"Authorization": "Bearer x"})
    new = pp._SafeRedirect().redirect_request(req, None, 302, "Found", {}, "http://nexus.example.com/b")
    assert "Authorization" not in new.headers


def test_bad_prop_is_a_readable_error(server, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ARTIFACTORY_URL", server)
    (tmp_path / "a.zip").write_bytes(b"z")
    assert run("artifactory", "put", "r", "a.zip", "--prop", "build") == 1
    assert "expected K=V" in capsys.readouterr().err


def test_nexus_ignores_a_bearer_token_and_download_is_streamed(server, monkeypatch, tmp_path):
    monkeypatch.setenv("NEXUS_URL", server)
    monkeypatch.setenv("NEXUS_TOKEN", "tok")
    assert run("nexus", "get", "raw", "x/f.bin") == 0
    assert "Authorization" not in Fake.log[0][2]
    assert (tmp_path / "f.bin").read_bytes() == b"file-bytes" and not (tmp_path / "f.bin.part").exists()


def test_pp_version_matches_the_package():
    import pipeline_platform

    assert pp.__version__ == pipeline_platform.__version__
