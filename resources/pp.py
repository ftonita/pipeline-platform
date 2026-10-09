#!/usr/bin/env python3
"""pp: tiny CI helper shared by the GitLab templates and the Jenkins library.

Standard library only, so it runs anywhere python3 does. Secrets are read from the
environment, never from arguments, and are never printed.

    pp vault export   read secrets from Vault (any engine) into a dotenv file + secret files
    pp nexus put|get  upload / download files in a Nexus raw or maven repository
    pp artifactory put|get   the same for JFrog Artifactory (adds checksum + properties)

Run `pp <command> --help` for the environment variables each command reads.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

__version__ = "1.2.0"


class PPError(Exception):
    """A problem the user can fix; printed without a traceback."""


# --------------------------------------------------------------------------- http


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects, but never send credentials to a different host or over a downgraded scheme."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        a, b = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(newurl)
        if new is not None and (a.netloc, a.scheme) != (b.netloc, b.scheme):
            for h in ("Authorization", "X-vault-token"):
                new.headers.pop(h, None)
                new.unredirected_hdrs.pop(h, None)
        return new


def _check_url(url: str) -> str:
    if not url:
        raise PPError("URL is empty (set the *_URL / VAULT_ADDR variable)")
    if not url.startswith("https://") and os.environ.get("PP_ALLOW_HTTP") != "1":
        raise PPError(f"refusing non-https URL {url!r} (PP_ALLOW_HTTP=1 allows it for local tests)")
    return url.rstrip("/")


_OPENER = None


def _opener():
    global _OPENER
    if _OPENER is None:
        _OPENER = urllib.request.build_opener(
            _SafeRedirect, urllib.request.HTTPSHandler(context=ssl.create_default_context())
        )
    return _OPENER


def _request(method, url, *, headers=None, body=None, retries=3, sink=None):
    """Return (status, bytes). Retries 5xx and network errors; 4xx fails immediately.

    With `sink` (a path) the response is streamed to that file and the bytes are b"".
    """
    opener = _opener()
    last = ""
    for attempt in range(retries):
        data = open(body, "rb") if isinstance(body, str) else body  # noqa: SIM115 (closed below)
        hdrs = dict(headers or {})
        if isinstance(body, str):
            hdrs["Content-Length"] = str(os.path.getsize(body))
        try:
            with opener.open(
                urllib.request.Request(url, data=data, method=method, headers=hdrs), timeout=120
            ) as r:
                if sink is None:
                    return r.status, r.read()
                part = sink + ".part"
                with open(part, "wb") as f:
                    shutil.copyfileobj(r, f, 1 << 20)
                os.replace(part, sink)
                return r.status, b""
        except urllib.error.HTTPError as e:
            if e.code < 500:
                raise PPError(f"{method} {_safe(url)} -> HTTP {e.code} {e.reason}") from None
            last = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = str(getattr(e, "reason", e))
        finally:
            if isinstance(body, str):
                data.close()
        time.sleep(2**attempt if os.environ.get("PP_NO_SLEEP") != "1" else 0)
    raise PPError(f"{method} {_safe(url)} failed after {retries} attempts ({last})")


def _safe(url: str) -> str:
    return urllib.parse.urlunsplit(urllib.parse.urlsplit(url)._replace(query="", fragment=""))


def _env(name: str, *alts: str, required: bool = True) -> str:
    for n in (name, *alts):
        if os.environ.get(n):
            return os.environ[n]
    if required:
        raise PPError(f"environment variable {' or '.join((name, *alts))} is required")
    return ""


# --------------------------------------------------------------------------- vault

_SPEC = re.compile(r"^(?:(?P<engine>kv2|kv1|raw):)?(?P<path>[^#]+)#(?P<field>.+)$")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_spec(spec: str) -> tuple[str, str, str]:
    """`[engine:]mount/path#field` -> (engine, path, field). Default engine is kv2."""
    m = _SPEC.match(spec.strip())
    if not m:
        raise PPError(f"bad secret spec {spec!r}; expected [kv2|kv1|raw:]mount/path#field")
    engine, path = m["engine"] or "kv2", m["path"].strip("/")
    if engine == "kv2":
        if "/" not in path:
            raise PPError(f"bad kv2 spec {spec!r}: need mount/path")
        mount, rest = path.split("/", 1)
        path = f"{mount}/data/{rest}"
    return engine, path, m["field"]


def parse_pairs(text: str) -> list[tuple[str, str]]:
    """Lines of NAME=SPEC. Blank lines and # comments are ignored."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, spec = line.partition("=")
        if not sep or not _NAME.match(name.strip()):
            raise PPError(f"bad line {line!r}; expected NAME=mount/path#field")
        out.append((name.strip(), spec.strip()))
    return out


class Vault:
    def __init__(self) -> None:
        self.addr = _check_url(_env("VAULT_ADDR", "VAULT_SERVER_URL"))
        self.ns = os.environ.get("VAULT_NAMESPACE", "")
        self.token = ""
        self.owned = False  # we logged in, so we revoke at the end
        self._cache: dict[str, dict] = {}

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.ns:
            headers["X-Vault-Namespace"] = self.ns
        if self.token:
            headers["X-Vault-Token"] = self.token
        body = json.dumps(payload).encode() if payload is not None else None
        _, raw = _request(method, f"{self.addr}/v1/{path}", headers=headers, body=body)
        return json.loads(raw) if raw else {}

    def login(self) -> None:
        method = os.environ.get("VAULT_AUTH_METHOD", "")
        if not method:
            method = (
                "approle"
                if os.environ.get("VAULT_ROLE_ID")
                else "token"
                if os.environ.get("VAULT_TOKEN")
                else "jwt"
            )
        if method == "token":
            self.token = _env("VAULT_TOKEN")
            return
        if method == "approle":
            mount = os.environ.get("VAULT_AUTH_PATH", "approle")
            payload = {"role_id": _env("VAULT_ROLE_ID"), "secret_id": _env("VAULT_SECRET_ID")}
        elif method == "jwt":
            mount = os.environ.get("VAULT_AUTH_PATH", "jwt")
            payload = {"role": _env("VAULT_AUTH_ROLE"), "jwt": _env("VAULT_ID_TOKEN", "CI_JOB_JWT_V2")}
        else:
            raise PPError(f"unknown VAULT_AUTH_METHOD {method!r} (token|approle|jwt)")
        self.token = self._call("POST", f"auth/{mount}/login", payload)["auth"]["client_token"]
        self.owned = True

    def logout(self) -> None:
        if self.owned and self.token:
            try:
                self._call("POST", "auth/token/revoke-self", {})
            except PPError:
                pass  # best effort; the token expires on its own

    def get(self, spec: str) -> str:
        engine, path, field = parse_spec(spec)
        if path not in self._cache:  # read once: dynamic engines issue new credentials per read
            data = self._call("GET", path).get("data") or {}
            self._cache[path] = (
                data["data"] if engine == "kv2" and isinstance(data.get("data"), dict) else data
            )
        value = self._cache[path].get(field)
        if value is None:
            known = ", ".join(sorted(self._cache[path])) or "none"
            raise PPError(f"field {field!r} not found at {path} (fields: {known})")
        return value if isinstance(value, str) else json.dumps(value)


def _write_private(path: str, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def cmd_vault_export(args: argparse.Namespace) -> int:
    envs = parse_pairs(os.environ.get("VAULT_ENV", "")) + [_pair(p) for p in args.env]
    files = parse_pairs(os.environ.get("VAULT_FILES", "")) + [_pair(p) for p in args.file]
    if not envs and not files:
        raise PPError("nothing to do: set VAULT_ENV / VAULT_FILES or pass --env / --file")
    clash = {n for n, _ in envs} & {n for n, _ in files}
    if clash:
        raise PPError(f"same name in env and files: {', '.join(sorted(clash))}")
    vault = Vault()
    vault.login()
    try:
        lines = []
        for name, spec in envs:
            value = vault.get(spec)
            if "\n" in value or "\r" in value:
                raise PPError(f"{name} is multi-line; use VAULT_FILES / --file for it")
            lines.append(f"{name}={value}")
        if files:
            os.makedirs(args.dir, mode=0o700, exist_ok=True)
            os.chmod(args.dir, 0o700)
        for name, spec in files:
            target = os.path.join(args.dir, name)
            _write_private(target, vault.get(spec))
            lines.append(f"{name}={target}")
    finally:
        vault.logout()
    _write_private(args.out, "\n".join(lines) + "\n")
    print(f"vault: wrote {len(lines)} secret(s) to {args.out}: {', '.join(n for n, _ in envs + files)}")
    return 0


def _pair(text: str) -> tuple[str, str]:
    return parse_pairs(text)[0]


# --------------------------------------------------------------------------- nexus / artifactory


def _auth_headers(prefix: str) -> dict[str, str]:
    import base64

    token = (
        os.environ.get(f"{prefix}_TOKEN", "") if prefix == "ARTIFACTORY" else ""
    )  # Nexus: user/password only
    if token:
        return {"Authorization": f"Bearer {token}"}
    user, password = os.environ.get(f"{prefix}_USER", ""), os.environ.get(f"{prefix}_PASSWORD", "")
    if user:
        return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}
    return {}  # anonymous (fine for downloads from open repositories)


def _repo_url(kind: str, repo: str, path: str) -> str:
    base = _check_url(_env(f"{kind.upper()}_URL"))
    prefix = "repository" if kind == "nexus" else "artifactory"
    return f"{base}/{prefix}/{urllib.parse.quote(repo)}/{urllib.parse.quote(path.strip('/'), safe='/')}"


def cmd_put(args: argparse.Namespace) -> int:
    kind = args.cmd
    files = sorted({f for pattern in args.files for f in glob.glob(pattern)})
    files = [f for f in files if os.path.isfile(f)]
    if not files:
        raise PPError(f"no files match: {' '.join(args.files)}")
    headers = {"Content-Type": "application/octet-stream", **_auth_headers(kind.upper())}
    pairs = [p.partition("=") for p in args.prop]
    if any(not k or not sep for k, sep, _ in pairs):
        raise PPError(f"bad --prop in {args.prop}; expected K=V")
    props = "".join(f";{urllib.parse.quote(k)}={urllib.parse.quote(v)}" for k, _, v in pairs)
    for f in files:
        dest = "/".join(x for x in (args.dest.strip("/"), os.path.basename(f)) if x)
        url = _repo_url(kind, args.repo, dest)
        h = dict(headers)
        if kind == "artifactory":
            h["X-Checksum-Sha1"] = _sha1(f)
            url += props
        _request("PUT", url, headers=h, body=f)
        print(f"{kind}: uploaded {f} -> {args.repo}/{dest}")
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    out = args.out or os.path.basename(args.path.rstrip("/"))
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    _request(
        "GET",
        _repo_url(args.cmd, args.repo, args.path),
        headers=_auth_headers(args.cmd.upper()),
        sink=out,
    )
    print(f"{args.cmd}: downloaded {args.repo}/{args.path} -> {out}")
    return 0


def _sha1(path: str) -> str:
    h = hashlib.sha1()  # noqa: S324 (Artifactory's checksum-deploy header requires SHA-1)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- cli

_VAULT_HELP = """\
Reads VAULT_ADDR (or VAULT_SERVER_URL) and authenticates with the first that applies:
  VAULT_ROLE_ID + VAULT_SECRET_ID  -> approle (mount: VAULT_AUTH_PATH, default approle)
  VAULT_TOKEN                      -> token
  VAULT_ID_TOKEN + VAULT_AUTH_ROLE -> jwt     (mount: VAULT_AUTH_PATH, default jwt)
Force one with VAULT_AUTH_METHOD. Optional: VAULT_NAMESPACE.
Secrets: VAULT_ENV / VAULT_FILES (lines NAME=[kv2:|kv1:|raw:]mount/path#field) or --env/--file.
  kv2:mount/path#field   KV v2, the default (adds /data/ for you)
  kv1:mount/path#field   KV v1
  raw:full/api/path#field any other engine (database/creds/ro#username, aws/creds/x#access_key)
"""
_REPO_HELP = "Reads {P}_URL and {A}."


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pp", description=__doc__.split("\n\n")[0])
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("vault", help="Vault secrets").add_subparsers(dest="sub", required=True)
    e = v.add_parser(
        "export",
        help="write secrets to a dotenv file",
        epilog=_VAULT_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    e.add_argument(
        "--env", action="append", default=[], metavar="NAME=SPEC", help="value goes into the dotenv file"
    )
    e.add_argument(
        "--file",
        action="append",
        default=[],
        metavar="NAME=SPEC",
        help="value goes into a 0600 file; NAME holds its path (multi-line secrets, keys)",
    )
    e.add_argument("--out", default="vault.env", help="dotenv file (default vault.env)")
    e.add_argument(
        "--dir", default=".vault-files", help="directory for --file secrets (default .vault-files)"
    )
    e.set_defaults(func=cmd_vault_export)
    for kind in ("nexus", "artifactory"):
        k = sub.add_parser(
            kind,
            help=f"{kind} upload/download",
            epilog=_REPO_HELP.format(
                P=kind.upper(),
                A="ARTIFACTORY_TOKEN, or ARTIFACTORY_USER + ARTIFACTORY_PASSWORD"
                if kind == "artifactory"
                else "NEXUS_USER + NEXUS_PASSWORD (a Nexus user token works as the pair)",
            ),
        )
        ks = k.add_subparsers(dest="sub", required=True)
        put = ks.add_parser("put", help="upload files (globs allowed)")
        put.add_argument("repo")
        put.add_argument("files", nargs="+")
        put.add_argument("--dest", default="", help="directory inside the repository")
        if kind == "artifactory":
            put.add_argument(
                "--prop", action="append", default=[], metavar="K=V", help="Artifactory property"
            )
        else:
            put.set_defaults(prop=[])
        put.set_defaults(func=cmd_put)
        get = ks.add_parser("get", help="download one file")
        get.add_argument("repo")
        get.add_argument("path")
        get.add_argument("--out", help="local file (default: basename of path)")
        get.set_defaults(func=cmd_get)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except PPError as exc:
        print(f"pp: error: {exc}", file=sys.stderr)
        return 1
    except (KeyError, json.JSONDecodeError) as exc:
        print(
            f"pp: error: unexpected response from the server ({type(exc).__name__}: {exc})", file=sys.stderr
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
