#!/usr/bin/env python3
"""Deliberate Publisher release and Catalogue commands; never used by Package acquisition."""
from __future__ import annotations

import argparse
import getpass
import hashlib
import http.cookiejar
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

DEFAULT_CATALOGUE = "https://catalogue.prancing-dreadnaught.com"


class PublicationError(RuntimeError):
    def __init__(self, stage: str, message: str, progress: dict | None = None):
        super().__init__(message)
        self.stage = stage
        self.progress = progress or {}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def public_url(value: str, *, local: bool = False) -> str:
    url = urllib.parse.urlsplit(value)
    if (url.scheme != "https" and not (local and url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1"))) or not url.hostname or url.username or url.password or url.fragment:
        raise PublicationError("input", "Use a public HTTPS URL without credentials or a fragment.")
    return value


def credential_file(address: str) -> Path:
    root = Path(os.environ.get("ROOKFRAME_AUTHOR_CREDENTIALS", Path.home() / ".config/rookframe-authoring"))
    return root / (hashlib.sha256(address.encode()).hexdigest() + ".json")


def save_credentials(address: str, token: str, token_id: str) -> None:
    path = credential_file(address)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    file = tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False)
    temporary = Path(file.name)
    try:
        with file:
            os.chmod(temporary, 0o600)
            json.dump({"catalogue": address, "token": token, "tokenId": token_id}, file)
            file.flush()
            os.fsync(file.fileno())
        # Windows requires the writer to close before replacement or cleanup.
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class Catalogue:
    def __init__(self, address: str, *, local: bool = False):
        self.address = public_url(address.rstrip("/"), local=local)
        parsed = urllib.parse.urlsplit(self.address)
        if parsed.path or parsed.query:
            raise PublicationError("input", "The Catalogue address must be an origin without a path or query.")
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPCookieProcessor(self.cookies))

    def request(self, method: str, path: str, body: dict | None = None, *, authenticated: bool = True, session: bool = False) -> dict:
        headers = {"Accept": "application/json", "Content-Type": "application/json", "Origin": self.address}
        if authenticated and not session:
            token = os.environ.get("ROOKFRAME_CATALOGUE_TOKEN")
            if not token:
                try:
                    saved = json.loads(credential_file(self.address).read_text())
                    if saved["catalogue"] == self.address:
                        token = saved["token"]
                except (OSError, KeyError, ValueError):
                    pass
            if not token:
                raise PublicationError("catalogue-authentication", "Sign in with 'catalogue login' or set ROOKFRAME_CATALOGUE_TOKEN in the authoring environment.")
            headers["Authorization"] = "Bearer " + token
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.address + "/api/v1" + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=60) as response:
                payload = response.read(1024 * 1024 + 1)
                if len(payload) > 1024 * 1024:
                    raise PublicationError("catalogue-response", "Catalogue response exceeds 1 MiB.")
                return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as error:
            try:
                message = json.loads(error.read(32768))["error"]["message"]
            except (ValueError, KeyError):
                message = f"Catalogue returned HTTP {error.code}."
            raise PublicationError("catalogue-request", message, {"method": method, "path": path, "httpStatus": error.code}) from error
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            raise PublicationError("catalogue-transport", "The Catalogue request did not complete. Read back the existing submission before repeating a write.", {"method": method, "path": path}) from error


def catalogue_command(args) -> dict:
    client = Catalogue(args.catalogue_url, local=args.local)
    action = args.action
    if action == "register":
        password = getpass.getpass("New Catalogue password: ")
        if password != getpass.getpass("Repeat password: "):
            raise PublicationError("registration", "Passwords differ.")
        return client.request("POST", "/auth/register", {"email": args.email, "name": args.name, "password": password}, authenticated=False)
    if action == "verify":
        return client.request("POST", "/auth/verify", {"token": args.token or getpass.getpass("Email confirmation token: ")}, authenticated=False)
    if action in ("resend", "forgot-password"):
        return client.request("POST", "/auth/" + action, {"email": args.email}, authenticated=False)
    if action == "login":
        password = getpass.getpass("Catalogue password: ")
        client.request("POST", "/auth/login", {"email": args.email, "password": password}, authenticated=False)
        result = client.request("POST", "/tokens", {"name": args.name, "password": password}, session=True)
        save_credentials(client.address, result["token"], result["id"])
        client.request("POST", "/auth/logout", {}, session=True)
        return {"status": "authenticated", "catalogue": client.address, "credentialFile": str(credential_file(client.address)), "expiresInDays": result["expiresInDays"]}
    if action == "propose":
        result = client.request("POST", "/proposals", {"manifestUrl": public_url(args.manifest)})
        proposal = {"catalogue": client.address, **result}
        if args.output:
            # Preserve a previous proposal unless the caller chooses a new path.
            with args.output.open("x") as output:
                json.dump(proposal, output, indent=2)
                output.write("\n")
        return proposal
    if action == "submit":
        proposal = json.loads(args.proposal.read_text())
        if proposal.get("catalogue") != client.address:
            raise PublicationError("catalogue-proposal", "The saved proposal belongs to a different Catalogue.")
        if not args.confirm:
            return {"status": "confirmation_required", "proposal": proposal, "next": "Review the saved proposal, then repeat with --confirm to submit for review."}
        result = client.request("POST", "/submissions", {"proposalId": proposal["id"], "authorNote": args.note})
        # The proposal ID is the retry identity even if the first response was lost.
        return {"status": "submitted", "submission": result, "readback": client.request("GET", "/submissions/" + result["id"])}
    if action == "status":
        return client.request("GET", "/submissions/" + args.id if args.id else "/submissions")
    if action == "tokens":
        return client.request("GET", "/tokens")
    if action == "packages":
        return client.request("GET", "/me/packages")
    if action == "withdraw-request":
        return client.request("POST", "/submissions/" + args.id + "/withdraw", {})
    if action in ("withdraw-release", "relist-release"):
        if not args.confirm:
            return {"status": "confirmation_required", "packageId": args.id, "version": args.version, "action": action}
        return client.request("POST", f"/me/packages/{args.id}/releases/{urllib.parse.quote(args.version, safe='')}", {"listed": action == "relist-release"})
    if action == "logout":
        # Revoke the token used by this authoring installation before removing its file.
        saved = credential_file(client.address)
        token_id = args.token_id or (json.loads(saved.read_text()).get("tokenId") if saved.exists() else None)
        if not token_id:
            raise PublicationError("catalogue-logout", "Choose --token-id from catalogue tokens to revoke the authoring token.")
        client.request("DELETE", "/tokens/" + token_id)
        saved.unlink(missing_ok=True)
        return {"status": "signed_out"}
    raise PublicationError("input", "Unknown Catalogue action.")


def gh_json(endpoint: str, *, method: str = "GET", body: dict | None = None, optional: bool = False) -> dict | None:
    command = ["gh", "api", endpoint, "--method", method, "-H", "Accept: application/vnd.github+json", "-H", "X-GitHub-Api-Version: 2022-11-28"]
    with tempfile.TemporaryDirectory(prefix="rookframe-github-") as folder:
        if body is not None:
            path = Path(folder) / "body.json"
            path.write_text(json.dumps(body))
            command += ["--input", str(path)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode:
        if optional and "HTTP 404" in result.stderr:
            return None
        raise PublicationError("github-api", result.stderr.strip() or "GitHub request failed.", {"endpoint": endpoint, "method": method})
    return json.loads(result.stdout) if result.stdout.strip() else {}


def checked_build(project: Path, archive: Path) -> tuple[dict, bytes, dict]:
    from rookframe_authoring_checks import configured_profiles, configure_application, verify
    from rookframe_authoring import check_revision
    from rookframe_package_build import source_identity
    if archive.stat().st_size > 512 * 1024 * 1024:
        raise PublicationError("build-inspection", "Archive exceeds 512 MiB.")
    configure_application(project)
    checked = verify("artifact", archive)
    with zipfile.ZipFile(archive) as contents:
        manifest_bytes = contents.read("rookframe.json")
        manifest = json.loads(manifest_bytes)
        build = json.loads(contents.read("artifacts/build.json"))
    check_revision(manifest, checked)
    profiles = configured_profiles(project)
    if set(build["profiles"]) != set(profiles):
        raise PublicationError("build-inspection", "The archive does not contain every configured Application Profile.")
    if (manifest_bytes != (project / "rookframe.json").read_bytes()
            or build["sourceSha256"] != source_identity(project)):
        raise PublicationError("build-inspection", "The selected archive does not match the current authoring inputs. Restore its inputs or build a new release explicitly.")
    return manifest, manifest_bytes, build


def release_assets(manifest: dict, repo: str, tag: str) -> tuple[str, str]:
    prefix = f"https://github.com/{repo}/releases/download/{tag}/"
    names = []
    for field in ("manifest", "download"):
        value = public_url(manifest[field])
        if not value.startswith(prefix):
            raise PublicationError("github-destination", f"The Manifest's {field} URL does not target the selected repository and versioned release.")
        name = value[len(prefix):]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}", name):
            raise PublicationError("github-destination", "Release asset names must be simple filenames.")
        names.append(name)
    if names[0] == names[1]:
        raise PublicationError("github-destination", "Manifest and archive must have different filenames.")
    return names[0], names[1]


def tag_commit(repo: str, tag: str) -> str | None:
    ref = gh_json(f"repos/{repo}/git/ref/tags/{urllib.parse.quote(tag, safe='')}", optional=True)
    if ref is None:
        return None
    obj = ref["object"]
    for _ in range(8):
        if obj["type"] == "commit":
            return obj["sha"]
        if obj["type"] != "tag":
            break
        obj = gh_json(f"repos/{repo}/git/tags/{obj['sha']}")["object"]
    raise PublicationError("github-tag", "The release tag does not resolve to a commit.")


def stream_digest(stream) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(65536), b""):
        digest.update(block)
    return digest.hexdigest()


def assert_asset(repo: str, asset: dict, path: Path) -> None:
    if asset.get("state") != "uploaded" or asset.get("size") != path.stat().st_size:
        raise PublicationError("github-asset", f"Existing asset {asset['name']} has different or incomplete contents. No asset was overwritten.")
    # Read remote bytes for retry decisions; a name alone cannot establish sameness.
    with tempfile.TemporaryFile() as downloaded:
        result = subprocess.run(["gh", "api", f"repos/{repo}/releases/assets/{asset['id']}", "-H", "Accept: application/octet-stream"], stdout=downloaded, stderr=subprocess.PIPE, timeout=180)
        if result.returncode:
            raise PublicationError("github-asset-readback", "Could not read the existing release asset. No asset was changed.")
        downloaded.seek(0)
        actual = stream_digest(downloaded)
    with path.open("rb") as source:
        expected = stream_digest(source)
    if actual != expected:
        raise PublicationError("github-asset", f"Existing asset {asset['name']} differs from this exact build. Choose a new version; no asset was overwritten.")


def publish_github(args) -> dict:
    # All checks and uploads consume one private snapshot of the selected build.
    if args.archive.stat().st_size > 512 * 1024 * 1024:
        raise PublicationError("build-inspection", "Archive exceeds 512 MiB.")
    with tempfile.TemporaryDirectory(prefix="rookframe-build-publication-") as directory:
        source = args.archive
        args.archive = Path(directory) / "checked.rookpackage"
        shutil.copyfile(source, args.archive)
        return publish_checked_github(args)


def publish_checked_github(args) -> dict:
    repo, tag, commit = args.repo, args.tag, args.commit
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise PublicationError("github-destination", "Provide an explicit owner/repository and a full source commit SHA.")
    project, archive = args.project.resolve(), args.archive.resolve()
    manifest, manifest_bytes, build = checked_build(project, archive)
    if tag != "v" + manifest["version"]:
        raise PublicationError("github-destination", "The release tag must be v followed by the exact Manifest version.")
    names = release_assets(manifest, repo, tag)
    from rookframe_media import assets as media_assets, validate_bytes
    media = media_assets(manifest)
    media_names = []
    prefix = f"https://github.com/{repo}/releases/download/{tag}/"
    for item in media:
        url = public_url(item.get("url") or urllib.parse.urljoin(manifest["download"], Path(item["path"]).name))
        name = url.removeprefix(prefix)
        if not url.startswith(prefix) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}", name):
            raise PublicationError("github-destination", "Every media URL must target a file in this versioned release.")
        media_names.append(name)
    if len(set([*names, *media_names])) != len(names) + len(media_names):
        raise PublicationError("github-destination", "Every Manifest, archive and media asset needs a distinct filename.")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
    if head != commit or subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=project, text=True).strip():
        raise PublicationError("github-source", "Publish from a clean authoring checkout at the exact selected source commit.")
    destination = gh_json(f"repos/{repo}")
    if destination["private"]:
        raise PublicationError("github-destination", "Package release hosting must be public. Change repository visibility deliberately before publishing.")
    gh_json(f"repos/{repo}/commits/{commit}")
    resolved = tag_commit(repo, tag)
    if resolved is not None and resolved != commit:
        raise PublicationError("github-tag", "The existing version tag points to another commit. It was not moved.")
    progress = {"repository": repo, "tag": tag, "commit": commit, "buildId": build["buildId"], "profiles": list(build["profiles"]), "manifestUrl": manifest["manifest"], "archiveUrl": manifest["download"], "assets": []}
    if not args.confirm:
        return {"status": "confirmation_required", **progress, "next": "Inspect the destination and exact build, then repeat with --confirm. Build alone never publishes."}
    try:
        release = gh_json(f"repos/{repo}/releases/tags/{urllib.parse.quote(tag, safe='')}", optional=True)
        if release is None:
            release = gh_json(f"repos/{repo}/releases", method="POST", body={"tag_name": tag, "target_commitish": commit, "name": f"{manifest['name']} {manifest['version']}", "body": args.notes.read_text() if args.notes else manifest.get("summary", ""), "draft": True, "prerelease": "-" in manifest["version"].split("+", 1)[0]})
        elif release.get("draft") and resolved is None and release.get("target_commitish") != commit:
            raise PublicationError("github-release", "An existing draft has another target commit. It was not changed.")
        progress.update({"releaseId": release["id"], "releaseUrl": release["html_url"], "draft": release["draft"]})
        with tempfile.TemporaryDirectory(prefix="rookframe-release-") as folder:
            manifest_path = Path(folder) / names[0]
            manifest_path.write_bytes(manifest_bytes)
            uploads = [(names[0], manifest_path), (names[1], archive)]
            with zipfile.ZipFile(archive) as contents:
                for item, name in zip(media, media_names):
                    data = contents.read(item["path"])
                    validate_bytes(item["path"], data)
                    path = Path(folder) / name
                    path.write_bytes(data)
                    uploads.append((name, path))
            for name, path in uploads:
                current = gh_json(f"repos/{repo}/releases/{release['id']}")
                assets = [asset for asset in current["assets"] if asset["name"] == name]
                if len(assets) > 1:
                    raise PublicationError("github-assets", "Duplicate remote asset names require manual reconciliation.")
                if assets:
                    assert_asset(repo, assets[0], path)
                else:
                    endpoint = f"https://uploads.github.com/repos/{repo}/releases/{release['id']}/assets?name={urllib.parse.quote(name, safe='')}"
                    result = subprocess.run(["gh", "api", endpoint, "--method", "POST", "-H", "Content-Type: " + (mimetypes.guess_type(name)[0] or "application/octet-stream"), "--input", str(path)], capture_output=True, text=True, timeout=300)
                    if result.returncode:
                        raise PublicationError("github-upload", f"Upload of {name} did not complete. Retry will inspect remote assets first.")
                    assert_asset(repo, json.loads(result.stdout), path)
                progress["assets"].append(name)
        if release["draft"]:
            release = gh_json(f"repos/{repo}/releases/{release['id']}", method="PATCH", body={"draft": False})
        progress.update(draft=release["draft"], releaseUrl=release["html_url"])
        if tag_commit(repo, tag) != commit:
            raise PublicationError("github-readback", "The published tag does not match the selected source commit.")
        # Public, unauthenticated readback proves the actual hosted handoff source.
        with urllib.request.build_opener(urllib.request.ProxyHandler({}), PublicRedirect()).open(manifest["manifest"], timeout=60) as response:
            hosted = response.read(1024 * 1024 + 1)
        if hosted != manifest_bytes:
            raise PublicationError("github-public-readback", "The hosted public Manifest differs from the inspected build.")
        return {"status": "published", **progress, "next": "Run catalogue propose --manifest with this hosted Manifest, review the returned proposal, then explicitly submit it."}
    except PublicationError as error:
        error.progress = {**progress, **error.progress}
        raise
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise PublicationError("github-transport", "Publication did not finish. The recorded external progress may already exist; retry inspects it and never deletes releases.", progress) from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    catalogue = commands.add_parser("catalogue")
    catalogue.add_argument("--catalogue-url", default=DEFAULT_CATALOGUE)
    catalogue.add_argument("--local", action="store_true", help=argparse.SUPPRESS)
    actions = catalogue.add_subparsers(dest="action", required=True)
    for action in ("register", "login", "resend", "forgot-password"):
        sub = actions.add_parser(action)
        sub.add_argument("--email", required=True)
        if action in ("register", "login"):
            sub.add_argument("--name", required=action == "register", default="Authoring CLI")
    verify = actions.add_parser("verify")
    verify.add_argument("--token")
    propose = actions.add_parser("propose")
    propose.add_argument("--manifest", required=True)
    propose.add_argument("--output", type=Path)
    submit = actions.add_parser("submit")
    submit.add_argument("--proposal", type=Path, required=True)
    submit.add_argument("--note", default="")
    submit.add_argument("--confirm", action="store_true")
    actions.add_parser("status").add_argument("--id")
    actions.add_parser("packages")
    actions.add_parser("tokens")
    actions.add_parser("withdraw-request").add_argument("--id", required=True)
    for action in ("withdraw-release", "relist-release"):
        sub = actions.add_parser(action)
        sub.add_argument("--id", required=True)
        sub.add_argument("--version", required=True)
        sub.add_argument("--confirm", action="store_true")
    actions.add_parser("logout").add_argument("--token-id")
    github = commands.add_parser("publish-github")
    github.add_argument("--project", type=Path, default=Path.cwd())
    github.add_argument("--archive", type=Path, required=True)
    github.add_argument("--repo", required=True)
    github.add_argument("--tag", required=True)
    github.add_argument("--commit", required=True)
    github.add_argument("--notes", type=Path)
    github.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = catalogue_command(args) if args.command == "catalogue" else publish_github(args)
        print(json.dumps(result, indent=2))
        return 0
    except PublicationError as error:
        print(json.dumps({"status": "failed", "stage": error.stage, "message": str(error), "externalProgress": error.progress}, indent=2), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        print(json.dumps({"status": "failed", "stage": "input-or-build-check", "message": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
