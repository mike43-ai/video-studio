# Pitfalls (already hit)

## Voice
- `*accent*` markers are stripped before TTS; if a raw `*` is heard, the cache key predates the fix → delete `.tts-cache/`.
- VieNeu reads numbers/abbreviations its own way. Write them as spoken: "tám giây", "hai nghìn không trăm hai sáu", "AI" → "ây ai".
- Very short lines (≤ 3 syllables) may "babble"; merge with the neighbour line.
- First VieNeu run downloads ~1 GB from Hugging Face; first Whisper run ~1.6 GB. Warn the user about the wait.
- CPU speed (4-core Intel, 8 GB): VieNeu ≈ 3× real time, Whisper large-v3-turbo ≈ 6× real time.
  For long scripts use `--model small` for a draft transcription.

## Transcription
- Whisper's Vietnamese spelling of names/terms is unreliable → always pass `--script`.
- "matched exactly" low → the TTS likely skipped or misread a line. Listen to that line, rephrase, re-voice.
- Recognized-only words (fillers) are dropped by the alignment; script words Whisper missed get interpolated times.

## Beats & cuts
- librosa BPM can be off by a few % (117.5 vs real 120) or doubled/halved; beat *times* are still usable.
- Downbeat = every 4th beat with the strongest onset phase — wrong for 3/4 or rubato; set scenes by hand then.
- Cuts snap only EARLIER (never after the first word). Increase `--snap` if few cuts land on beats.

## Render
- 8 GB RAM → HyperFrames auto low-memory mode (1 worker, screenshot capture): ~3 s render per 1 s of 1080x1920.
- Chrome stalls on long renders → `vs render` uses segmented capture; just re-run the same command (`--resume`).
- Google Fonts loads from CDN at render time; offline machine → download the font, put it next to index.html, use `@font-face`.
- Don't `gsap.set()` elements of later scenes at load; use `tl.set(el, vars, time)`.
- Never `repeat: -1`; never build the timeline in async code.
- `<br>` in scene text breaks wrapping — let `max-width` wrap.

## Delivery
- `deliver` copies the video stream; if the render has odd dimensions/codec for a platform, re-encode:
  `ffmpeg -i final.mp4 -c:v libx264 -crf 18 -pix_fmt yuv420p -c:a copy out.mp4`.
- Report the measured LUFS/duration from `deliver`, not the target.

## New tools
- Intel Mac: `pip install opencv-python-headless numpy` picks an old numpy and COMPILES it (hangs for ages) →
  install.sh uses `--only-binary :all:`. Do the same for any manual install.
- `hyperframes snapshot` calls Gemini vision by itself when `GEMINI_API_KEY` is in the environment (paid) —
  `vs render --stills/--thumb`, `vs review animatic`, `vs check --page` unset it. Call snapshot through `vs`.
- Some ffmpeg builds (Homebrew 8.x here) have no `drawtext`/`subtitles` filter → `vs compose` draws subtitles
  with Pillow instead; don't hand-write ffmpeg commands that rely on them.
- `vs check` cue audibility is measured above 2.5 kHz: a deep boom under speech can show ⚠ while being fine —
  listen before "fixing" it. Speech check reads digits as words differently ("tám" vs "8") → ⚠, not an error.
- `vs music analyze` (librosa) may report half/double tempo and a drop one bar early on synth beds; the drop
  time printed by `vs music make` is the true one.
- `vs fetch` of YouTube: yt-dlp takes ~30 s to start on this machine; auto-subtitles are de-duplicated but
  timing per word is spread evenly inside each cue (use Whisper on `audio.wav` for exact word times).
- `vs redact` finds QR codes and faces only; numbers/names in text must be boxed by hand after looking.
