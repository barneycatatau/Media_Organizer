# ⚙️ Configuration Reference

All application settings are stored in the `config.cfg` file under the `[PATHS]` section.

---

## 📁 Paths

### source_path

```ini
source_path=D:\Media
```

Source directory to scan for photos and videos.

- Scanning is **recursive** — all subfolders are included automatically.
- Supports local paths (`C:\Photos`) and network paths (`\\server\folder`).
- `media_organizer.py` only reads this tree and copies files from it.
- `duplicate_check.py` operates on this tree and can **move or permanently delete** duplicate copies.

---

### destination_path

```ini
destination_path=\\192.168.1.3\NAS\Photos
```

Destination directory where organized files will be copied.

The application automatically creates the required structure from the year/GPS settings and, when `year_wise=no`, the source-relative folder path.
Startup fails fast if this folder cannot be created or written to.

For Windows UNC paths, the organizer can authenticate before copying when credentials are configured:

```ini
destination_path=\\192.168.1.55\Pictures
network_username=
network_password=
network_domain=
```

Leave the network fields blank to let Windows use the current process credentials. Fill them when the script runs elevated, as a scheduled task, or as another Windows user, because that process may not see the Explorer session credentials.

The credentials are optional and are used only for Windows UNC destinations such as `\\server\share`. `network_domain` is prepended to a simple username; alternatively, specify `DOMAIN\user` directly in `network_username`. On non-Windows systems, the share must already be mounted or otherwise available to the operating system.

At startup, `media_organizer.py` creates and removes a small `.media_organizer_write_test` file in the destination. Processing stops immediately with a clear error when the destination cannot be reached, authenticated, created, or written to. Windows errors also distinguish rejected credentials from a share already connected with a different account.

> **Security:** `network_password` is stored as plain text in `config.cfg`. Keep the real configuration file private, do not commit it, and limit filesystem access to the account that runs the organizer.

> 💡 The script **copies** files — the source is never altered.

---

### move_duplicate_to_path

```ini
move_duplicate_to_path=D:\Duplicates
```

Used by `duplicate_check.py`. The checker hashes every supported file below `source_path` and groups SHA-256 matches across the entire tree, including different subfolders. It then keeps one file from each content group and handles all other copies according to this setting:

- **Valid path**: duplicates are *moved* to this folder. Their paths relative to `source_path` are preserved. If a target name already exists, `_dup1`, `_dup2`, and so on are appended. Use a review directory outside `source_path`.
- **`no`**: duplicates are *permanently deleted* immediately.

```ini
move_duplicate_to_path=no
```

> ⚠️ Setting `no` is irreversible — duplicates cannot be recovered.

Only byte-for-byte identical files match. The retained file is the one with the lowest filesystem `ctime` (creation time on Windows; metadata-change time on Unix-like systems). A tie is resolved by the case-insensitive filename.

> `duplicate_check.py` always scans `source_path`, not `destination_path`. To clean an organized library, set `source_path` to that library before running the command and review the move/delete setting carefully.

---

## 🖼️ Supported File Extensions

### organize_image_extensions

```ini
organize_image_extensions=jpg,jpeg,png,heic
```

Image extensions processed by `media_organizer.py`. Comma-separated, without a leading dot. Matching is case-insensitive (`.JPG` and `.jpg` are treated the same).

---

### organize_video_extensions

```ini
organize_video_extensions=mov,avi,mp4,mkv
```

Video extensions processed by `media_organizer.py`. Same rules as image extensions above.

---

### image_extensions

```ini
image_extensions=jpg,jpeg,png,heic
```

Image extensions processed by `duplicate_check.py`.

---

### video_extensions

```ini
video_extensions=mov,avi,mp4,mkv
```

Video extensions processed by `duplicate_check.py`.

---

## 🗂️ Organization Settings

### GPS

```ini
GPS=yes
```

Controls whether GPS coordinates embedded in files are geocoded to resolve the country where the photo was taken.

- **`yes`**: adds a country folder when a country can be resolved (`2019/Brazil/`, `2019/Argentina/`). If coordinates are missing or geocoding does not return a country, that folder is omitted.
- **`no`**: GPS metadata is ignored when resolving the destination path.

Example structure when enabled:

```text
2019/
└── Brazil/
    └── photo.jpg
```

> 💡 Geocoding uses the OpenStreetMap Nominatim API. Results are cached in a local SQLite database to avoid repeated requests and respect the 1 request/second rate limit.

---

### year_wise

```ini
year_wise=yes
```

Controls whether media is grouped by year.

| `year_wise` | `GPS` | GPS result | Result |
|-------------|-------|------------|--------|
| yes | yes | Country found | `2019/Brazil/photo.jpg` |
| yes | yes | No country | `2019/photo.jpg` |
| yes | no | Not used | `2019/photo.jpg` |
| no | yes | Country found | `Brazil/Phone/DCIM/photo.jpg` |
| no | yes | No country | `Phone/DCIM/photo.jpg` |
| no | no | Not used | `Phone/DCIM/photo.jpg` |

`Phone/DCIM` represents the file's original path relative to `source_path`. This relative hierarchy is preserved only when `year_wise=no`. When `year_wise=yes`, source subfolders are not reproduced. Empty source folders are never copied.

---

### verify_before_copy

```ini
verify_before_copy=yes
```

Controls the comparison with files that existed at the destination before the current run.

- **`yes`**: the resolved destination folder and its descendants are indexed by SHA-256. An identical file found in that scope is skipped. The console and log show the number of files hashed, the number reused from cached subfolders, and elapsed time every few seconds; this can take a while on a NAS or other network share.
- **`no`**: the destination check is skipped (faster on a first run).

Duplicate detection among source files in the current run is always enabled and uses one run-wide SHA-256 index, regardless of this setting.

The destination comparison is not library-wide. For example, a file targeting `2026/Brazil` is not compared with an existing file in `2025/Italy`. The completed in-memory index is reused for the remaining files during that run, including across related parent and child folders. For example, after indexing `2026/Brazil`, an index of `2026` skips the cached `Brazil` subtree and hashes only the remaining files. If the parent was indexed first, a child index is derived from it without rereading files. Newly copied files keep all applicable cached indexes up to date. This cache is not persisted between executions.

---

### GPS worker reliability

GPS requests are handled serially to respect the Nominatim rate limit. A media worker waits at most 60 seconds for its geocoding result. Unexpected geocoding-worker errors release the waiting media worker and are written to the log; the file then follows the existing no-country/failure handling instead of waiting indefinitely.

---

### rename_prefix

```ini
rename_prefix=IMG_
```

Controls whether files are renamed when copied.

- **`no`**: original filenames are preserved. If two files share the same name but have different content, a numeric suffix is appended (`photo_1.jpg`, `photo_2.jpg`).
- **Prefix** (e.g. `IMG_`): all files are renamed with sequential numbering:

```text
IMG_0001.jpg
IMG_0002.jpg
IMG_0003.jpg
```

---

## ↩️ Undo Configuration

### undo

```ini
undo=LOG\media_organizer_20260131_064337.log
```

Used exclusively by `media_organizer_undo.py`.

- **Path to a `.log` file**: the script reads the log generated by a previous `media_organizer.py` run and **removes** all files copied during that session. Empty folders are deleted automatically.
- **`no`**: disables the undo operation (safety lock to prevent accidental execution).

```ini
undo=no
```

> ⚠️ This operation is destructive. Files are permanently deleted from the destination. The source is never affected.
