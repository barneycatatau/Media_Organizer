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

For each file found in the source, the application will:

1. 🔍 Scan all folders recursively.
2. 🗓️ Extract the creation date from EXIF metadata, falling back to file modification date.
3. 🌍 Optionally resolve GPS coordinates to a country name via OpenStreetMap.
4. 🔒 Calculate a SHA-256 hash for duplicate detection.
5. 📋 Copy files into the organized folder structure at the destination.

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

Run:

```bash
python duplicate_check.py
```

Recommended configuration:

```ini
move_duplicate_to_path=D:\Duplicates
```

Moving duplicates to a review folder instead of deleting them outright allows manual inspection before permanent removal.

> Files are kept by oldest creation date. In case of a tie, alphabetical order determines which is preserved.

Review:

```text
LOG/duplicate_check_*.html
```

---

## ➕ Step 5 – Reprocess New Media

New photos and videos can be added to the source directory at any time. Simply re-run:

```bash
python media_organizer.py
```

When `verify_before_copy=yes`, files already present in the destination (matched by SHA-256 hash) are automatically skipped, making reprocessing safe and efficient.

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
Run duplicate_check.py
        │
        ▼
  Need to revert?
        │
        ▼
Run media_organizer_undo.py
```

This workflow provides a safe and repeatable way to build and maintain an organized media library.
