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
| Ctrl+X / Ctrl+C / Ctrl+V / Ctrl+Shift+V / Ctrl+Alt+V | Cut / copy / paste / paste insert / paste attributes | ✓ |
| Ctrl+M, Ctrl+Shift+E | Export Media, Export Frame | ✓ |
| Ctrl+Alt+K editor, `.kys` import | Keyboard shortcuts | ✗ |

## Timeline
| Item | Status |
|---|---|
| Track headers: source patch, targeting, lock, sync lock, eye / M / S | ✓ (patching is not separate from targeting yet) |
| Clips with label colors, names, speed %, disabled look, waveforms | ✓ |
| Thumbnails on video clips | ✓ (continuous, from a per-media filmstrip) |
| Drag move with snapping, Ctrl-drag insert, Alt-click ignores links | ✓ |
| Edge trim, ripple trim (B) | ✓ |
| Rolling (N), Rate Stretch (R), Slip (Y), Slide (U) tools | ✓ (linked clips follow; Alt = this clip only) |
| Pen (P), Type (T) tools on the timeline | ✗ |
| Marquee selection | ✓ (Shift adds) |
| Rubber bands (volume) | ✓ (opacity band ✗) |
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
| Keyframe interpolation: linear / ease / Bezier (speed + influence per side) / hold | ✓ |
| Effect Controls twirl-down per property: value slider / angle dial, Value and Velocity graphs with draggable Bezier handles (`clip.set_keyframe_ease`, `effect.set_keyframe_ease`) | ✓ |
| Effect Controls timeline zoom: Ctrl/Alt+wheel around the pointer, Shift+wheel scroll, double-click to fit | ✓ |

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

## Playback (monitors)
| Item | Status |
|---|---|
| Frame-exact Program/Source monitors: WebCodecs decoding (MP4/MOV/WebM/MKV via mediabunny) of the original media, one clock, the frame active at the playhead (same rule as export) | ✓ (codecs Chromium can't decode fall back to proxy / <video>) |
| Cuts and transitions play without a stall: upcoming clips pre-decoded, decoder streams kept warm | ✓ |
| Scrubbing shows the exact frame (typically 15-90 ms per jump) | ✓ |
| Program monitor shows sequence frames only (a 60 fps source in a 30 fps sequence plays at 30, like Premiere and the export) | ✓ |
| Program audio on Web Audio: every clip scheduled sample-accurately at its cut, the audio output is the playback clock (no element warm-up, no rate nudging) | ✓ (media Web Audio can't decode fall back to <audio>; reverse play is silent) |
| J/K/L shuttle incl. reverse play (long-GOP sources decoded in 0.5 s chunks) and 2x-8x | ✓ |
| Select Playback Resolution (Full, 1/2, 1/4; paused frames full) | ✓ |
| Shift+J/L slow motion, trim-mode dual view, dropped-frame indicator, render bar / preview files | ✗ |
| Program monitor composited on the GPU (WebGL2) in the export renderer's order: Motion, crop, opacity, Transform/Gaussian Blur effects, transitions; text and shapes rasterized by the export renderer | ✓ mean pixel difference vs export < 1 (CSS fallback without WebGL2) |

## Keyframes & audio
| Item | Status |
|---|---|
| Effect Controls keyframes: click / Shift+click, marquee select, drag in time (Shift snaps to the playhead), Delete | ✓ |
| Right-click Temporal Interpolation: Linear, Bezier, Auto Bezier, Continuous Bezier, Hold, Ease In, Ease Out; per-side icons | ✓ |
| Shift while scrubbing the ruler snaps the playhead to edits, markers, In/Out | ✓ |
| Audio Meters (peak hold, clip LEDs), Audio Track Mixer (fader, pan, M/S, meters, Mix), Audio Clip Mixer | ✓ |
| Volume rubber band on audio clips; Audio Gain (G: set, adjust, normalize max / all peaks); audio scrubbing | ✓ |
| Audio effects: Parametric Equalizer, Single-band Compressor, Hard Limiter (preview = export for EQ; dynamics within ~1 dB) | ✓ |
| Essential Sound: audio types, Auto-Tag (file name + speech/music/ambience analysis), Loudness Auto-Match (LUFS) | ✓ (heuristic tagger, not Premiere's AI model) |
| Auto-Match judgement: measured through speed + audio effects (compressor/limiter re-measured), one gain per source so cuts keep their dynamics (outliers pulled halfway), music under dialogue as a bed (-12 LU), true-peak ceiling, +20 dB boost cap, noise warning, sub-0.5 dB changes skipped | ✓ (Lumina extension) |
| Spatial interpolation menu for Position, keyframes on the rubber band dragged in time, clip pan | ✗ |

## Export
| Item | Status |
|---|---|
| File > Export > Media (Ctrl+M): file name, location, format, range (entire / In-Out), hardware encoding, preview frame | ✓ |
| Formats: H.264, HEVC, ProRes 422 HQ, WAV; NVENC / AMF / Quick Sync auto-detected | ✓ |
| Render queue card: progress, fps, ETA, cancel, Show in Explorer | ✓ |
| Output verified with ffprobe (duration, size, streams) before "done" | ✓ |
| What renders: Motion, crop, opacity, keyframes, speed, text graphics, track eye/mute/solo, clip volume + keyframes | ✓ |
| Effects (Transform, Gaussian Blur) and transitions (Cross Dissolve, Dip to Black/White, Constant Power) | ✓ |
| Other effects, nested sequences, blend modes | ✗ (not rendered yet) |

## Language
Korean (Premiere's Korean terminology) is the default; View > Language switches to English.
