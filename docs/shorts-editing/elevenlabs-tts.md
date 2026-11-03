---
name: elevenlabs-tts
description: "Shorts narration is made with ElevenLabs (not D:\\Dev\\tts); key location, voice, model and v3 audio-tag style"
metadata:
  node_type: memory
  type: project
  originSessionId: 3899aeb6-a6c5-49f4-97f8-45adc1ac9f74
  modified: 2026-10-22T05:42:57.369Z
---

The user generates Shorts narration with ElevenLabs on the website (stated 2026-10-22). No code in D:\Dev calls the API yet; D:\Dev\tts is a separate local Seed-VC/Qwen experiment and is not used, post.py included.

- API key: `C:\Users\HETX\.config\elevenlabs\.env` (`ELEVENLABS_API_KEY`). Never print it.
- Voice "Caddynote v2", voice_id `io5IHZ9hdFs3rdaF6R5r`.
- Generated as text-to-dialogue with one speaker; history shows `text` empty and the script inside `dialogue`.
- Model eleven_v3; the site makes each take twice, as eleven_v3 and eleven_v3_dpo. Settings: stability 0.5, similarity 0.75. Output mp3_44100_128.
- The script is the whole Short in one request, with line breaks per sentence and v3 audio tags at the start of a section: `[brisk, matter-of-fact]` by default, `[rapid-fire]` for a dense list of facts (example: 퓨키 데모 취소, 2026-10-21).
- The site's download filename (e.g. `ElevenLabs_2026-10-21T14_28_25__s50_v3.mp3`) says v3 for both takes of a pair; its UTC timestamp identifies the history item (14:28:25 was the eleven_v3_dpo item). Match by timestamp or waveform, not by the model in the name.
- The user regenerates many takes per script (8 pairs for 퓨키) and picks one.

**Why:** step 1 of [[shorts-automation-pipeline]] can be automated through the API instead of the site.
**How to apply:** for a new Short, call text-to-dialogue with this voice and model, make several takes for the user to pick, then apply only the Hard Limiter (the user's only Audition step, see [[shorts-automation-pipeline]]).
