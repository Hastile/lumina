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
| Ctrl+M, Ctrl+Shift+E | Export Media, Export Frame | ✓ |
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
| Motion (position/scale/rotation/anchor/crop), Opacity, their keyframes | ✓ |
| Native text layers: styled runs, font/size, fill, stroke, tracking, point/area text with alignment, shadow, hidden (eye off) layers | ✓ (paragraph wrapping in area boxes ✗) |
| After Effects MOGRT text fields: the template's layout, fonts, colors and strokes read from its .aep, edited values applied | ✓ text layers only (AE shapes/effects other than Drop Shadow ✗) |
| Shape layers (rectangle/ellipse/pen paths, fill color, transform, stacking, hidden layers) | ✓ stroke/gradient/shadow not decoded |
| Other effects (blur, Transform effect, …) | ✗ (reported) |
| Transitions | ✗ (reported) |
| Captions (.srt items) | ✗ (reported) |
| Nested sequences | ✗ (M2, reported) |

## Motion & graphics
| Item | Status |
|---|---|
| Clips render at native size like Premiere (no auto-fit); Properties > Fill / Fit | ✓ |
| Effect Controls: Motion / Opacity / Time Remapping, stopwatch, keyframe navigator, keyframe lane | ✓ |
| Drag a clip in the Program monitor to move it | ✓ (scale/rotate handles ✗) |
| Type tool (T) / Ctrl+T new text layer, text editing in Properties (font, size, bold/italic, align, fill, stroke, background) | ✓ |
| Rectangle / Ellipse tools (drag in the Program monitor, Shift = square), Ctrl+Alt+R / Ctrl+Alt+E, Graphics and Titles > New Layer | ✓ (Pen tool paths ✗) |
| Graphic layer stack: eye toggle, bring forward / send backward, fill, stroke, size, position, rotation, opacity | ✓ |
| Keyframe interpolation: linear / ease (≈ Premiere's default Bezier) / hold | ✓ (custom Bezier handles ✗) |

## Effects & transitions
| Item | Status |
|---|---|
| Effects panel tree (Video Effects > Distort > Transform, Blur & Sharpen > Gaussian Blur; Video/Audio Transitions), search, drag onto a clip or edit point, double-click to apply | ✓ |
| Presets folder: user effect presets imported from Premiere's profile (.prfpset), keyframes anchored to in/out point or scaled as saved; drag or double-click to apply | ✓ (presets of effects Lumina doesn't render are skipped) |
| Effect Controls: effect groups with fx on/off, delete, keyframes (stopwatch / navigator / lane) per parameter | ✓ |
| Ctrl+D / Ctrl+Shift+D / Shift+D default transitions; transitions drawn on the timeline, select + Delete, Set Transition Duration... | ✓ |
| Transitions follow trims; removed when their edit point disappears (same undo step) | ✓ |
| .prproj: Transform (Geometry2) with keyframes, Gaussian Blur, transitions (incl. Film Impact dissolves as Cross Dissolve) | ✓ |
| Other effects (Lumetri, Crop effect, Warp Stabilizer, ...), transition handles UI, effect presets | ✗ |

## Export
| Item | Status |
|---|---|
| File > Export > Media (Ctrl+M): file name, location, format, range (entire / In-Out), hardware encoding, preview frame | ✓ |
| Formats: H.264, HEVC, ProRes 422 HQ, WAV; NVENC / AMF / Quick Sync auto-detected | ✓ |
| Render queue card: progress, fps, ETA, cancel, Show in Explorer | ✓ |
| Output verified with ffprobe (duration, size, streams) before "done" | ✓ |
| What renders: Motion, crop, opacity, keyframes, speed, text graphics, track eye/mute/solo, clip volume + keyframes | ✓ |
| Effects (Transform, Gaussian Blur) and transitions (Cross Dissolve, Dip to Black/White, Constant Power) | ✓ |
| Other effects, nested sequences, blend modes, audio pan / track volume | ✗ (not rendered yet) |

## Language
Korean (Premiere's Korean terminology) is the default; View > Language switches to English.
