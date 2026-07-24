# План тестирования

## Unit

- state machine и конкурентный переход;
- ring buffer, bounded callback ingress, lossless RAM/disk queue и corruption;
- preprocessing/levels/resampling;
- VAD pre/post-roll, natural/forced cuts, overlap и flush;
- ASR profiles/model lifecycle/guard;
- overlap merger (RU/EN, punctuation, fuzzy mismatch, real repeats, sequence);
- formatter, commands, vocabulary;
- settings migration/corrupt backup/atomic replace;
- SQLite journal/recovery/retention;
- target policy, clipboard sequence/restore, UTF-16 SendInput, spacing/hotkeys;
- metrics bounds/RTF.

Unit не требует реального микрофона или модели.

## Integration

Synthetic/test WAV подаётся через тот же dispatcher. Fake deterministic backend
проверяет порядок, RU/EN switch, stop while processing, worker failure, backlog и
spool recovery. Реальный faster-whisper test помечен отдельно и запускается после
наличия локальной модели.

## Windows desktop smoke

Opt-in test открывает Notepad, сохраняет clipboard, фиксирует HWND, вставляет
кириллицу/English/punctuation/newline, читает текст control/UIA либо сохраняет
видимый результат и восстанавливает clipboard. Дополнительно проверяются смена
foreground и non-activating overlay. Возврат SendInput сам по себе не считается
успехом.

Расширенная интерактивная матрица запускается командой
`python tools/run_app_injection_matrix.py`. Она использует временные документы и
профили, проверяет фактическое поле/файл Word, Chrome, VS Code и Telegram, затем
закрывает только созданные окна. В Telegram используется поиск, поэтому сообщение
не отправляется.

## Soak

`tools/run_soak_test.py --duration 3600` работает не менее 3600 секунд wall clock.
Каждые 10 секунд сохраняет RAM/CPU/threads/queue/spool. Проверяются zero drops,
FIFO sequence, queue drain, bounded memory/thread trend, preprocessing, VAD,
forced segmentation, ASR protocol/guard, merger, valid journal и cleanup.
Ускоренный CI-вариант не заменяет обязательный полный run.

## WER

`tools/evaluate_wer.py AUDIO.wav REFERENCE.txt --language ru --profile balanced`
сохраняет raw/normalized comparison, S/D/I, time и RTF. `--keep-yo` и
`--normalize-numbers` независимы.

Личный набор: запишите 10–20 собственных фраз в тихом/шумном режиме, обычную,
тихую, громкую, быструю речь, технические термины, RU/EN, mixed names и длинный
монолог. Сравнивайте VoxType и Windows на одном WAV/reference; без такого запуска
заявление о превосходстве запрещено.
