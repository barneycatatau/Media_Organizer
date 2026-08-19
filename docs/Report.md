# 📊 Report Documentation

Every execution generates log and report files inside the `LOG/` directory:

```text
LOG/
├── *.log
└── *.html
```

The HTML report provides a complete visual summary of the operation.

---

## 📸 Media Organizer Report

### 📈 Statistics Dashboard

| Icon | Metric | Description |
|------|----------|-------------|
| 📁 | Folders Scanned | Directories processed during the scan |
| 📄 | Files Processed | Total media files found |
| 📸 | Images | Image file count |
| 🎥 | Videos | Video file count |
| ⛔ | Duplicates Ignored | Files with identical content to another file in the same run |
| 🔎 | Existing Files Found | Identical files already present in the destination |
| 🔁 | Renamed Files | Filename conflicts resolved by appending a numeric suffix |
| ⚡ | Files Per Minute | Average processing speed |
| 🧵 | Worker Threads | Parallel worker threads used |
| 🗄️ | GPS Cache Reads | Country lookups served from local cache (no API call) |
| 💾 | GPS Cache Writes | New geolocation entries stored in the local cache |
| 🌍 | With GPS | Files with GPS coordinates and a resolved country |
| ❓ | Without GPS | Files without GPS coordinates (when GPS is enabled) |
| 📍 | GPS Failure | Files with coordinates for which no country was resolved |
| 📂 | Folders Created | New folders generated at the destination |
| ✅ | Files Copied | Successfully copied files |
| 🚫 | Ignored Files | Files skipped due to unsupported extensions |
| ⏱️ | Execution Time | Total runtime |

---

### 🌍 Country Statistics

Available only when:

```ini
GPS=yes
```

Displays the number of files assigned to each country as a card grid.

Files without a resolved country are labeled in the report as:

```text
Sem GPS
```

This is a reporting label, not a destination folder name. With `year_wise=yes`, those files are copied directly below their year folder.

---

### 🗂️ Folder Structure Summary

This section summarizes copied files by their extracted year and country metadata. For each group, it displays:

- Reported destination path
- Image count
- Video count
- Total files
- Sample list of the first 50 copied files

> 💡 Files beyond the first 50 are omitted for report performance.

The report is not a fresh filesystem scan. For the exact destination of every successful copy—especially when `year_wise=no` preserves source-relative folders—use the `Copied: source -> destination` entries in the text log.

---

### 🚫 Ignored Files

Lists files that were found but not processed because their extension was not listed in the configuration.

Files are grouped by extension. Useful for identifying new formats that should be added to `organize_image_extensions` or `organize_video_extensions`.

---

### ⚠️ Errors

Displays any errors encountered during processing:

- EXIF extraction failures
- GPS lookup errors
- Permission denied
- File access errors

If this section appears, review each item to ensure no important file was skipped.

---

## 🔁 Duplicate Check Report

### 📈 Statistics

| Icon | Metric | Description |
|------|----------|-------------|
| 📁 | Folders Scanned | Directories processed |
| 📄 | Files Processed | Total files analyzed |
| ✅ | Unique Files | Distinct SHA-256 content groups; includes the retained file from duplicate groups |
| ⛔ | Duplicates Found | Extra copies detected across the entire recursive `source_path` tree |
| 📦 | Files Moved | Duplicates moved to the review folder |
| 🗑️ | Files Deleted | Permanently removed duplicates |
| ⚡ | Files Per Minute | Processing speed |
| 🧵 | Workers | Parallel threads used |
| ⏱️ | Execution Time | Total runtime |

---

### ⚙️ Configuration Summary

Shows the parameters used in the run:

- Source path
- Duplicate action (move or delete)
- Extensions analyzed
- Selection rule used (lowest filesystem `ctime`; case-insensitive filename on tie)

Only byte-for-byte identical files share a duplicate group. The scan is global across all subfolders, rather than restarting the comparison for each folder.

---

## ↩️ Undo Report

### 📈 Statistics

| Icon | Metric | Description |
|------|----------|-------------|
| 🗑️ | Files Deleted | Successfully removed files |
| ❓ | Not Found | Files listed in the log but missing from the destination |
| ⚠️ | Errors | Removal failures |
| 📂 | Folders Removed | Empty folders deleted |
| 🗓️ | Years Affected | Year folders impacted by the undo |
| 🌍 | Countries Affected | Country folders impacted by the undo |

---

### 📋 Undo Details

Displays:

- Reference log file used for the undo
- Undo execution log path
- Execution timestamp

---

### 🗓️ Affected Years and Countries

Visual tags listing the years (e.g. `2019`, `2020`) and countries (e.g. `Brazil`, `Argentina`) whose folders were removed during the undo operation.

---

### ❓ Files Not Found

Lists files that appeared in the original log but were no longer present at the destination when the undo ran. This can happen if files were manually moved or deleted beforehand.

---

### ⚠️ Errors

Lists any failures encountered while removing files or folders, such as permission errors.
