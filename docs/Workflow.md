# 🗂️ Recommended Workflow

This document describes the recommended way to use Media Organizer from initial configuration to a clean, organized library.

---

## ⚙️ Step 1 – Configure the Application

Edit `config.cfg` and set at minimum:

```ini
source_path=D:\Media
destination_path=D:\OrganizedMedia
```

Optional settings to enable:

```ini
GPS=yes
year_wise=yes
verify_before_copy=yes
rename_prefix=no
```

> 💡 See [Configuration Reference](Configuration.md) for a full description of every option.

---

## 📂 Step 2 – Organize Media

Run:

```bash
python media_organizer.py
```

Before scanning media, the application connects to a configured Windows UNC share and performs a write test in the destination. If this startup check fails, correct the path, permissions, or network credentials before retrying.

For each file found in the source, the application will:

1. 🔍 Scan all folders recursively.
2. 🗓️ Read available EXIF/video and filesystem dates, then choose the earliest valid year.
3. 🌍 Optionally resolve GPS coordinates to a country name via OpenStreetMap.
4. 🔒 Calculate a SHA-256 hash and skip content already copied during the current run, regardless of its source subfolder.
5. 📋 Copy files into the organized folder structure at the destination.

When `verify_before_copy=yes`, the first file for each resolved destination folder triggers a one-time recursive index of the files already there. This comparison is scoped to that folder and its descendants, not the entire destination library. For large folders or network shares this may be the longest stage. It is active, not stalled, while the console displays messages such as:

```text
Indexing destination for duplicates: \\server\share\Photos\2026\Brazil
Indexing destination: 350 files hashed (1m 12s)
Destination index ready: 812 files hashed (2m 45s)
```

The index cache understands the destination hierarchy and lasts for the entire execution. If `2026/Brazil` has already been indexed and the organizer later needs to index `2026`, it reuses the hashes from `Brazil`, skips that subtree, and hashes only files that are not already cached. The reverse is also supported: an index for a child folder can be derived from an already indexed parent without reading the files again. Files copied during the run are added to every applicable cached index.

Reuse is visible in the console and log:

```text
Destination index ready: 51 files hashed, 848 files reused from cached subfolders (1m 03s)
Destination index derived from cached parent: 848 files reused
```

This avoids repeatedly reading large nested folders, which is especially important on a NAS or other network share. The cache is in memory and is not retained between separate executions. The same progress messages are recorded in the run log. If no destination comparison is needed on an initial import, `verify_before_copy=no` skips this indexing step.

Review the generated report:

```text
LOG/media_organizer_*.html
```

---

## ✅ Step 3 – Verify Results

Inspect the HTML report and the destination folder:

- Folder structure and year/country grouping
- Country assignments for GPS-tagged files
- Ignored files (unsupported extensions)
- Processing errors or warnings

Confirm the destination library looks correct before taking further steps.

---

## 🔁 Step 4 – Remove Duplicates (Optional)

The duplicate checker modifies `source_path`, not `destination_path`. Set `source_path` to the exact directory tree you want to clean. To check the organized library, for example:

```ini
source_path=D:\OrganizedMedia
move_duplicate_to_path=D:\DuplicateReview
```

Keep the review directory outside `source_path`, then run:

```bash
python duplicate_check.py
```

Every supported file below `source_path` is hashed before grouping. SHA-256 matches are compared globally, so identical files in different subfolders belong to the same duplicate group. Only byte-for-byte identical content matches.

The checker keeps the file with the lowest filesystem `ctime`—creation time on Windows and metadata-change time on Unix-like systems. A tie is resolved by the case-insensitive filename. When a review directory is configured, remaining copies are moved while preserving their relative source path.

> ⚠️ `move_duplicate_to_path=no` permanently deletes every duplicate copy selected across the entire tree. Use a review directory first unless irreversible deletion is intentional.

After reviewing the result, open:

```text
LOG/duplicate_check_*.html
```

---

## ➕ Step 5 – Reprocess New Media

New photos and videos can be added to the source directory at any time. Simply re-run:

```bash
python media_organizer.py
```

Within each run, matching source hashes are skipped globally. When `verify_before_copy=yes`, an identical file already present in the resolved destination folder or one of its descendants is also skipped. Unrelated year or country folders are not part of that comparison.

---

## ↩️ Step 6 – Undo if Necessary

To revert an organization session, point the `undo` setting to the log file from that session:

```ini
undo=LOG\media_organizer_20260131_064337.log
```

Then run:

```bash
python media_organizer_undo.py
```

The application will:

- 🗑️ Remove all files copied during that session.
- 🗂️ Delete any folders that become empty.
- 📄 Generate an undo report.

> ⚠️ The original source files are never affected.

---

## 🗺️ Typical Workflow

```text
Configure config.cfg
        │
        ▼
Run media_organizer.py
        │
        ▼
Review HTML report
        │
        ▼
    (Optional)
Point source_path to the tree to clean,
then run duplicate_check.py
        │
        ▼
  Need to revert?
        │
        ▼
Run media_organizer_undo.py
```

This workflow provides a safe and repeatable way to build and maintain an organized media library.
