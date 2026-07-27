# VoxType accuracy evaluation protocol

## Metric status

The supplied Russian references were produced by another Whisper-based bot and
have not been reviewed word-by-word by a human. Results against these files are
reported as **pseudo-WER** (WER against supplied reference), never as absolute
speech-recognition accuracy.

The supplied text is evaluation-only. It must not be used as an initial prompt,
production vocabulary, correction table, phrase replacement source, or model
memory.

Normalized scoring:

- Unicode-aware case folding;
- `ё` is folded to `е`;
- punctuation and whitespace are ignored;
- substitutions, deletions, and insertions use word-level Levenshtein
  alignment;
- CER uses the same normalized text without spaces.

## Fixed split

The immutable split is stored in
`tools/private_eval_split.json`.

- Tuning: 001, 004, 005, 006, 008, 010, 011, 012.
- Held-out: 002, 003, 007, 009.

Parameters and cascade thresholds may be selected only on the tuning set.
Held-out results are inspected only after a configuration is fixed. File 009
may be run during screening as an observation-only held-out case; its result
must not influence selection.

## Reproducibility

`tools/run_accuracy_matrix.py` hashes:

- every audio input;
- every reference input;
- the split file, VAD model, optional vocabulary, and managed model manifest;
- all inference and pipeline options;
- the experiment-runner protocol version.

A completed config hash is reused unless `--force` is explicitly supplied.
Private outputs are written under `build/private-eval/experiments/` and must
not be committed. Summary files are:

- `build/private-eval/experiments/summary.csv`;
- `build/private-eval/experiments/summary.json`.

All release claims must distinguish tuning pseudo-WER, held-out pseudo-WER,
and human-reviewed WER. The latter remains unavailable until the references
are manually corrected while listening to the source audio.

## Fixed production candidate

The configuration frozen before the final held-out run is:

- faster-whisper/CTranslate2 CPU;
- primary `small`, INT8, beam 1;
- at most one `large-v3-turbo`, INT8, beam 1 retry when the guard marks the
  primary result suspicious;
- explicit `language=ru`;
- raw preprocessing;
- Silero threshold 0.45, end silence 650 ms, pre/post-roll 400/300 ms;
- preferred/maximum segment 15/25 seconds and 0.8-second overlap;
- at most 30 confirmed context words, only while continuing an unfinished
  same-language phrase; reset after terminal punctuation, an uncertain
  fragment, a pause over 8 seconds, or a language switch;
- overlap merge and formatting enabled.

No held-out result was used to choose these values.
