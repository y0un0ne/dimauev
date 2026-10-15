# Windows Release Build

Build on Windows 10/11 x64 with Python 3.12 and Node.js installed.

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\packaging\build_release.ps1
```

This creates a portable onedir ZIP, per-user setup executable, and `SHA256SUMS.txt` under `release/`. Use `-SkipInstaller` only for local packaging diagnostics.

Verify the packaged folder:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\packaging\verify_release.ps1
```

Build outputs, bundled browser cache, and installer artifacts are ignored by Git. Publish them through GitHub Releases, not source commits.
