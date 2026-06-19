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
| 🌍 | With GPS | Files containing GPS coordinates |
| ❓ | Without GPS | Files without GPS coordinates (when GPS is enabled) |
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

Files without coordinates are grouped under:

```text
(No GPS)
```

---

### 🗂️ Folder Structure Summary

A hierarchical listing of folders created at the destination. For each folder, displays:

- Full path
- Image count
- Video count
- Total files
- Sample list of the first 50 copied files

> 💡 Files beyond the first 50 are omitted for report performance.

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
| ✅ | Unique Files | Files without duplicates |
| ⛔ | Duplicates Found | Duplicate files detected |
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
- Selection rule used (oldest file is kept; alphabetical order on tie)

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
