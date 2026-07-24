# Задачи VoxType

Статусы: `[ ]` ожидает, `[-]` выполняется, `[x]` выполнено и проверено,
`[!]` ограничение зафиксировано.

## 1. Основа и окружение

- [x] Создать PLAN.md и TASKS.md.
- [x] Инициализировать Git и делать логические коммиты по этапам.
- [x] Собрать реальный hardware/toolchain/audio probe.
- [x] Проверить официальные документы ключевых библиотек.
- [x] Создать `pyproject.toml`, lock и структуру пакета.
- [x] Зафиксировать решения в `docs/DECISIONS.md`.

## 2. Runtime

- [x] Типизированные модели, события, исключения и строгая state machine.
- [x] Bounded аудиоочередь, ring buffer, disk spool без потери порядка.
- [x] Устройства, capture, resampling, уровни и preprocessing profiles.
- [x] VAD с pre/post-roll и forced segmentation с overlap.
- [x] ASRBackend, faster-whisper, профили, ModelManager и benchmark/RTF.
- [x] TranscriptMerger, formatter, команды, vocabulary, hallucination guard.
- [x] Session journal/recovery/retention с атомарными записями.
- [x] Settings schema/migration/atomic repository.
- [x] Metrics, health и privacy-safe rotating logs.
- [x] ApplicationController и корректное завершение worker-ресурсов.

## 3. Windows и UI

- [x] Foreground/target policy и защита собственного/elevated окна.
- [x] Clipboard snapshot/restore, Unicode SendInput и injection fallback.
- [x] RegisterHotKey, startup и power-event handling.
- [x] Современное главное окно RU, meter, profile/language/status/history.
- [x] Неактивирующий overlay, tray и сохранение позиции.
- [x] Onboarding, settings, history и diagnostics.

## 4. Проверки

- [x] Unit: state, buffers/queue/spool, VAD/segmenter/overlap/merger.
- [x] Unit: formatter/commands/vocabulary/guard/settings/sessions/retention.
- [x] Unit: target/clipboard/spacing/language/metrics.
- [x] Integration: synthetic WAV source, backlog, worker failure, spool recovery.
- [x] Windows smoke: реальное содержимое Notepad, Unicode, clipboard, target.
- [x] `ruff format --check`, `ruff check`, `mypy`, `pytest` проходят.
- [x] Приложение реально запускается и корректно завершается.
- [x] Полный 60-минутный soak, метрики сохранены без выдуманных значений.
- [x] WER tool и инструкция личного набора.

## 5. Выпуск

- [x] README_RU и полный комплект docs.
- [x] Bootstrap/run/test/lint/build scripts.
- [x] Nuitka portable one-folder и packaged self/inference/UI smoke EXE.
- [x] Inno Setup installer, install/self-test/uninstall smoke.
- [x] SHA-256 release-артефактов.
- [x] Независимый review и устранение P0/P1 замечаний.
- [x] Повтор всех обязательных проверок и финальный Git commit.

## Зафиксированные ограничения

- [!] Clean-machine запуск на другом физическом ПК не проверен.
- [x] Реальная матрица Word/Telegram/VS Code/browser выполнена 4/4.
- [!] Живая многoабзацная диктовка голосом пользователя через микрофон не
  выполнялась; ASR WAV, microphone pipeline и реальные target-приложения
  проверены раздельно.
- [!] Сборка не подписана: сертификат code signing отсутствует.
