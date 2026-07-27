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
ruff format --check: 116 files formatted
ruff check: All checks passed
mypy: Success, 87 source files

scripts/test.ps1
176 passed, 5 opt-in acceptance tests deselected

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

## ASR benchmark

Русская эталонная расшифровка получена из другого Whisper-сервиса и не была
полностью вычитана человеком. Поэтому результаты ниже — только **pseudo-WER**,
а не объективный WER или подтверждённая точность.

| Проверка | Режим | pseudo-WER | CER | Wall RTF | Peak RSS |
|---|---|---:|---:|---:|---:|
| RU tuning, 8 файлов | Balanced | 29,469% | 22,492% | 0,636 | 2 240,6 МБ |
| RU held-out, 4 файла | Balanced | 25,552% | 17,432% | 0,557 | 2 210,0 МБ |
| RU все 12 файлов | Balanced | **28,402%** | 21,142% | 0,616 | 2 240,6 МБ |
| EN synthetic SAPI | Balanced | 1,852% | 2,120% | 1,196 | 649,3 МБ |
| RU файл 006 | Maximum Accuracy | 10,448% | 4,047% | 1,752 | 1 826,7 МБ |

Balanced работает быстрее реального времени на русском наборе. Maximum Accuracy
на проверенном русском файле дал около 10,45% pseudo-WER, но wall RTF около
1,752 означает накопление очереди при непрерывной речи на этом CPU. Английский
набор синтетический и короткий, поэтому его результат нельзя переносить на
произвольный живой микрофон.

Подтверждённые 90–95% качества русского распознавания не достигнуты; VoxType не
заявляет такую точность. Зафиксированная конфигурация использует raw audio,
чанки 15/25 секунд с overlap 0,8 секунды, явный RU/EN и не более 30 завершённых
слов контекста. Vulkan backend отклонён: на текущем AMD-драйвере он вернул
повреждённый многоязычный результат. Аудио не отправляется в облачный STT.

## 60-минутный soak

Команда: `tools/run_soak_test.py --duration 3600 --sample-interval 10`.
Полный JSON: `build/private-eval/final-soak/report.json` (не входит в release).

- wall clock: 3 600,906 с; samples: 361;
- captured/processed: 3 600 / 3 600 чанков, 57 600 000 frames;
- dropped frames: 0; FIFO: pass; duplicates: 0; retries: 37;
- cascade retries: 8; forced segments: 75; queue high watermark: 110;
- backlog полностью опустошён; spool после завершения: 0 файлов;
- RSS min/max: 40 714 240 / 51 949 568 bytes; линейного роста нет;
- threads base/max/final: 15 / 18 / 12; retained leak отсутствует;
- synthetic RTF average/max: 0,364 / 1,501;
- synthetic latency average/p50/p95/max: 5,481 / 1,078 / 37,5 / 37,5 с;
- все 11 критериев: PASS.

Кратковременный пик RTF/latency создан намеренным backlog-сценарием; звук при нём
оставался в disk spool и затем был обработан по FIFO.

## Release

- `release/VoxType-0.2.0-win64-portable.zip`, SHA-256
  `dbede927e10e5637c024e3a5f3bd18fa7e166fd767d047738acf71ff22d734d4`,
  122 855 161 bytes.
- `release/VoxType-0.2.0-win64-setup.exe`, SHA-256
  `7c3a2172917a426f0e20e2d420d6be2c9e8c7ab078a278a396149561fa51271b`,
  80 544 819 bytes.
- Установлен `%LOCALAPPDATA%\Programs\VoxType\VoxType.exe`, версия
  `0.2.0.0`; installed self-test, inference smoke и Qt UI smoke: exit code 0.
- Короткая desktop-приёмка установленной версии реально зарегистрировала
  `Ctrl+Alt+Space`, открыла микрофон в RU/Balance, обработала хвост до `idle`,
  переключила язык `Ctrl+Alt+L` на EN и штатно закрыла worker/audio runtime.
- Подпись отсутствует: сертификат code signing не предоставлен.

## Непроверенное

Не выполнялся запуск на физически другом чистом ПК. Также не выполнялась
контролируемая 1–2-минутная RU и 30–60-секундная EN диктовка голосом пользователя:
в автоматизированной сессии нет источника живой речи. Реальный microphone start,
language switch и tail flush проверены, но их нельзя выдавать за проверку качества
текста. Матрица Word/Chrome/VS Code/Telegram пройдена 4/4 ранее; Telegram
использовал только поле поиска и не отправлял сообщений.
