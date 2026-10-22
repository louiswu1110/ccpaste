# ccpaste

Sharp previews of everything you paste into Claude Code: images and text, every session, in your terminal via Sixel.

English | [繁體中文](https://github.com/louiswu1110/ccpaste/blob/main/README.zh-TW.md)

![ccpaste demo](https://raw.githubusercontent.com/louiswu1110/ccpaste/main/docs/demo.gif)

## Why

Claude Code only draws real pictures in terminals that speak the kitty graphics protocol, Ghostty or iTerm2. Elsewhere, for example Windows Terminal under WSL, pasted images show up as blurry half-block thumbnails. ccpaste is a small TUI you run in a second pane next to Claude Code. It lists your sessions on the left and draws the selected session's pasted images sharply via Sixel, plus the long text pastes, labelled like Claude Code labels them (`#1`, `#2` for images, `T#5` for text pastes).

## Features

- Sharp Sixel previews of pasted images: a thumbnail strip plus a large preview.
- Long text pastes shown as plain wrapped text, scrollable, with the same `T#N` labels Claude Code uses.
- A session sidebar (newest first, with an "All sessions" view); new items and sessions appear automatically.
- Keyboard and mouse: vim-style keys, clicks, wheel, drag-to-copy, and a right-click context menu.
- Copy a path, a text paste, or the image itself to the clipboard (see Context menu and toasts for backends).
- Non-destructive hide and optional auto-hide of old sessions; Claude Code's files are never modified.
- Never modifies Claude Code's files and keeps no copies of your pastes; the only thing it writes is its own `hidden.json`.
- A single Python file with one dependency (Pillow).

## Install

You need a terminal that speaks **Sixel**, for example Windows Terminal 1.22 or newer. macOS and Linux are supported as long as the terminal speaks Sixel; no other terminals have been verified. Run `ccpaste` in a second pane next to Claude Code.

### Recommended: from PyPI (all platforms)

```
uv tool install ccpaste
```

Alternatives:

```
pipx install ccpaste
pip install --user ccpaste
```

Need uv? Official installer ([docs](https://docs.astral.sh/uv/getting-started/installation/)):

```
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### Standalone binary (no Python needed)

Download from the [Releases page](https://github.com/louiswu1110/ccpaste/releases). Assets:

| Platform | Asset |
|---|---|
| Windows x86_64 | `ccpaste-windows-x86_64.zip` (contains `ccpaste-windows-x86_64.exe`) |
| Linux x86_64 | `ccpaste-linux-x86_64` |
| Linux aarch64 | `ccpaste-linux-aarch64` |
| macOS Apple silicon | `ccpaste-macos-aarch64` |
| macOS Intel | `ccpaste-macos-x86_64` |

Windows (PowerShell):

```powershell
Invoke-WebRequest https://github.com/louiswu1110/ccpaste/releases/latest/download/ccpaste-windows-x86_64.zip -OutFile ccpaste.zip
Expand-Archive ccpaste.zip -DestinationPath .
.\ccpaste-windows-x86_64.exe
```

macOS / Linux (swap in your platform's asset name):

```
curl -L -o ccpaste https://github.com/louiswu1110/ccpaste/releases/latest/download/ccpaste-linux-x86_64
chmod +x ccpaste
./ccpaste
```

The binaries are unsigned. Windows SmartScreen may warn on first run (More info, then Run anyway). If you downloaded a binary with a browser, macOS quarantines it; clear the flag once with `xattr -d com.apple.quarantine ccpaste-macos-aarch64` (or the file name you downloaded). Binaries fetched with `curl` are not quarantined, so this is not needed for them.

### From source

Needs Python 3 and Pillow. The program is the single file `ccpaste`:

```
pip install --user pillow
python3 ccpaste
```

Or put it on your PATH: `ln -s "$PWD/ccpaste" ~/.local/bin/ccpaste`.

## Screenshots

![Image thumbnails and a large Sixel preview](https://raw.githubusercontent.com/louiswu1110/ccpaste/main/docs/screenshot-preview.png)

*Sessions on the left; a thumbnail strip and a large preview of the selected image on the right.*

![A text paste with the copy toast](https://raw.githubusercontent.com/louiswu1110/ccpaste/main/docs/screenshot-text.png)

*A text paste (`T#7`) shown as text; selecting text copies it and a green "copied N chars" toast confirms.*

![The right-click context menu](https://raw.githubusercontent.com/louiswu1110/ccpaste/main/docs/screenshot-menu.png)

*Right-click (or `m`) opens a context menu: Copy path, Copy object, Hide.*

## Usage

```
ccpaste                  # sidebar of sessions, newest first
ccpaste -s <id-prefix>   # open one session directly (ambiguous prefix lists candidates and exits 1)
ccpaste --list           # print the sessions (title, id, project, time, counts) and exit
ccpaste --list -s <id>   # print that session's item labels and exit
```

The screen is split: a sidebar of sessions on the left (first row "All sessions", then one two-line row per session: title with the 8-character id, then project, age and counts) and the selected session's content on the right (thumbnail strip, info line, large preview). Without `-s` the sidebar starts focused on the newest session; with `-s` the right pane starts focused. New items and sessions appear automatically (polled about once a second); a newly arrived item in the shown session becomes the selection.

| Focus | Key | Action |
|---|---|---|
| Sidebar | Up / Down, k / j | previous / next session (right pane updates live) |
| Sidebar | g / G | first / last row |
| Sidebar | Enter, Right, l, Tab | focus the right pane |
| Right pane | Left / Right, h / l | previous / next item |
| Right pane | Up / Down, k / j | move one thumbnail row |
| Right pane | g / G | first / last item |
| Right pane | PgUp / PgDn | scroll a text paste by a page |
| Right pane | K / J | scroll a text paste by 3 lines |
| Right pane | Tab, Esc, s | focus the sidebar |
| Both | b | show / hide the sidebar |
| Both | r | force redraw |
| Both | d / u / H | hide or un-hide the selection / undo the last hide / show hidden items |
| Both | ? | help overlay: the main keys and mouse actions, plus the cell size in use (any key closes it) |
| Both | m | open the context menu for the selection (same as right-click) |
| Both | y | copy the selected item's path to the clipboard |
| Both | [ / ] | shrink / grow the assumed terminal cell size by 10% (calibration if images overflow) |
| Both | q, Ctrl+C | quit |

## Hide

Hiding is non-destructive: ccpaste never modifies or deletes Claude Code's files. It keeps its own list in `~/.config/ccpaste/hidden.json` (`$XDG_CONFIG_HOME/ccpaste/hidden.json` if set; `%APPDATA%\ccpaste\hidden.json` on Windows):

```json
{"version": 1, "sessions": ["<sessionId>"], "items": ["<sid>:img:<n>", "<sid>:txt:<id>"], "auto_hide_days": null}
```

- `d` hides the selected session when the sidebar is focused, or the selected item (image or text paste) when the right pane is focused. "All sessions" cannot be hidden. Hidden things disappear from the sidebar, the All view, the counts and the thumbnail strip.
- Right after hiding, a toast says "hidden ... · u to undo"; `u` restores the most recent hide (repeat for older ones, for this run). `d` only toggles the selection: it hides, or un-hides something that is already hidden and shown via `H`.
- `H` toggles "show hidden": hidden sessions and items then appear dimmed with a marker, and `d` un-hides the selected one.
- Auto-hide: set `auto_hide_days` in the json, or pass `ccpaste --hide-older N`, to hide sessions whose last activity is older than N days. This is computed on the fly and never written to the file; `H` shows them too, and `d` does not un-hide them (change the setting instead).
- The file is written atomically. A missing or corrupt file is treated as empty (a corrupt one is copied to `hidden.json.bak`).

The footer is deliberately short: your position on the left (`#10 · 376/376`; in the All view the item label also carries its 4-character session prefix) and at most three hints plus `? help` on the right.

## Mouse

Mouse tracking is enabled while ccpaste runs (button-event tracking with SGR coordinates) and switched off again on exit. Hold Shift while dragging to use the terminal's own selection instead (terminal-dependent).

| Action | Effect |
|---|---|
| Click a sidebar row | select that session, focus the sidebar |
| Click a thumbnail | select that item, focus the right pane |
| Click the divider | show / hide the sidebar |
| Right-click a thumbnail, the preview or the info / path lines | select that item and open the context menu (Copy path, Copy object, Hide / Unhide) |
| Right-click a sidebar session row | context menu (Hide / Unhide session, Copy session id) |
| Wheel over the sidebar | scroll the session list |
| Wheel over the thumbnails | previous / next item |
| Wheel over a text paste | scroll the text (over an image: previous / next item) |
| Drag over a text paste or the path line | highlight; the selected text is copied on release |
| Double-click | select the word under the pointer and copy it |
| Drag past the top / bottom edge of a text paste | autoscroll while selecting |

Copying uses the native clipboard where possible (Windows: Win32 API; macOS: `pbcopy`; Linux: `wl-copy`, `xclip` or `xsel`) and otherwise hands the text to the terminal (the OSC 52 escape; this is the normal route over SSH and under WSL; confirmed working in Windows Terminal under WSL, other terminals unverified). A green toast "copied N chars" confirms it either way. Over a remote editor terminal that carries neither SSH variables nor a WSL kernel but does have `DISPLAY`/`WAYLAND_DISPLAY` set (e.g. a VS Code tunnel into a desktop Linux box), a local tool is chosen and the text lands on the remote machine's clipboard.

## Windows (native)

ccpaste also runs in Windows Terminal from cmd or PowerShell, without WSL: install Python 3 and Pillow (`pip install pillow`), use Windows Terminal 1.22 or newer (for Sixel), then run `python ccpaste` (or `python ccpaste -s <id>`). It reads Claude Code's files from `%USERPROFILE%\.claude` and `.claude-*` and the pasted images from `<CLAUDE_CODE_TMPDIR or %TEMP%>\claude`; the images location is based on a third-party plugin's description of Claude Code and is unverified. **Windows support is untested on real hardware**: the console setup (VT input and output modes, raw input, quick-edit off), input reader, native clipboard and path handling are implemented but only their pure logic is covered by tests that run on Linux. Please report what breaks.

## Context menu and toasts

Right-click (or `m`) opens a small bordered menu at the pointer; it flips left or up near the window edges and never covers the footer. Hover with the mouse (motion tracking is only enabled while a menu is open) or use Up / Down / j / k and Enter; Esc, `q` or a click outside closes it. Every item, image or text, gets the same menu:

- **Copy path**: an image's file path; for a text paste the paste-cache file path if it has one, otherwise the `history.jsonl` it came from.
- **Copy object**: a text paste's full text, or the image itself on the clipboard (so it can be pasted into other apps, including Claude Code). Image backends: Windows native (`CF_DIB` plus a `PNG` format), WSL (`powershell.exe` with `System.Windows.Forms`), macOS (`osascript`), Linux (`wl-copy` or `xclip`). If no backend works you get "couldn't copy image — copied its path instead". The WSL backend is confirmed working (Windows Terminal, pasting back into Claude Code); the other image backends are implemented but unverified on real systems.
- **Hide / Unhide**: see Hide.

Sidebar rows get "Hide session" / "Unhide session" and "Copy session id". Every action confirms with a toast (a rounded box at the bottom right, just above the footer, green; yellow only for real problems such as an image that could not be copied): "copied path", "copied image", "copied N chars", "hidden ... u to undo". Toasts and menus are drawn over the image preview; when they go away the Sixel underneath is re-placed from the cache.

## Environment variables

- `CLAUDE_CONFIG_DIR`: Claude Code config directory (default `~/.claude`).
- `CLAUDE_CODE_TMPDIR`: Claude Code temp root; images are looked up under `${CLAUDE_CODE_TMPDIR:-/tmp}/claude-<uid>`.
- `CCPASTE_CONFIG_DIRS`: config directories to read, separated by `:` (`;` on Windows); by default `$CLAUDE_CONFIG_DIR`, `~/.claude` and every `~/.claude-*` with `projects/` or `history.jsonl`.
- `CCPASTE_TMP_ROOT`: override that images root directly (used by the tests).
- `CCPASTE_CELL=WxH`: terminal cell size in pixels, for example `10x20`. Normally ccpaste asks the terminal (window pixel size, then `CSI 16t`) and falls back to a conservative 10x20 guess; the help overlay (`?`) shows the size in use and where it came from.

## Where data comes from

ccpaste only reads Claude Code's files, never modifies them, and keeps no copies; the only file it writes is its own hidden list.

- Images: `<tmp root>/<project-slug>/<sessionId>/images/<N>.<ext>`, where N is the number in `[Image #N]`. Any extension is accepted; files that Pillow cannot decode show a text placeholder.
- Text pastes: `$CLAUDE_CONFIG_DIR/history.jsonl` (`pastedContents` of each entry), resolving `contentHash` entries from `$CLAUDE_CONFIG_DIR/paste-cache/<hash>.txt`. Entries appear only after the prompt is submitted.
- Session titles: the last `ai-title` (a `custom-title` takes priority) in `$CLAUDE_CONFIG_DIR/projects/<slug>/<sessionId>.jsonl`, read incrementally; falls back to the first prompt, then "(untitled)". Nothing else from transcripts is read out.

## Limitations

- Only sessions with at least one pasted image or text appear (or the one named with `-s`). Pastes whose cached text Claude Code has already pruned from `paste-cache` cannot be recovered and are not listed.
- Rendering was developed against the Sixel behaviour of Windows Terminal; other terminals are untested. Image quality is limited to a 256-colour palette per image.
- Images are decoded and encoded in a background thread, so a very large image shows a placeholder for a moment.
- Pasted text is shown as plain wrapped text; there is no search.

## License

[MIT](https://github.com/louiswu1110/ccpaste/blob/main/LICENSE) © 2026 Louis Wu
