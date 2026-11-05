# File formats

All times in seconds (float, 3 decimals), measured from the start of `voice.wav` = start of the video.

## lines.json (tts.py)
```json
[{"i": 0, "text": "Bạn có biết não người chỉ tập trung được khoảng *tám giây*?", "start": 0.0, "end": 2.33, "para": 0}]
```
`text` keeps `*accent*` markers; the spoken audio does not include them.

## words.json (transcribe.py)
```json
[{"w": "Bạn", "start": 0.0, "end": 0.16, "line": 0}]
```
One entry per **script** word when `--script` is used (punctuation stays attached: `"thoại,"`).

## beats.json (beats.py)
```json
{"tempo": 117.5, "duration": 16.0, "beats": [0.5, 1.0], "downbeats": [0.5, 2.5], "drops": [8.03], "onsets": [0.49]}
```

## timeline.json / timeline.js (timeline.py)
```json
{
  "fps": 30, "duration": 9.1,
  "lines": [...], "words": [...], "beats": {...},
  "scenes":   [{"id": "s0", "start": 0.0, "end": 2.33, "text": "…", "lines": [0], "cut": 0.0, "out": 2.7}],
  "captions": [{"start": 0.0, "end": 0.86, "text": "Bạn có biết não", "words": [{"w": "Bạn", "start": 0.0, "end": 0.16}]}]
}
```
- `start/end` = speech span; `cut/out` = when the scene is on screen (cut may sit on a beat just before speech).
- `timeline.js` is the same object as `window.TIMELINE = {…};` so a composition can `<script src="timeline.js">`
  without fetch (works from file:// and in the renderer).

## cues.json (mix.py)
```json
[[0.0, "whoosh", -12], [1.74, "pop", -16], [5.95, "/abs/path/hit.wav", -10]]
```
`[time, name-or-path, peak_db]`. A name resolves via `vs sfx path <name>` across `VS_SFX_DIRS`.
Take times from the timeline, e.g. the word that should "pop":
```bash
python3 -c "import json;t=json.load(open('timeline.json'));print([w for w in t['words'] if 'tám' in w['w']])"
```

## Reading the timeline in a composition
```js
const T = window.TIMELINE;
const tl = gsap.timeline({ paused: true });
T.scenes.forEach(s => tl.fromTo(`#${s.id}`, {opacity: 0}, {opacity: 1, duration: 0.3}, s.cut));
const hit = T.words.find(w => w.w.includes("tám"));   // anchor an effect to a spoken word
tl.from("#big-number", {scale: 0, duration: 0.25, ease: "back.out"}, hit.start);
T.beats.drops.forEach(d => tl.to("#flash", {opacity: 1, duration: 0.05}, d));
window.__timelines = {root: tl};
```
Build synchronously (no async/fetch/setTimeout), finite tweens only, root `data-duration` = `T.duration`.
