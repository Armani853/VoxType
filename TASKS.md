# Задачи VoxType

Статусы: `[ ]` ожидает, `[-]` выполняется, `[x]` выполнено и проверено,
`[!]` ограничение зафиксировано.

## 1. Основа и окружение

- [-] Создать PLAN.md и TASKS.md.
- [ ] Инициализировать Git и делать логические коммиты по этапам.
- [ ] Собрать реальный hardware/toolchain/audio probe.
- [ ] Проверить официальные документы ключевых библиотек.
- [ ] Создать `pyproject.toml`, lock и структуру пакета.
- [ ] Зафиксировать решения в `docs/DECISIONS.md`.

## 2. Runtime

- [ ] Типизированные модели, события, исключения и строгая state machine.
- [ ] Bounded аудиоочередь, ring buffer, disk spool без потери порядка.
- [ ] Устройства, capture, resampling, уровни и preprocessing profiles.
- [ ] VAD с pre/post-roll и forced segmentation с overlap.
- [ ] ASRBackend, faster-whisper, профили, ModelManager и benchmark/RTF.
- [ ] TranscriptMerger, formatter, команды, vocabulary, hallucination guard.
- [ ] Session journal/recovery/retention с атомарными записями.
- [ ] Settings schema/migration/atomic repository.
- [ ] Metrics, health и privacy-safe rotating logs.
- [ ] ApplicationController и корректное завершение всех worker-ресурсов.

## 3. Windows и UI

- [ ] Foreground/target policy и защита собственного/elevated окна.
- [ ] Clipboard snapshot/restore, Unicode SendInput и injection fallback.
- [ ] RegisterHotKey, startup и power-event handling.
- [ ] Современное главное окно RU, meter, profile/language/status/history.
- [ ] Неактивирующий overlay, tray и сохранение позиции.
- [ ] Onboarding, settings, history и diagnostics.

## 4. Проверки

- [ ] Unit: state, buffers/queue/spool, VAD/segmenter/overlap/merger.
- [ ] Unit: formatter/commands/vocabulary/guard/settings/sessions/retention.
- [ ] Unit: target/clipboard/spacing/language/metrics.
- [ ] Integration: synthetic WAV source, backlog, worker failure, spool recovery.
- [ ] Windows smoke: реальное содержимое Notepad, Unicode, clipboard, target.
- [ ] `ruff format --check`, `ruff check`, `mypy`, `pytest` проходят.
- [ ] Приложение реально запускается и корректно завершается.
- [ ] Полный 60-минутный soak, метрики сохранены без выдуманных значений.
- [ ] WER tool и инструкция личного набора.

## 5. Выпуск

- [ ] README_RU и полный комплект docs.
- [ ] Bootstrap/run/test/lint/build scripts.
- [ ] Nuitka portable one-folder без установленного Python и smoke EXE.
- [ ] Inno Setup installer либо проверенный воспроизводимый script.
- [ ] SHA-256 release-артефактов.
- [ ] Независимый финальный review и устранение замечаний.
- [ ] Повтор всех обязательных проверок и финальный Git commit.
