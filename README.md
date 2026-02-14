# Factorio Mods Updater

Automatic Factorio mod updater designed for [Clusterio](https://github.com/clusterio/clusterio). The script queries the Factorio Mod Portal API, compares versions with the local mod packs list, and downloads newer versions.

## Requirements

- Python 3.8+
- [factorio.com](https://factorio.com) account (username + token for downloading mods)

## Project structure

```
update_mods.py      # Main update script
config.json         # Configuration (created automatically on first run)
```

## Configuration

On first run the script will create a `config.json` file with default values. Fill it in:

```json
{
    "mods_api_url": "https://mods.factorio.com/api/mods?page_size=max&full=True&version={version}&is_space_age=true",
    "mod_packs_path": "./mod-packs.json",
    "mods_dir": "/path/to/mods/folder",
    "factorio_version_file": "/path/to/base-info.json",
    "username": "your_username",
    "token": "your_token"
}
```

| Field | Description |
|-------|-------------|
| `mods_api_url` | Factorio Mod Portal API URL (set by default) |
| `mod_packs_path` | Path to the mod packs file |
| `mods_dir` | Path to the folder where mod `.zip` files are downloaded |
| `factorio_version_file` | Path to the `base-info.json` file containing the Factorio version |
| `username` | factorio.com username |
| `token` | factorio.com authorization token |

You can find your token at: https://factorio.com/profile

## Usage

```bash
# Standard run
python update_mods.py

# With verbose logging (DEBUG)
python update_mods.py -v
```

## How it works

1. Reads the Factorio version from `base-info.json`
2. Fetches the available mods list from the Factorio Mod Portal API
3. Compares versions and SHA1 checksums of mods in `mod-packs.json`
4. If updates are available - downloads new versions to a temporary folder
5. Moves downloaded files to `mods_dir`
6. Archives the old `mod-packs.json` (with a timestamp) and saves the updated version

System mods (`base`, `space-age`, `quality`, `elevated-rails`) are skipped.

## Automation (cron)

The script is designed to run every 12 hours via cron:

```cron
0 */12 * * * /usr/bin/python3 /path/to/update_mods.py >> /var/log/factorio-mods-updater.log 2>&1
```
