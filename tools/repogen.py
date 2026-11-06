#!/usr/bin/env python3
"""repogen.py - build and maintain a Feather / AltStore compatible IPA source.

Feather reads ONE json file (usually repo.json) that lists every app and every
download URL. This tool keeps that file in sync with the .ipa files you publish.

Commands
  info  <file.ipa>     Read an IPA and print the metadata we can extract.
  add   <file.ipa>     Upload the IPA to a GitHub Release and add/update it in repo.json.
  sync                 Regenerate the top-level (repo) fields from repo.config.json.
  validate             Check repo.json is well formed. Used by CI.

Options for `add`
  --tag TAG        Release tag to put the IPA under (default: <app-name>-<version>)
  --no-upload      Do not touch GitHub; just update repo.json with the local file
  --dry-run        Print what would be written, change nothing
  --publish        git add + commit + push repo.json and icons when done
  --icon PATH      Use this PNG as the app icon instead of the one inside the IPA
  --no-itunes      Never fall back to the iTunes lookup API for a missing icon

Only the Python standard library is used. No pip install required.
"""

from __future__ import annotations

import argparse
import json
import plistlib
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "repo.config.json"
SOURCE_PATH = ROOT / "repo.json"
ICONS_DIR = ROOT / "icons"
META_DIR = ROOT / "meta"

DATE_FMT = "%Y-%m-%dT%H:%M:%SZ"


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def log(msg: str = "") -> None:
    print(msg, flush=True)


def die(msg: str, code: int = 1):
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(code)


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        die(f"{path.name} is not valid JSON ({exc})")


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")


def run(cmd, check=True) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        die(f"`{' '.join(cmd)}` failed:\n{detail}")
    return (proc.stdout or "").strip()


def gh(*args, check=True) -> str:
    if not shutil.which("gh"):
        die("the GitHub CLI (`gh`) is not installed. https://cli.github.com/")
    return run(["gh", *args], check=check)


def slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9._-]+", "-", value.lower()).strip("-")
    return value or "app"


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime(DATE_FMT)


# --------------------------------------------------------------------------- #
# IPA inspection
# --------------------------------------------------------------------------- #
def _pick_app_dir(names: list[str]) -> str:
    candidates = set()
    for name in names:
        if not name.startswith("Payload/"):
            continue
        parts = name.split("/")
        if len(parts) >= 3 and parts[1].endswith(".app"):
            candidates.add(parts[1])
    with_plist = sorted(
        c for c in candidates if f"Payload/{c}/Info.plist" in names and "Watch" not in c
    )
    if not with_plist:
        die("no Payload/*.app/Info.plist inside this file - is it really an .ipa?")
    return with_plist[0]


def _pick_icon(z: zipfile.ZipFile, app_dir: str, info: dict) -> bytes | None:
    """Find the best loose PNG icon inside the bundle. Assets.car only -> None."""
    prefix = f"Payload/{app_dir}/"
    depth = prefix.count("/")

    root_pngs = []
    for name in z.namelist():
        if not name.startswith(prefix) or not name.lower().endswith(".png"):
            continue
        if name.count("/") != depth:  # only files sitting at the bundle root
            continue
        root_pngs.append(name)

    def score(name):
        base = name.rsplit("/", 1)[-1].lower()
        if base.startswith("appicon"):
            return 2
        if "icon" in base:
            return 1
        return 0

    ranked = sorted(root_pngs, key=lambda n: (score(n), z.getinfo(n).file_size), reverse=True)
    best = next((n for n in ranked if score(n) > 0), None)
    if best is None:
        return None
    return z.read(best)


def _itunes_icon(bundle_id: str) -> str | None:
    url = (
        "https://itunes.apple.com/lookup?bundleId="
        + urllib.parse.quote(bundle_id)
        + "&entity=software"
    )
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None
    results = payload.get("results") or []
    if not results:
        return None
    return results[0].get("artworkUrl512") or results[0].get("artworkUrl100")


def extract_ipa(path: Path, want_icon: bool = True) -> dict:
    if not path.is_file():
        die(f"{path} does not exist")
    if path.suffix.lower() != ".ipa":
        log(f"warning: {path.name} does not end in .ipa - continuing anyway")

    names_all: list[str]
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        app_dir = _pick_app_dir(names)
        try:
            info = plistlib.loads(z.read(f"Payload/{app_dir}/Info.plist"))
        except Exception as exc:
            die(f"could not parse Info.plist in the IPA ({exc})")

        icon = _pick_icon(z, app_dir, info) if want_icon else None
        fairplay = any(n.startswith(f"Payload/{app_dir}/SC_Info/") for n in names)

    bundle_id = info.get("CFBundleIdentifier")
    if not bundle_id:
        die("Info.plist has no CFBundleIdentifier")

    name = (
        info.get("CFBundleDisplayName")
        or info.get("CFBundleName")
        or app_dir[: -len(".app")]
    )
    return {
        "name": str(name).strip(),
        "bundleIdentifier": str(bundle_id).strip(),
        "version": str(info.get("CFBundleShortVersionString") or "1.0"),
        "buildVersion": str(info.get("CFBundleVersion") or ""),
        "minimumOSVersion": str(info.get("MinimumOSVersion") or ""),
        "executable": str(info.get("CFBundleExecutable") or ""),
        "fairplayEncrypted": fairplay,
        "size": path.stat().st_size,
        "iconBytes": icon,
        "file": path.name,
    }


# --------------------------------------------------------------------------- #
# repo.config.json / repo.json plumbing
# --------------------------------------------------------------------------- #
def load_config() -> dict:
    cfg = load_json(CONFIG_PATH)
    if cfg is None:
        die("repo.config.json is missing")
    return cfg


def repo_slug(cfg: dict) -> tuple[str, str, str]:
    gh_cfg = cfg.get("github", {})
    owner, repo = gh_cfg.get("owner", ""), gh_cfg.get("repo", "")
    branch = gh_cfg.get("branch", "main") or "main"

    remote = ""
    proc = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=ROOT, capture_output=True, text=True
    )
    if proc.returncode == 0:
        remote = proc.stdout.strip()

    if remote:
        m = re.search(r"github\.com[:/]+([^/]+)/([^/#]+?)(?:\.git)?$", remote)
        if m:
            owner = owner or m.group(1)
            repo = repo or m.group(2)
    if not owner and shutil.which("gh") and cfg.get("github", {}).get("autoDetect", True):
        try:
            owner = gh("api", "user", "-q", ".login")
        except SystemExit:
            owner = ""
    return owner, repo, branch


def root_fields(cfg: dict) -> dict:
    src = cfg.get("source", {})
    owner, repo, branch = repo_slug(cfg)
    if owner and repo:
        pages = (
            f"https://{owner}.github.io"
            if repo.lower() == f"{owner.lower()}.github.io"
            else f"https://{owner}.github.io/{repo}"
        )
        raw = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}"
    else:
        pages = ""
        raw = ""

    def pick(value, fallback):
        return value if value not in (None, "", "auto") else fallback

    return {
        "name": src.get("name") or "My Apps",
        "identifier": src.get("identifier") or "com.example.apps",
        "subtitle": src.get("subtitle") or "",
        "description": src.get("description") or "",
        "iconURL": pick(src.get("iconURL"), f"{raw}/icons/repo.png" if raw else ""),
        "headerURL": pick(src.get("headerURL"), ""),
        "website": pick(src.get("website"), f"https://github.com/{owner}/{repo}" if owner and repo else ""),
        "tintColor": (src.get("tintColor") or "0A84FF").lstrip("#"),
        "featuredApps": src.get("featuredApps") or [],
    }, pages, raw


def blank_source(cfg: dict) -> dict:
    root, _, _ = root_fields(cfg)
    data = dict(root)
    data["apps"] = []
    data["news"] = []
    return data


def load_source(cfg: dict) -> dict:
    data = load_json(SOURCE_PATH)
    if data is None:
        return blank_source(cfg)
    data.setdefault("apps", [])
    data.setdefault("news", [])
    return data


def apply_root(data: dict, cfg: dict) -> dict:
    """Put the repo-level fields in a stable, Feather-friendly order."""
    root, _, _ = root_fields(cfg)
    out = dict(root)
    out["apps"] = data.get("apps", [])
    out["news"] = data.get("news", [])
    return out


def version_entry(meta: dict, url: str, description: str = "") -> dict:
    entry = {
        "version": meta["version"],
        "date": now_utc(),
        "downloadURL": url,
        "size": meta["size"],
    }
    if meta.get("buildVersion"):
        entry["buildVersion"] = meta["buildVersion"]
    if meta.get("minimumOSVersion"):
        entry["minOSVersion"] = meta["minimumOSVersion"]
    if description:
        entry["localizedDescription"] = description
    return entry


def upsert_app(data: dict, meta: dict, entry: dict, icon_url: str, sidecar: dict) -> dict:
    apps = data.setdefault("apps", [])
    bundle = meta["bundleIdentifier"]
    app = next((a for a in apps if a.get("bundleIdentifier") == bundle), None)

    if app is None:
        app = {
            "name": meta["name"],
            "bundleIdentifier": bundle,
            "developerName": sidecar.get("developerName") or meta["name"],
            "subtitle": sidecar.get("subtitle", ""),
            "localizedDescription": sidecar.get("localizedDescription", ""),
            "iconURL": icon_url or sidecar.get("iconURL", ""),
            "tintColor": (sidecar.get("tintColor") or "").lstrip("#"),
            "category": sidecar.get("category", ""),
            "screenshotURLs": sidecar.get("screenshotURLs", []),
            "appPermissions": sidecar.get(
                "appPermissions", {"entitlements": [], "privacy": {}}
            ),
            "versions": [],
        }
        apps.append(app)
    else:
        for key in ("name", "subtitle", "developerName", "localizedDescription", "tintColor", "category"):
            if sidecar.get(key) not in (None, ""):
                app[key] = sidecar[key]
        if icon_url:
            app["iconURL"] = icon_url
        if sidecar.get("screenshotURLs"):
            app["screenshotURLs"] = sidecar["screenshotURLs"]
        if sidecar.get("appPermissions"):
            app["appPermissions"] = sidecar["appPermissions"]

    versions = app.setdefault("versions", [])
    versions = [
        v for v in versions
        if not (v.get("version") == entry["version"] and v.get("size") == entry["size"])
    ]
    versions.insert(0, entry)
    versions.sort(key=lambda v: (v.get("date") or ""), reverse=True)
    app["versions"] = versions

    newest = versions[0]
    app["version"] = newest["version"]
    app["versionDate"] = newest.get("date", "")
    app["size"] = newest.get("size", 0)
    app["downloadURL"] = newest.get("downloadURL", "")

    apps.sort(key=lambda a: (a.get("name") or "").lower())
    return app


def load_sidecar(bundle_id: str) -> dict:
    for name in (f"{bundle_id}.json", "default.json"):
        path = META_DIR / name
        if path.exists():
            return load_json(path) or {}
    return {}


# --------------------------------------------------------------------------- #
# icon handling
# --------------------------------------------------------------------------- #
def store_icon(meta: dict, raw_url_base: str, explicit: Path | None) -> str:
    ICONS_DIR.mkdir(exist_ok=True)
    target = ICONS_DIR / f"{slugify(meta['bundleIdentifier'])}.png"

    if explicit:
        if not explicit.is_file():
            die(f"icon not found: {explicit}")
        shutil.copyfile(explicit, target)
    elif meta.get("iconBytes"):
        target.write_bytes(meta["iconBytes"])
    elif meta.get("itunesIcon"):
        return meta["itunesIcon"]
    else:
        log("  no icon found inside the IPA and no iTunes match - iconURL left empty")
        return ""

    return f"{raw_url_base}/icons/{target.name}" if raw_url_base else ""


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_info(args) -> None:
    meta = extract_ipa(Path(args.ipa))
    printable = {k: v for k, v in meta.items() if k != "iconBytes"}
    printable["embeddedIcon"] = bool(meta.get("iconBytes"))
    log(json.dumps(printable, indent=2))


def cmd_sync(args) -> None:
    cfg = load_config()
    data = load_source(cfg)
    save_json(SOURCE_PATH, apply_root(data, cfg))
    _, pages, _ = root_fields(cfg)
    log("repo.json updated.")
    if pages:
        log(f"Feather source URL: {pages}/repo.json")


def cmd_add(args) -> None:
    cfg = load_config()
    ipa = Path(args.ipa).expanduser().resolve()
    meta = extract_ipa(ipa)
    sidecar = load_sidecar(meta["bundleIdentifier"])

    log(f"app        {meta['name']}  ({meta['bundleIdentifier']})")
    log(f"version    {meta['version']} (build {meta['buildVersion'] or '?'})")
    log(f"size       {meta['size']:,} bytes")
    if meta["fairplayEncrypted"]:
        log("warning: this IPA contains SC_Info (FairPlay). Re-signed installs usually")
        log("warning: fail to launch with these. Use a decrypted build instead.")

    if not meta.get("iconBytes") and not args.icon and not sidecar.get("iconURL") and not args.no_itunes:
        meta["itunesIcon"] = _itunes_icon(meta["bundleIdentifier"])

    _, pages, raw = root_fields(cfg)
    owner, repo, branch = repo_slug(cfg)
    tag = args.tag or f"{slugify(meta['name'])}-{slugify(meta['version'])}"

    if args.no_upload:
        url = f"https://example.invalid/{urllib.parse.quote(meta['file'])}"
        log(f"\nskipping upload; placeholder URL: {url}")
    elif args.dry_run:
        url = f"https://github.com/{owner}/{repo}/releases/download/{tag}/{urllib.parse.quote(meta['file'])}"
        log(f"\n[dry-run] would upload to release '{tag}' as {url}")
    else:
        if not owner or not repo:
            die("repo.config.json is missing github.owner / github.repo and no git remote was found")
        log(f"\nuploading to release '{tag}' on {owner}/{repo} ...")
        existing = gh("release", "view", tag, check=False)
        if "not found" in existing.lower() or not existing:
            gh("release", "create", tag,
               "--title", f"{meta['name']} {meta['version']}",
               "--notes", f"{meta['name']} {meta['version']}")
        gh("release", "upload", tag, str(ipa), "--clobber")
        url = (
            f"https://github.com/{owner}/{repo}/releases/download/"
            f"{tag}/{urllib.parse.quote(meta['file'])}"
        )
        log(f"uploaded -> {url}")

    if args.dry_run:
        icon_name = f"{slugify(meta['bundleIdentifier'])}.png"
        icon_url = (
            f"{raw}/icons/{icon_name}"
            if (meta.get("iconBytes") or args.icon)
            else (sidecar.get("iconURL") or meta.get("itunesIcon") or "")
        )
    else:
        icon_url = store_icon(meta, raw, Path(args.icon).resolve() if args.icon else None)
    entry = version_entry(meta, url, sidecar.get("localizedDescription", ""))
    data = load_source(cfg)
    app = upsert_app(data, meta, entry, icon_url, sidecar)
    data = apply_root(data, cfg)

    if args.dry_run:
        log("\n[dry-run] repo.json app entry would be:")
        log(json.dumps(app, indent=2))
        return

    if args.no_upload:
        log("\nnote: --no-upload wrote a placeholder downloadURL. Re-run without it,")
        log("note: or edit that URL by hand before you share this source.")

    save_json(SOURCE_PATH, data)
    log(f"\nrepo.json updated ({len(data['apps'])} app(s)).")

    if args.publish:
        run(["git", "add", "repo.json", "icons", "meta"], check=False)
        if run(["git", "status", "--porcelain"], check=False):
            run(["git", "commit", "-m", f"add {meta['name']} {meta['version']}"])
            run(["git", "push", "origin", branch])
            log("pushed.")
        else:
            log("nothing to commit.")

    if pages:
        log(f"\nFeather source URL:  {pages}/repo.json")


def cmd_set_repo(args) -> None:
    cfg = load_config()
    github = cfg.setdefault("github", {})
    if args.owner:
        github["owner"] = args.owner
    if args.repo:
        github["repo"] = args.repo
    if args.branch:
        github["branch"] = args.branch
    save_json(CONFIG_PATH, cfg)

    data = load_source(cfg)
    save_json(SOURCE_PATH, apply_root(data, cfg))
    _, pages, _ = root_fields(cfg)
    log(f"repo set to {github.get('owner')}/{github.get('repo')}")
    if pages:
        log(f"Feather source URL: {pages}/repo.json")


def cmd_validate(args) -> None:
    errors: list[str] = []
    data = load_json(SOURCE_PATH)
    if data is None:
        die("repo.json is missing")

    for key in ("name", "identifier", "apps"):
        if key not in data:
            errors.append(f"root: missing required key '{key}'")
    for key in ("iconURL", "headerURL", "website"):
        val = data.get(key)
        if val and not str(val).startswith("http"):
            errors.append(f"root: {key} should start with http(s)://")

    if not isinstance(data.get("apps"), list):
        errors.append("root: 'apps' must be a list")
        data["apps"] = []

    seen = set()
    for idx, app in enumerate(data.get("apps", [])):
        label = app.get("bundleIdentifier") or f"apps[{idx}]"
        for key in ("name", "bundleIdentifier"):
            if not app.get(key):
                errors.append(f"{label}: missing '{key}'")
        if app.get("bundleIdentifier") in seen:
            errors.append(f"{label}: duplicate bundleIdentifier")
        seen.add(app.get("bundleIdentifier"))

        versions = app.get("versions")
        if not isinstance(versions, list) or not versions:
            errors.append(f"{label}: needs a non-empty 'versions' list")
            versions = []
        for version in versions:
            for key in ("version", "downloadURL", "size"):
                if key not in version:
                    errors.append(f"{label}: a version is missing '{key}'")
            url = str(version.get("downloadURL", ""))
            if url and not url.startswith("https://"):
                errors.append(f"{label}: downloadURL must be https ({url})")
            size = version.get("size")
            if not isinstance(size, int) or size <= 0:
                errors.append(f"{label}: size must be a positive integer")

    # flat fields are what Feather reads first
    for app in data.get("apps", []):
        label = app.get("bundleIdentifier", "?")
        if app.get("versions") and not app.get("downloadURL"):
            errors.append(f"{label}: missing flat 'downloadURL' (Feather reads this one)")

    if errors:
        log(f"repo.json has {len(errors)} problem(s):")
        for err in errors:
            log(f"  - {err}")
        raise SystemExit(1)

    log(f"repo.json OK - {len(data['apps'])} app(s), "
        f"{sum(len(a.get('versions', [])) for a in data['apps'])} version(s).")


# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(description="Build a Feather/AltStore IPA source.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_info = sub.add_parser("info", help="inspect an IPA")
    p_info.add_argument("ipa")
    p_info.set_defaults(func=cmd_info)

    p_add = sub.add_parser("add", help="upload an IPA and add it to repo.json")
    p_add.add_argument("ipa")
    p_add.add_argument("--tag")
    p_add.add_argument("--icon")
    p_add.add_argument("--no-upload", action="store_true")
    p_add.add_argument("--dry-run", action="store_true")
    p_add.add_argument("--publish", action="store_true")
    p_add.add_argument("--no-itunes", action="store_true")
    p_add.set_defaults(func=cmd_add)

    p_sync = sub.add_parser("sync", help="refresh repo-level fields from repo.config.json")
    p_sync.set_defaults(func=cmd_sync)

    p_repo = sub.add_parser("set-repo", help="set github owner/repo in repo.config.json")
    p_repo.add_argument("--owner")
    p_repo.add_argument("--repo")
    p_repo.add_argument("--branch")
    p_repo.set_defaults(func=cmd_set_repo)

    p_val = sub.add_parser("validate", help="lint repo.json")
    p_val.set_defaults(func=cmd_validate)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
