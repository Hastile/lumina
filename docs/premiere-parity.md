# Premiere parity checklist

The goal is that a Premiere user can switch with no relearning. Update this
file at every milestone. Treat each ✗ that is in scope for the milestone as a bug.

## Layout (Editing workspace)
| Item | Status |
|---|---|
| Source / Effect Controls / Audio Clip Mixer tabs at top left | ✓ |
| Program monitor at top right | ✓ |
| Project / Media Browser / Effects at bottom left, then Tools, Timeline, Audio Meters | ✓ |
| Workspace bar (Learning … Libraries) | ✓ visual only; only Editing works |
| Dockable, re-arrangeable panels; `` ` `` maximizes a panel | ✓ |

## Shortcuts (Windows defaults)
| Keys | Action | Status |
|---|---|---|
| V A B N R C Y U P H Z T | Tools | ✓ selectable; working: V, A, B (ripple trim drag), C, H, Z |
| Space, J K L, ←/→, Shift+←/→, Home/End, ↑/↓ | Transport | ✓ (reverse plays by seeking) |
| I, O, X, Shift+I/O, Ctrl+Shift+X | Marks | ✓ |
| `,` `.` | Insert / Overwrite from Source | ✓ |
| `;` `'` | Lift / Extract | ✓ (one undo step) |
| Ctrl+K, Ctrl+Shift+K | Add Edit / to All Tracks | ✓ |
| Q, W | Ripple trim previous/next edit to playhead | ✓ |
| Delete, Shift+Delete | Clear / Ripple Delete | ✓ |
| M | Add Marker | ✓ |
| S | Snap | ✓ |
| Shift+E, Ctrl+L, Ctrl+R | Enable, Link/Unlink, Speed/Duration | ✓ |
| = - \ | Zoom in / out / to sequence | ✓ |
| Ctrl+Z, Ctrl+Shift+Z, Ctrl+S, Ctrl+Shift+S, Ctrl+O, Ctrl+I, Ctrl+N, Ctrl+Alt+N | File / Edit | ✓ |
| Shift+1…8 | Focus panels | ✓ |
| Ctrl+C / Ctrl+V / Ctrl+Shift+V | Copy / paste / paste attributes | ✗ |
| Ctrl+M | Export Media | ✗ (M5) |
| Ctrl+Alt+K editor, `.kys` import | Keyboard shortcuts | ✗ |

## Timeline
| Item | Status |
|---|---|
| Track headers: source patch, targeting, lock, sync lock, eye / M / S | ✓ (patching is not separate from targeting yet) |
| Clips with label colors, names, speed %, disabled look, waveforms | ✓ |
| Thumbnails on video clips | ✗ |
| Drag move with snapping, Ctrl-drag insert, Alt-click ignores links | ✓ |
| Edge trim, ripple trim (B) | ✓ |
| Rolling (N), Rate (R), Slip (Y), Slide (U), Pen (P), Type (T) tools | ✗ (M2/M4) |
| Marquee selection | ✗ |
| Rubber bands (volume / opacity) | ✗ (M3) |
| Nested sequences | ✗ (M2) |

## Premiere project hand-off (.prproj, read-only)
`File > Open Project` and `File > Import` accept `.prproj`, and so does dropping one onto the Project panel.
The `project_import_premiere` MCP tool does the same. The source file is never modified.

| Item | Status |
|---|---|
| Bin tree, file media (thumbnails/waveforms/proxies generated automatically) | ✓ |
| Sequences: frame rate, frame size, track count, clip placement, source in, speed | ✓ |
| Video+audio link groups, label colors, track mute, sequence markers, playhead | ✓ |
| Duplicate sequence names → disambiguated with the bin name | ✓ |
| Motion/opacity/effects/keyframes | ✗ (M3), so scaled shorts footage shows letterboxed |
| Transitions | ✗ (reported) |
| MOGRT / Essential Graphics / captions (.aegraphic, .srt) | ✗ (M4, reported) |
| Nested sequences | ✗ (M2, reported) |
