# Seven Wonders audio

`wav/` contains the 32 selected mono, 48 kHz, 16-bit PCM sound effects. These are
original procedural effects, not sampled recordings. The game manifest discovers
`wav/` separately from `out/png`, so a clip's guest asset name is its basename,
for example `hand_move.wav`. Generator scripts and this document are not delivered
as game assets.

Regenerate all effects with Python 3 and FFmpeg:

```sh
python3 audio/generate.py
```

Run from the submodule root. To regenerate selected sounds only:

```sh
python3 audio/generate.py coins_gain coins_spend military_clash
```

Check that regenerated PCM matches the saved WAVs without modifying them:

```sh
python3 audio/generate.py --check
```

The check removes its temporary directory even if generation or comparison fails.
PCM reproduction was verified with FFmpeg 7.1.5; codec/filter changes between
FFmpeg versions can change the output. No Python packages or network access are
required. Normal generation overwrites only the selected files in `audio/wav/`.

`hand_move.wav` is shared by passing and dealing. `extra_play_available.wav`
is shared by ordinary decisions and extra plays. The scoreboard opens with
`scoreboard_open.wav`, a short snare hit, followed by category reveal cues.
`all_ready.wav` replaces an overlapping `ready.wav`.
