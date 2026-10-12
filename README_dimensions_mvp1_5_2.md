# Dimensions Conversation Extractor — MVP 1.5.2

Patch acceptance logic untuk range extraction.

## Kenapa berubah?

Pada Dimensions, conversation yang ada di frozen snapshot bisa hilang sebelum detailnya sempat dibuka, misalnya karena sudah direview/submitted secara manual.

MVP 1.5.1 mewajibkan:

```text
missing_from_active_snapshot = 0
```

sehingga 227/229 detail (99.13%) tetap menghentikan seluruh range.

MVP 1.5.2 memakai **hybrid acceptance**, bukan sekadar menghitung berapa acceptance check yang lolos.

## HARD checks — tetap wajib 100%

Tidak boleh dilonggarkan:

```text
pagination_complete_all = True
final_has_more_all      = False
errors_in_db            = 0
click_failures          = 0
```

Jika salah satu gagal, tanggal gagal dan range berhenti secara default.

## SOFT detail coverage

Default:

```text
minimum detail coverage = 90%
maximum missing details = 25
```

Contoh:

```text
227 / 229 = 99.13%
missing   = 2
```

akan menjadi:

```text
ACCEPTED WITH GAPS
```

dan range lanjut ke tanggal berikutnya.

Missing IDs tetap disimpan di:

```text
dimensions_output/<date>/missing_conversation_ids.json
```

## Run

Default:

```powershell
py dimensions_extractor_mvp1_5_2.py `
  --start-date 2026-09-01 `
  --end-date 2026-09-05
```

Custom threshold 95%:

```powershell
py dimensions_extractor_mvp1_5_2.py `
  --start-date 2026-09-01 `
  --end-date 2026-09-05 `
  --min-detail-coverage 0.95
```

Custom 90% + maximum 10 missing:

```powershell
py dimensions_extractor_mvp1_5_2.py `
  --start-date 2026-09-01 `
  --end-date 2026-09-05 `
  --min-detail-coverage 0.90 `
  --max-missing-details 10
```

`-1` disables the absolute missing cap:

```powershell
--max-missing-details -1
```

Tidak direkomendasikan untuk unattended backfill.

## Status

Per date `summary.json` sekarang mencatat:

```text
detail_coverage_pct
hard_checks_passed
coverage_check_passed
missing_count_check_passed
acceptance_status
```

`acceptance_status` dapat berupa:

```text
complete
accepted_with_gaps
failed_acceptance
```

Tanggal `accepted_with_gaps` dianggap selesai untuk resume, sehingga tidak diproses terus-menerus pada setiap rerun kecuali memakai:

```powershell
--rerun-complete
```

## Safety

Threshold hanya berlaku untuk **detail coverage**.

Date/API verification, pagination integrity, DB errors, dan click failures tetap fail-closed.
