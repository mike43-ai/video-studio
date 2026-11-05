# Audio mixing guide

## Targets
| Where | Integrated | True peak |
|---|---|---|
| TikTok / Reels / Shorts / YouTube | −14 LUFS (default) | −1.5 dBTP |
| Podcast / voice-only | −16 LUFS | −1.5 dBTP |
| Ads / broadcast-ish | −23 LUFS (EBU R128) | −1 dBTP |

`vs mix --target -16` etc. Always confirm with `vs deliver` (measures the final file).

## Levels before loudnorm
- Voice is the reference (TTS peaks ≈ −1…−6 dBFS).
- Music bed `--music-db -16` (default). Busy/loud track → −20; quiet ambient → −12.
- Ducking `--duck 8` (ratio). Music still masks words → raise to 12–20. Pumping/obvious → lower to 4–6.
- SFX peak_db: impacts −9…−12 · whoosh/riser −12…−14 · pop/click/tick −14…−20 · ding/chime −14…−16.
  Never louder than the voice; one cue per idea, not per word.

## Timing cues
- Whoosh: start ~0.15 s BEFORE the cut so its peak lands on the cut.
- Riser: end exactly on the drop / title.
- Pop/ding: on the word's `start`.
- Leave 0.3 s+ between cues; dense cues read as noise.

## Fixing a bad mix
| Symptom | Fix |
|---|---|
| Words hard to hear | `--duck 14` or `--music-db -20` |
| Music vanishes under voice | `--duck 5` |
| Ending cut abruptly | `--fade 2.5`, or `vs timeline --tail 2` then re-mix |
| deliver warns LUFS off | input had long silence → trim, or check `--duration` matches timeline |
| A/V length differ | mix with `--timeline timeline.json` so both use the same duration |
| Clicks at line joins | raise `--line-gap` in tts or re-voice that line |

## Music sources (license first)
Own/generated music, YouTube Audio Library, Pixabay Music, or a licensed track. Do not use a commercial song
unless the user confirms rights.
