# План реализации VoxType

## Цель

Создать локальное Windows 11 x64 приложение для длительной русской и английской
диктовки с ограниченными очередями, резервным дисковым spool, безопасной вставкой
в зафиксированное окно, восстановлением сессий и воспроизводимой one-folder
сборкой без обязательного GPU.

## Принципы приёмки

- Ни одна возможность не считается проверенной без выполненной команды или
  реального интерактивного smoke-теста.
- Аудиоколбэк только копирует PCM в ограниченную очередь; тяжёлая работа идёт вне
  callback.
- При backlog звук последовательно уходит в локальный spool и не отбрасывается.
- В сторонние приложения вставляется только committed-текст; при сомнительной
  цели или ошибке текст остаётся в журнале/очереди.
- Модель и исходное аудио не включаются в технические логи и не отправляются в
  сеть. Сеть используется ModelManager только для явной загрузки модели.
- Основной путь — CPU; ускорители допускаются только после успешного benchmark.

## Этапы

1. Обследование Windows, Python, CPU/RAM/GPU, аудиоустройств, toolchain и Git.
2. Проверка официальной документации и фиксация совместимых зависимостей/lock.
3. Модульная реализация core, audio, VAD, ASR, transcript, Windows, sessions,
   settings, monitoring и UI.
4. Интеграция через ApplicationController со строгой state machine и корректным
   завершением worker-потоков.
5. Детерминированные unit/integration тесты, WAV-source, WER и diagnostics tools.
6. Реальные Windows smoke-тесты (включая содержимое Notepad и clipboard), запуск
   приложения и проверка offline-пути при уже установленной модели.
7. Полный 60-минутный soak с выборкой RAM/CPU/threads/queue каждые 10 секунд.
8. `ruff format --check`, `ruff check`, `mypy`, `pytest`, исправления и повтор.
9. Nuitka one-folder portable, smoke собранного EXE, Inno Setup installer либо
   воспроизводимый installer-script при отсутствии компилятора.
10. Независимый финальный review, устранение замечаний, повтор проверок и Git
    release-коммит.

## Параллельная работа

- Audio/ASR: capture, preprocessing, spool, VAD/segmenter, backend/model profiles.
- Windows: foreground/target policy, clipboard, SendInput, hotkeys, startup.
- UI: main window, overlay, tray, onboarding/settings/history/diagnostics.
- Главный поток: архитектурный каркас, state/controller, sessions/settings,
  monitoring, интеграция, тесты, packaging, документация и проверка результатов.

## Риски и безопасные решения

- Большие Whisper-модели могут не достигать RTF < 1 на целевом CPU: профиль
  выбирается только benchmark, FAST остаётся доступным fallback.
- Clipboard нельзя восстановить абсолютно для форматов с отложенным рендерингом:
  реализуется ограниченное резервное копирование доступных форматов и Unicode
  SendInput/safe-copy fallback; ограничение явно документируется.
- UIPI запрещает ввод в elevated-окно: никакого обхода, текст сохраняется локально.
- Интерактивные проверки зависят от доступности desktop session, микрофона,
  модели и приложений; непроверенное будет отмечено как непроверенное.

## Артефакты

- Исходники: `src/voxtype/`.
- Тесты: `tests/`.
- Инструменты: `tools/`.
- Сценарии: `scripts/`.
- Документация: `docs/`, `README_RU.md`.
- Portable: `dist/VoxType/` и release ZIP/checksum.
- Installer: `dist/installer/` при доступном Inno Setup.

