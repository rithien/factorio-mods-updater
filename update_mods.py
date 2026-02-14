import argparse
import json
import logging
import os
import shutil
import tempfile
import time
import urllib.request
import urllib.error

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
MODS_LIST_CACHE = os.path.join(SCRIPT_DIR, "mods_list.json")
SYSTEM_MODS = {"base", "space-age", "quality", "elevated-rails"}

log = logging.getLogger(__name__)


DEFAULT_CONFIG = {
    "mods_api_url": "https://mods.factorio.com/api/mods?page_size=max&full=True&version={version}&is_space_age=true",
    "mod_packs_path": "./mod-packs.json",
    "mods_dir": "<FILL IN - path to mods folder>",
    "factorio_version_file": "<FILL IN - path to base-info.json>",
    "username": "<FILL IN>",
    "token": "<FILL IN>",
}


def load_config():
    if not os.path.exists(CONFIG_PATH):
        log.warning("config.json not found - creating default: %s", CONFIG_PATH)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent="\t", ensure_ascii=False)
        log.error("Fill in config.json and run again.")
        raise SystemExit(1)

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    missing = [k for k, v in config.items() if isinstance(v, str) and v.startswith("<FILL IN")]
    if missing:
        log.error("Incomplete fields in config.json: %s", ", ".join(missing))
        raise SystemExit(1)

    return config


def validate_paths(config):
    errors = []

    mod_packs_path = config["mod_packs_path"]
    if not os.path.isabs(mod_packs_path):
        mod_packs_path = os.path.join(SCRIPT_DIR, mod_packs_path)
    if not os.path.isfile(mod_packs_path):
        errors.append(f"mod_packs_path: file does not exist: {mod_packs_path}")
    else:
        try:
            with open(mod_packs_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                errors.append(f"mod_packs_path: expected JSON array, got {type(data).__name__}")
        except json.JSONDecodeError as e:
            errors.append(f"mod_packs_path: invalid JSON: {e}")

    mods_dir = config["mods_dir"]
    if not os.path.isdir(mods_dir):
        errors.append(f"mods_dir: directory does not exist: {mods_dir}")

    version_file = config["factorio_version_file"]
    if not os.path.isfile(version_file):
        errors.append(f"factorio_version_file: file does not exist: {version_file}")
    else:
        try:
            with open(version_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            version = data.get("version")
            if not version or not isinstance(version, str):
                errors.append("factorio_version_file: missing 'version' field or invalid type")
            elif len(version.split(".")) < 2:
                errors.append(f"factorio_version_file: invalid version format: {version}")
        except json.JSONDecodeError as e:
            errors.append(f"factorio_version_file: invalid JSON: {e}")

    if errors:
        for err in errors:
            log.error(err)
        raise SystemExit(1)


def get_factorio_version(version_file_path):
    with open(version_file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    full_version = data["version"]
    parts = full_version.split(".")
    return f"{parts[0]}.{parts[1]}"


def fetch_mods_list(api_url, version):
    url = api_url.format(version=version)
    log.info("Fetching mod list from API: %s", url)
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    with open(MODS_LIST_CACHE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    log.info("Saved mod list cache (%d mods)", len(data.get("results", [])))
    return data


def build_mods_index(api_data):
    index = {}
    for mod in api_data.get("results", []):
        name = mod.get("name")
        latest = mod.get("latest_release")
        if name and latest:
            index[name] = latest
    return index


def load_mod_packs(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_mod_packs(path, packs):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(packs, f, indent="\t", ensure_ascii=False)


def archive_mod_packs(path):
    timestamp = int(time.time())
    archive_path = f"{path}.{timestamp}"
    shutil.copy2(path, archive_path)
    log.info("Archived mod-packs.json -> %s", archive_path)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_no_redirect_opener = urllib.request.build_opener(_NoRedirect)


def download_mod(download_url, file_name, tmp_dir, username, token):
    url = f"https://mods.factorio.com{download_url}?username={username}&token={token}"
    dest = os.path.join(tmp_dir, file_name)
    log.info("[download] Starting: %s", file_name)
    log.debug("[download] URL: %s", url.replace(token, "***"))

    # First request - get redirect URL from mods.factorio.com (no auto-redirect)
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "factorio-mods-updater/1.0")
    try:
        resp = _no_redirect_opener.open(req, timeout=300)
        # No redirect - unexpected
        body = resp.read(1024).decode("utf-8", errors="replace")
        resp.close()
        log.error("[download] Expected redirect, got %d for %s", resp.status, file_name)
        log.error("[download] Response body: %s", body[:500])
        raise RuntimeError(f"Expected redirect, got {resp.status}")
    except urllib.error.HTTPError as e:
        log.debug("[download] HTTP %d from mods.factorio.com", e.status)
        if e.status in (301, 302, 303, 307, 308):
            real_url = e.headers.get("Location")
            if not real_url:
                body = e.read(1024).decode("utf-8", errors="replace")
                log.error("[download] Redirect %d without Location header. Headers: %s", e.status, dict(e.headers))
                log.error("[download] Body: %s", body[:500])
                raise RuntimeError(f"Redirect {e.status} without Location")
            log.info("[download] Redirect %d -> %s", e.status, real_url)
        else:
            body = e.read(2048).decode("utf-8", errors="replace")
            log.error("[download] HTTP error %d from mods.factorio.com for %s", e.status, file_name)
            log.error("[download] Headers: %s", dict(e.headers))
            log.error("[download] Body: %s", body[:500])
            raise

    # Second request - download file from CDN (dl-mod.factorio.com), clean request without auth
    cdn_req = urllib.request.Request(real_url)
    cdn_req.add_header("User-Agent", "factorio-mods-updater/1.0")
    try:
        with urllib.request.urlopen(cdn_req, timeout=300) as resp:
            log.debug("[download] CDN responded %d, Content-Length: %s",
                      resp.status, resp.headers.get("Content-Length", "?"))
            with open(dest, "wb") as f:
                shutil.copyfileobj(resp, f)
    except urllib.error.HTTPError as e:
        body = e.read(2048).decode("utf-8", errors="replace")
        log.error("[download] HTTP error %d from CDN for %s", e.status, file_name)
        log.error("[download] CDN URL: %s", real_url)
        log.error("[download] Headers: %s", dict(e.headers))
        log.error("[download] Body: %s", body[:500])
        raise

    size = os.path.getsize(dest)
    log.info("[download] OK: %s (%d bytes)", file_name, size)
    return dest


def find_updates(packs, mods_index, factorio_version):
    updates = []
    for pack in packs:
        pack_fv = pack.get("factorio_version", "")
        pack_fv_major_minor = ".".join(pack_fv.split(".")[:2])
        if pack_fv_major_minor != factorio_version:
            continue

        for mod in pack.get("mods", []):
            mod_name = mod.get("name", "")
            if mod_name in SYSTEM_MODS:
                continue
            if not mod.get("enabled", False):
                continue

            latest = mods_index.get(mod_name)
            if not latest:
                log.warning("Mod '%s' not found in API (pack: %s)", mod_name, pack.get("name"))
                continue

            local_version = mod.get("version", "")
            local_sha1 = mod.get("sha1", "")
            remote_version = latest.get("version", "")
            remote_sha1 = latest.get("sha1", "")

            if local_version != remote_version or local_sha1 != remote_sha1:
                updates.append({
                    "pack_name": pack.get("name"),
                    "mod_name": mod_name,
                    "old_version": local_version,
                    "new_version": remote_version,
                    "new_sha1": remote_sha1,
                    "download_url": latest.get("download_url", ""),
                    "file_name": latest.get("file_name", ""),
                })

    return updates


def apply_updates(packs, successful_mods):
    now_ms = int(time.time() * 1000)
    updated_packs = 0

    for pack in packs:
        pack_changed = False
        for mod in pack.get("mods", []):
            mod_name = mod.get("name", "")
            if mod_name in successful_mods:
                info = successful_mods[mod_name]
                mod["version"] = info["new_version"]
                mod["sha1"] = info["new_sha1"]
                pack_changed = True

        if pack_changed:
            pack["updated_at_ms"] = now_ms
            updated_packs += 1

    return updated_packs


def main():
    config = load_config()
    validate_paths(config)

    mod_packs_path = config["mod_packs_path"]
    if not os.path.isabs(mod_packs_path):
        mod_packs_path = os.path.join(SCRIPT_DIR, mod_packs_path)

    mods_dir = config["mods_dir"]
    username = config["username"]
    token = config["token"]

    factorio_version = get_factorio_version(config["factorio_version_file"])
    log.info("Factorio version: %s", factorio_version)

    api_data = fetch_mods_list(config["mods_api_url"], factorio_version)
    mods_index = build_mods_index(api_data)
    log.info("Indexed %d mods from API", len(mods_index))

    packs = load_mod_packs(mod_packs_path)
    updates = find_updates(packs, mods_index, factorio_version)

    if not updates:
        log.info("No updates - all mods are up to date")
        return

    log.info("Found %d updates:", len(updates))
    for u in updates:
        log.info("  %s: %s -> %s (pack: %s)", u["mod_name"], u["old_version"], u["new_version"], u["pack_name"])

    # Download to temporary folder
    tmp_dir = tempfile.mkdtemp(prefix="factorio-mods-")
    log.info("Temporary folder: %s", tmp_dir)

    # mod_name -> update info (only successfully downloaded)
    successful = {}
    # file_name -> tmp_path (unique files to move)
    downloaded_files = {}

    try:
        for u in updates:
            file_name = u["file_name"]
            mod_name = u["mod_name"]

            if file_name not in downloaded_files:
                try:
                    tmp_path = download_mod(u["download_url"], file_name, tmp_dir, username, token)
                    downloaded_files[file_name] = tmp_path
                except (urllib.error.URLError, OSError, RuntimeError) as e:
                    log.error("Failed to download %s: %s", mod_name, e)
                    continue

            if file_name in downloaded_files:
                successful[mod_name] = u

        if not successful:
            log.error("No mods were downloaded successfully")
            return

        # Move from temp to mods_dir
        for file_name, tmp_path in downloaded_files.items():
            dest = os.path.join(mods_dir, file_name)
            shutil.move(tmp_path, dest)
            log.info("Moved: %s -> %s", file_name, dest)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Archive and update mod-packs.json
    archive_mod_packs(mod_packs_path)
    updated_packs = apply_updates(packs, successful)
    save_mod_packs(mod_packs_path, packs)
    log.info("Updated mod-packs.json (%d mods, %d packs)", len(successful), updated_packs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Factorio mod updater")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging (DEBUG)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    main()
