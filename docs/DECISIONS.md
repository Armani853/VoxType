# Технические решения

## D-001 — Python 3.12 и PySide6 Widgets

Среда содержит Python 3.12.7 x64. Используется PySide6 6.11.0 и QWidget UI:
официальная документация Qt поддерживает Python 3.10+ и рекомендует виртуальное
окружение. Qt wheels включают нужные Qt binaries. Источник:
<https://doc.qt.io/qtforpython-6/gettingstarted.html>.

## D-002 — faster-whisper/CTranslate2 CPU INT8

Основной backend — faster-whisper 1.2.1 / CTranslate2 4.8.1. На этом компьютере
runtime реально сообщил CPU compute types `int8`, `int8_float32`, `float32` и
отсутствие CUDA. CTranslate2 рекомендует INT8 для CPU; конкретная модель
выбирается только по RTF. Источники:
<https://github.com/SYSTRAN/faster-whisper/blob/master/README.md>,
<https://opennmt.net/CTranslate2/quantization.html>,
<https://opennmt.net/CTranslate2/performance.html>.

Названия по умолчанию: FAST=`small`, кандидат BALANCED=`turbo`,
MAXIMUM_ACCURACY=`large-v3`. Если `turbo` не выдерживает RTF с запасом,
автовыбор обязан оставить более быструю реально измеренную модель.

На Ryzen 5 5500U измерено: `small` RTF 0,398 RU / 0,510 EN, а
`large-v3-turbo` 1,806 RU / 2,455 EN. Поэтому first-run default изменён на
FAST=`small`; BALANCED доступен вручную после предупреждения/benchmark.

## D-003 — Silero VAD через ONNX и energy fallback

Silero VAD используется на 16 кГц и обрабатывает фиксированные окна, а
AdaptiveEnergyVAD позволяет UI и тестовому pipeline работать до установки/при
повреждении ONNX model. Fallback явно показывается в диагностике и не выдаётся за
Silero. Официальный Silero VAD поддерживает 8/16 кГц и streaming:
<https://github.com/snakers4/silero-vad>.

## D-004 — LosslessAudioQueue и последовательный disk spool

PortAudio callback копирует float32 PCM в bounded ingress. Dispatcher выполняет
resampling/preprocessing и помещает блоки в bounded RAM queue. При достижении
порога блоки нумерованно записываются в spool; consumer подтверждает удаление
только после обработки. Порядок определяется sequence, а не временем завершения
I/O. Размер spool ограничен свободным диском/конфигурацией, чтобы ошибка диска не
стала бесконтрольным заполнением.

## D-005 — SQLite WAL для истории и recovery

SQLite выбран вместо одного JSONL: транзакции атомарно связывают уникальный
sequence event и обновление committed text; WAL сохраняет уже подтверждённое при
сбое. Незавершённая запись имеет `finished_at IS NULL`. Технический лог никогда не
содержит полный текст; сам текст находится только в session database при
включённой истории.

## D-006 — committed-only insertion и Locked target

Provisional text виден только в VoxType. Перед вставкой фрагмент проходит ASR,
hallucination guard, overlap merge и journal. По умолчанию HWND/process identity
фиксируются на старте. Если target неактивен/закрыт/выше по integrity, FIFO
остаётся локально; safe-copy не пытается обходить UIPI.

Clipboard paste сохраняет доступные eager formats и sequence number. Если другое
приложение изменило clipboard после нашей вставки, VoxType не перезаписывает его
устаревшим snapshot. Это осознанный выбор в пользу сохранности новых данных.

## D-007 — one-folder Nuitka

Используется `--mode=standalone` и PySide6 plugin. One-folder выбран из-за
быстрого старта, прозрачных DLL и меньшего числа антивирусных ложных срабатываний.
Официальный manual подтверждает, что только standalone/onefile/app не требуют
Python на целевой машине:
<https://nuitka.net/user-documentation/user-manual.html>.

## D-008 — local-first сеть

Никаких входящих портов, telemetry и error upload. Сеть разрешена ModelManager
только для явной загрузки model snapshot во временный каталог. Готовая модель
открывается `local_files_only`; offline диктовка не обращается к сети.

## D-009 — реальное окружение 2026-07-21

- Acer Aspire A515-45, AMD Ryzen 5 5500U, 6/12 cores.
- RAM 25 075 970 048 bytes (≈23,35 GiB).
- AMD Radeon Graphics; CUDA/NVIDIA отсутствуют; Vulkan runtime текущим probe не
  подтверждён.
- Realtek microphone доступен через Windows WASAPI.
- Проверенные capture rates: 8/16/22,05/32/44,1/48/96 кГц.
- GCC UCRT64 есть; MSVC, Nuitka и Inno Setup изначально отсутствовали.
- Nuitka 4.1.3 установлен в `.venv`; Inno Setup проверяется build-script.
