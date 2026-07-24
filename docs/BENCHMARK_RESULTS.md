# Реальные результаты проверок

Дата начала: 2026-07-21, timezone Europe/Moscow. Значения ниже получены командами
в этом репозитории; незапущенные проверки явно помечены.

## Компьютер

- Windows 11 x64 build 26200 (устаревшее поле `WindowsProductName` WMI вернуло
  Windows 10; `platform` и build соответствуют Windows 11).
- Acer Aspire A515-45.
- AMD Ryzen 5 5500U, 6 физических / 12 логических cores.
- RAM: 25 075 970 048 bytes (≈23,35 GiB).
- AMD Radeon Graphics; NVIDIA/CUDA отсутствуют; текущий probe не подтвердил
  доступный Vulkan runtime (`vulkan_available=false`).
- Realtek microphone: WASAPI device 12, 2 input channels.
- `sounddevice`/PortAudio подтвердил 8/16/22,05/32/44,1/48/96 кГц для default
  microphone.
- CTranslate2 4.8.1 CPU compute types: `float32`, `int8`, `int8_float32`.

## Toolchain

- Python 3.12.7 x64, Git 2.46.0.windows.1.
- PySide6 6.11.0, faster-whisper 1.2.1, CTranslate2 4.8.1,
  sounddevice 0.5.5, Nuitka 4.1.3.
- Nuitka использовал MSVC 14.3; Inno Setup 6.7.3 установлен в пользовательский
  профиль и реально собрал per-user installer.

## Автоматические и Windows-проверки

```text
scripts/lint.ps1
ruff format --check: 110 files formatted
ruff check: All checks passed
mypy: Success, 83 source files

scripts/test.ps1
162 passed, 5 opt-in acceptance tests deselected

$env:VOXTYPE_RUN_WINDOWS_SMOKE=1
pytest tests/windows/test_windows_native_smoke.py -v
4 passed: clipboard roundtrip/restore, реальная вставка Unicode в Notepad,
non-activating overlay и Locked-target FIFO при смене окна

$env:PYTHONPATH=(Resolve-Path src).Path
python tools/run_app_injection_matrix.py
4/4 passed: VS Code, Word, Chrome и Telegram Desktop
```

`--self-test --no-ui`, `--inference-smoke-test` и offscreen Qt startup прошли как
из Python, так и у standalone `dist/VoxType/VoxType.exe`. Inno Setup installer
прошёл тихую установку в отдельный user-каталог, self-test и удаление.
Повторный packaged inference при `HF_HUB_OFFLINE=1` и нерабочем HTTP(S) proxy
также завершился с exit code 0. Дополнительно `PYTHONHOME` и `PYTHONPATH` были
намеренно направлены в отсутствующий каталог: standalone не зависит от
установленного Python.

## Реальный ASR benchmark

Одинаковые собственные RU/EN WAV проверены `tools/evaluate_wer.py`, CPU INT8:

| Модель | Язык | Аудио | Время | RTF | Raw/normalized WER | Peak RSS |
|---|---:|---:|---:|---:|---:|---:|
| small | RU | 11,088 с | 4,418 с | 0,398 | 0% / 0% | 693,7 МБ |
| small | EN | 7,883 с | 4,017 с | 0,510 | 0% / 0% | 661,4 МБ |
| large-v3-turbo | RU | 11,088 с | 20,029 с | 1,806 | 12,5% / 0% | 1 948,5 МБ |
| large-v3-turbo | EN | 7,883 с | 19,355 с | 2,455 | 0% / 0% | 1 886,1 МБ |

На этом компьютере автоматически выбран FAST=`small`: он остаётся ниже целевого
RTF 0,7 на обоих файлах. `large-v3-turbo` оставлен ручным вариантом, потому что
его измеренный RTF > 1 гарантирует рост backlog. Набор короткий и не доказывает
превосходство над Windows Dictation; такого заявления нет.

## 60-минутный soak

Команда: `tools/run_soak_test.py --duration 3600 --sample-interval 10`.
Полный JSON: `soak-results/acceptance-final/soak-result.json`.

- wall clock: 3 600,937 с; samples: 361;
- captured/processed: 3 600 / 3 600 чанков, 57 600 000 frames;
- dropped frames: 0; FIFO: pass; duplicates: 0; retries: 37;
- forced segments: 149; queue high watermark: 94;
- backlog полностью опустошён; spool после завершения: 0 файлов;
- RSS min/max: 42 614 784 / 49 127 424 bytes; линейного роста нет;
- threads base/max/final: 15 / 18 / 12; retained leak отсутствует;
- synthetic RTF average/max: 0,360 / 1,502;
- synthetic latency average/max: 5,461 / 27,015 с;
- все 11 критериев: PASS.

Кратковременный пик RTF/latency создан намеренным backlog-сценарием; звук при нём
оставался в disk spool и затем был обработан по FIFO.

## Release

- `release/VoxType-0.1.0-win64-portable.zip`, SHA-256
  `7e487640c79116daec9ca0719f8e1f30b30a3708495039948bff6ff041feba82`,
  122 844 714 bytes.
- `release/VoxType-0.1.0-win64-setup.exe`, SHA-256
  `5aac7055247b625ff5a6cca2e81fe0acb47b66e83d253deccdd6fbc9c859a3d7`,
  80 537 264 bytes.
- Подпись отсутствует: сертификат code signing не предоставлен.

## Непроверенное

Не выполнялся запуск на физически другом чистом ПК. Также не выполнялась именно
живая многoабзацная диктовка голосом пользователя через микрофон в этой сессии:
реальные ASR WAV, microphone-capable pipeline, tail flush и настоящая вставка в
Windows-приложения проверены отдельно. Матрица Word/Chrome/VS Code/Telegram
пройдена реально; Telegram использовал только поле поиска и не отправлял сообщений.
