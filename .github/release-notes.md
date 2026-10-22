Single-file binaries, no Python needed. You still need a terminal that speaks Sixel (Windows Terminal 1.22+ or another Sixel terminal).

**Windows (x86_64):** unzip `ccpaste-windows-x86_64.zip` and run `ccpaste-windows-x86_64.exe` in Windows Terminal. The binary is unsigned, so SmartScreen may warn on first run.

**Linux / macOS:**

```
chmod +x ccpaste-linux-x86_64        # or the file for your platform
./ccpaste-linux-x86_64
```

On macOS, clear the quarantine flag first: `xattr -d com.apple.quarantine ccpaste-macos-*`.

**With Python 3 + Pillow:** download `ccpaste.py` and run `python3 ccpaste.py`.

Linux binaries need glibc 2.35 or newer (Ubuntu 22.04+, Debian 12+).

Verify downloads against `SHA256SUMS` (it lists every file, so unrelated lines are skipped):

- Linux: `sha256sum -c --ignore-missing SHA256SUMS`
- macOS: `shasum -a 256 -c --ignore-missing SHA256SUMS`
- Windows (PowerShell): `Get-FileHash <file>`, then compare the hash with the matching line in `SHA256SUMS`

This is an early release. The automated test only checks that each binary starts and can render an image, not that it works interactively; the Windows and macOS builds have not yet been tried interactively.
