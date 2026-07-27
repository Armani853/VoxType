# Матрица выполнения исходного ТЗ VoxType

Дата финальной сверки: 24 июля 2026 года.

Обозначения:

- `[x]` — реализовано и подтверждено кодом, автоматическим тестом, реальным
  Windows smoke-тестом или собранным артефактом;
- `[!]` — реализация готова, но указанный внешний ручной тест нельзя честно
  подтвердить в текущей среде;
- опциональные пункты считаются выполненными, если принято и документировано
  разрешённое ТЗ стабильное решение.

Итог: все реализуемые требования ТЗ закрыты. Не подменены автоматикой только две
внешние проверки: запуск на другом физически чистом ПК и живая многoабзацная
диктовка голосом пользователя через реальный микрофон.

## 1. Порядок выполнения

- [x] Окружение, CPU/RAM/GPU, Windows, toolchain и аудиоустройства обследованы.
- [x] Официальная документация и совместимость ключевых зависимостей проверены.
- [x] `PLAN.md` и `TASKS.md` созданы до реализации.
- [x] Модульная структура проекта создана.
- [x] Рабочее desktop-приложение реализовано и реально запускалось.
- [x] Unit, integration, packaging, Windows и soak-тесты написаны и выполнены.
- [x] Найденные дефекты исправлены, включая переполнение capture-очереди,
  overlap merge, зависание inference, clipboard и фокус приложений.
- [x] Полный 60-минутный wall-clock soak выполнен.
- [x] Portable one-folder сборка реально собрана.
- [x] Per-user Inno Setup installer реально собран.
- [x] Русская инструкция пользователя подготовлена.
- [x] Реальные результаты тестов и benchmark зафиксированы.
- [x] Неоднозначные решения записаны в `docs/DECISIONS.md`.
- [x] В production-коде нет заглушек, TODO, `pass` и `NotImplementedError`.
- [x] Использован Git с логическими промежуточными коммитами и финальным
  release-коммитом.
- [x] Выполнены параллельные исследования Audio/ASR, Windows, UI, тестов/сборки
  и независимый финальный review; результаты перепроверены главным агентом.

## 2. Цель продукта

- [x] Локальная диктовка в активную текстовую каретку реализована.
- [x] Реальная вставка проверена в Notepad, Word, Chrome, VS Code и Telegram.
- [x] Политики позволяют работать с ChatGPT/email/обычными Windows-полями,
  если приложение принимает clipboard paste или Unicode `SendInput`.
- [x] Одна глобальная клавиша запускает и останавливает диктовку.
- [x] Есть явные RU и EN режимы.
- [x] UI и overlay показывают микрофон, уровень и распознавание.
- [x] Committed-текст вставляется непосредственно в целевое поле.
- [x] Нет автоматической остановки или искусственного лимита сессии.
- [x] Уже распознанный текст журналируется до вставки.
- [x] RAM, очереди, потоки и аудиобуферы ограничены.
- [x] Длинные аудиофрагменты режутся на forced chunks с overlap.
- [x] Повторная горячая клавиша переводит сессию в корректное завершение.
- [x] После stop обрабатывается и вставляется оставшийся хвост.
- [x] Минимум 60 минут подтверждён полным soak-тестом.
- [x] После установки модели ASR работает полностью локально.
- [x] Платные/cloud STT API отсутствуют.
- [x] Аудио, текст, статистика и логи не отправляются на сторонние серверы.

## 3. Целевое окружение

- [x] Основная цель — Windows 11 x64.
- [x] Проверен Acer Aspire A515-45 с Ryzen 5 5500U и 24 ГБ RAM.
- [x] CPU-only является основным и полностью рабочим путём.
- [x] CUDA не требуется; на проверенной системе она отсутствует.
- [x] AMD/Intel/NVIDIA обнаруживаются диагностикой без привязки к CPU.
- [x] Определяются модель CPU, физические/логические ядра и RAM.
- [x] Определяются GPU, CUDA и Vulkan.
- [x] Перечисляются устройства записи и поддерживаемые sample rates.
- [x] Диагностика отображается пользователю и попадает в privacy-safe log.

## 4. Технологический стек

- [x] Python 3.12 x64 и PySide6.
- [x] faster-whisper 1.2.1 / CTranslate2 4.8.1.
- [x] Silero VAD ONNX с закреплённой SHA-256.
- [x] sounddevice/PortAudio и Windows WASAPI.
- [x] NumPy, ctypes, platformdirs, dataclasses, logging, psutil.
- [x] pytest, pytest-qt, ruff, mypy и Nuitka.
- [x] Рабочие версии зависимостей закреплены в `pyproject.toml`.
- [x] Создан воспроизводимый `requirements.lock`.
- [x] Electron, web UI и локальный HTTP-сервер не используются.

## 5. Архитектура

- [x] `ApplicationController`.
- [x] `AudioCaptureService`.
- [x] `AudioPreprocessor`.
- [x] `VoiceActivityDetector`.
- [x] `StreamingSegmenter`.
- [x] Отдельный serial transcription worker.
- [x] `TranscriptMerger`.
- [x] `HallucinationGuard`.
- [x] `TextInjectionService`.
- [x] `TargetWindowManager`.
- [x] `GlobalHotkeyManager`.
- [x] `SessionJournal`.
- [x] `ModelManager`.
- [x] `PerformanceMonitor`.
- [x] `SettingsRepository`.
- [x] `TrayController`.
- [x] `MainWindow`.
- [x] `DictationOverlay`.
- [x] Логика разделена по пакетам, UI не выполняет ASR.
- [x] Callback не выполняет VAD, ASR, UI или filesystem I/O.
- [x] Границы компонентов типизированы; тяжёлые сервисы внедряются через factory/DI.

## 6. Состояния

- [x] Реализованы STARTING, MODEL_LOADING, IDLE, LISTENING,
  SPEECH_DETECTED, TRANSCRIBING, PAUSED_TARGET_LOST, FINISHING,
  ERROR_RECOVERABLE, ERROR_FATAL и SHUTTING_DOWN.
- [x] Добавлено отдельное непротиворечивое состояние пользовательской паузы.
- [x] Таблица разрешённых переходов не допускает противоречивых состояний.
- [x] Все переходы логируются.
- [x] Исключения worker переводят controller в явное error-состояние.

## 7. Захват аудио

- [x] Mono PCM и ASR rate 16 кГц.
- [x] Качественный stateful resampling при другой частоте микрофона.
- [x] Callback только копирует блоки в bounded FIFO.
- [x] Основная и аварийная callback-очереди ограничены.
- [x] Overflow не скрывается и не удаляет звук.
- [x] При backlog включается последовательный disk spool.
- [x] Spool обрабатывается строго FIFO и очищается после подтверждения.
- [x] UI получает предупреждение «обработка немного отстаёт».
- [x] Кратковременное замедление ASR переживается без потери.
- [x] Счётчики captured/dropped/queued/processed/high-watermark/overflow/RMS/clipping
  реализованы.
- [x] В 60-минутном тесте `dropped_frames = 0`.
- [x] Windows default input заново выбирается PortAudio при каждой сессии, если
  пользователь не закрепил конкретное устройство.

## 8. Предварительная обработка

- [x] Удаление DC offset и speech-safe high-pass.
- [x] Измерение noise floor и clipping.
- [x] Ограниченная адаптивная нормализация.
- [x] Soft limiter.
- [x] Отключаемое умеренное noise suppression.
- [x] Тишина/стационарный шум не усиливаются до максимума.
- [x] Gain ограничен и не создаёт clipping.
- [x] Профили Raw, Normal и Noisy room.
- [x] Normal является проверенным профилем по умолчанию.
- [x] Onboarding показывает «тихо / норма / клиппинг / неправильный микрофон».
- [x] Системная громкость микрофона не меняется.

## 9. VAD и сегментация

- [x] Silero VAD определяет речевые области.
- [x] Pre-roll 400 мс, post-roll 300 мс и speech padding.
- [x] Есть тихий/шумный профиль и регулируемый порог.
- [x] Minimum speech, end silence и защита от короткой паузы.
- [x] Frame 32 мс, threshold 0.45, minimum speech 180 мс, end silence 650 мс.
- [x] Preferred chunk 15 с, maximum 25 с.
- [x] Overlap 0.8 с.
- [x] Forced cut предпочитает ближайшую короткую паузу.
- [x] При отсутствии паузы выполняется безопасный cut с overlap.
- [x] Whisper никогда не получает часовой массив.
- [x] Все VAD/segment buffers ограничены.

## 10. Модели

- [x] Основной backend — faster-whisper через общий `ASRBackend`.
- [x] FAST=`small/int8`.
- [x] BALANCED=`small/int8` с выборочным retry на `large-v3-turbo/int8`.
- [x] MAXIMUM_ACCURACY=`large-v3-turbo/int8`.
- [x] Язык задаётся явно и word timestamps включены.
- [x] RTF измеряется локальным benchmark.
- [x] Цель RTF ≤ 0.7 применяется при выборе профиля.
- [x] На этом CPU автоматически выбран FAST, поскольку turbo имеет RTF > 1.
- [x] Ручной выбор тяжёлой модели предупреждает о backlog.
- [x] Опциональный Vulkan backend не включён: официальный build подтвердил
  ускорение, но дал повреждённый multilingual-текст на AMD-драйвере.
- [x] `ModelManager` показывает progress и позволяет повторить после ошибки.
- [x] Загрузка идёт во staging/temporary и публикуется атомарно.
- [x] Проверяются структура, revision, размер/SHA-256, когда они доступны.
- [x] Проверяется свободное место.
- [x] Готовая модель повторно не скачивается.
- [x] Неиспользуемую модель можно удалить в настройках.

## 11. RU/EN

- [x] Надёжные явные `language="ru"` и `language="en"`.
- [x] Основной путь не использует auto-detect.
- [x] RU/EN всегда виден в overlay.
- [x] Есть глобальная клавиша переключения языка.
- [x] При переключении текущий chunk завершается старым языком.
- [x] Последующие chunks идут новым языком без потери аудио.
- [x] Два языка не смешиваются в одном inference request.

## 12. Параметры Whisper и словарь

- [x] `task=transcribe`, explicit language, temperature 0.
- [x] Word timestamps и внешний VAD.
- [x] `condition_on_previous_text=False` защищает от циклов.
- [x] Контекст ограничен, очищен и содержит только committed-текст.
- [x] Custom vocabulary передаётся как bounded initial prompt.
- [x] Beam/best-of зависят от профиля.
- [x] При backlog beam/best-of временно снижаются до 1.
- [x] Настроены no-speech/log-probability/compression thresholds.
- [x] Пользовательский словарь принимает имена, компании, термины,
  библиотеки и аббревиатуры.
- [x] Словарь является мягкой подсказкой, а не заменой текста.

## 13. Provisional и final

- [x] Provisional и committed transcript разделены.
- [x] Provisional показывается только в VoxType.
- [x] В стороннее приложение уходит только committed-текст.
- [x] Commit выполняется после ASR, overlap merge, duplicate и guard checks.
- [x] Forced chunks регулярно дают текст при непрерывной речи.
- [x] Реакция UI/hotkey/capture не блокируется моделью.
- [x] Фактические latency и RTF измеряются и показываются в диагностике.
- [x] Stop выполняет bounded flush хвоста; при backlog честно показывает FINISHING.

## 14. TranscriptMerger

- [x] Используются абсолютные и word timestamps.
- [x] Сравниваются нормализованные suffix/prefix и longest overlap.
- [x] Учитываются регистр, пунктуация и временная граница.
- [x] Реальные повторы не удаляются только из-за одинакового текста.
- [x] Поддержаны RU, EN, числа, punctuation, hyphen и abbreviations.
- [x] Проверено точное повторение границы.
- [x] Проверено отличие только пунктуацией.
- [x] Проверено одно различающееся слово.
- [x] Проверен реальный повтор предложения.
- [x] Проверены два одинаковых коротких слова подряд.
- [x] Проверено смешение кириллицы и латиницы.
- [x] Проверен пустой chunk.
- [x] Проверен сбой timestamps.
- [x] Проверена длинная последовательность из 500 фрагментов.
- [x] Проверен overlap последних 5–15 слов.

## 15. HallucinationGuard

- [x] Детектируется текст на тишине.
- [x] Проверяются probability и compression ratio.
- [x] Проверяются повтор предложения, n-gram и десятки одинаковых слов.
- [x] Проверяется резкая смена языка.
- [x] Проверяются subtitle junk phrases.
- [x] Проверяется несоответствие текста длительности.
- [x] Проверяется duplicate committed-текста.
- [x] Подозрительный chunk получает ровно один conservative retry.
- [x] Результаты сравниваются и выбирается более правдоподобный.
- [x] Неуверенный фрагмент журналируется и помечается warning.
- [x] Бесконечных retry нет.
- [x] Внешний LLM и скрытое переписывание не используются.

## 16. Пунктуация и команды

- [x] Обычную пунктуацию выполняет Whisper.
- [x] Режим голосовых команд отключаем и по умолчанию выключен.
- [x] Поддержаны все перечисленные RU punctuation/layout commands.
- [x] Поддержаны все перечисленные EN punctuation/layout commands.
- [x] UI явно показывает состояние режима команд.
- [x] Команд запуска программ, удаления файлов и shell execution нет.

## 17. Вставка

- [x] Текст идёт в активную каретку, а не к mouse cursor.
- [x] Запуск диктовки не перехватывает focus.
- [x] Сохраняются HWND, title, process, integrity и capture time.
- [x] Clipboard strategy сохраняет доступные форматы.
- [x] Clipboard получает Unicode, затем отправляется Ctrl+V через `SendInput`.
- [x] После задержки исходный clipboard восстанавливается.
- [x] Lease/sequence защищает чужое параллельное изменение clipboard.
- [x] Unicode `SendInput` fallback поддерживает Cyrillic и surrogate pairs.
- [x] Ввод идёт ограниченными порциями, `keybd_event` не используется.
- [x] Safe mode только копирует committed-текст.
- [x] Formatter корректирует пробелы, punctuation, скобки и новые строки.
- [x] Реально проверены Notepad, Word, Chrome, VS Code и Telegram.
- [x] UIPI/elevated target блокируется без обхода.
- [x] При неудачной вставке текст остаётся в journal/pending FIFO.

## 18. Смена активного окна

- [x] Locked target является режимом по умолчанию.
- [x] При исчезновении/неактивности цели текст накапливается.
- [x] ASR при этом продолжает работать.
- [x] Overlay показывает неактивную цель.
- [x] После возврата цели очередь вставляется строго FIFO.
- [x] Follow focus доступен только после явного включения.
- [x] Overlay показывает текущую follow-focus цель.
- [x] В собственные окна VoxType автоматическая вставка запрещена.

## 19. Горячие клавиши

- [x] Используется native `RegisterHotKey` с `MOD_NOREPEAT`.
- [x] Ctrl+Alt+Space — start/stop.
- [x] Ctrl+Alt+L — RU/EN.
- [x] Ctrl+Alt+P — pause/resume.
- [x] PTT combination хранится в settings, но управление release намеренно
  отключено: `RegisterHotKey` не даёт надёжного key-up, а ТЗ разрешает PTT только
  «если возможно реализовать надёжно».
- [x] Рабочие сочетания настраиваются и атомарно сохраняются.
- [x] Конфликт регистрации не роняет приложение и объясняется пользователю.
- [x] F12 и системные сочетания отклоняются parser/validation.
- [x] Hotkeys работают без фокуса окна, в tray и сторонних приложениях.

## 20. Интерфейс

- [x] Современный PySide6 desktop UI на русском.
- [x] Строки и модель UI допускают добавление локализации.
- [x] Есть большая кнопка «Начать / Остановить».
- [x] Есть RU/EN selector.
- [x] Показаны microphone и audio meter.
- [x] Показаны model/profile.
- [x] Показаны все требуемые состояния.
- [x] Показаны duration, backlog и last fragment.
- [x] Есть Settings, History и Diagnostics.
- [x] Tray содержит start/stop, pause, RU/EN, open, settings и exit.
- [x] Закрытие окна по умолчанию сворачивает в tray.

## 21. Overlay

- [x] Overlay не активируется и не забирает keyboard focus.
- [x] Tool window не появляется в Alt+Tab.
- [x] Перетаскивается и запоминает позицию.
- [x] Использует Qt device-independent geometry для Windows scaling.
- [x] Начальная позиция не перекрывает центр.
- [x] Есть compact и expanded views.
- [x] Compact: mic, RU/EN, timer, level, state.
- [x] Expanded: provisional, queue, target, pause и stop.
- [x] IDLE/LISTENING/SPEECH/PROCESSING/PAUSED/WARNING/ERROR различаются.
- [x] Короткие start/stop sounds отключаемы и по умолчанию выключены.

## 22. Первый запуск

- [x] Мастер объясняет local-only режим.
- [x] Выбирает микрофон и показывает live level.
- [x] Отдельно калибрует тишину и речь.
- [x] Проверяет hardware и предлагает безопасный профиль.
- [x] Загружает/проверяет модель с progress.
- [x] Просит произнести RU и EN фразы и измеряет RTF.
- [x] Проверяет Unicode insertion во встроенном поле.
- [x] Показывает и сохраняет hotkey.
- [x] Мастер можно повторить из настроек.
- [x] Аккаунт не требуется.

## 23. Настройки

- [x] General: startup, tray, overlay, sounds, theme, UI language.
- [x] Dictation: language, model profile, commands, auto insert, safe clipboard,
  target policy, delay, spacing и capitalization.
- [x] Audio: device, VAD sensitivity, profile, suppression, normalization и test.
- [x] Model: installed model, download/delete/benchmark, CPU threads, GPU flag.
- [x] Power modes: energy, balanced, maximum performance.
- [x] Privacy: history/audio/retention/delete/open data folder.
- [x] Hotkeys: toggle, pause, language и PTT setting.
- [x] Diagnostics: log, export, self-test и library versions.
- [x] Settings сохраняются атомарно.
- [x] Corrupt config получает backup и безопасные defaults.

## 24. История и autosave

- [x] Каждая сессия получает UUID.
- [x] SQLite journal обновляется постепенно.
- [x] Хранятся id/start/end/language/model/profile/target.
- [x] Хранятся committed text, warnings и suspicious fragments.
- [x] Хранятся duration, processing time, lost frames и completion status.
- [x] Транзакции и sequence делают commit атомарным/idempotent.
- [x] Незавершённые сессии обнаруживаются при следующем запуске.
- [x] Committed-текст можно восстановить/скопировать/экспортировать.
- [x] Сохранённый spool предлагается дообработать.
- [x] Данные не удаляются до успешного восстановления.
- [x] История по умолчанию включена, raw audio — выключено.
- [x] Обработанные временные chunks удаляются, если audio retention не включён.

## 25. Логирование

- [x] Используются rotating UTF-8 logs.
- [x] Логируются app/Python versions, model/backend/state.
- [x] Логируются chunk duration, inference, RTF и queue.
- [x] Логируются audio/injection errors и recovery.
- [x] Все записи сессии имеют correlation/session id.
- [x] Transcript text и PCM в технический log не попадают.
- [x] Чувствительный текст остаётся только в разрешённом session storage.

## 26. Ошибки и восстановление

- [x] Обрабатываются disconnect/busy/disappeared/default-device microphone cases.
- [x] Обрабатываются missing/corrupt model, low RAM/disk.
- [x] Обрабатываются worker exit и inference timeout watchdog.
- [x] Обрабатываются queue overflow и disk spool recovery.
- [x] Обрабатываются busy clipboard, closed/elevated target.
- [x] Обрабатывается hotkey conflict.
- [x] UI exception перехватывается global hook без traceback пользователю.
- [x] Обрабатываются Windows shutdown, sleep/hibernate и resume.
- [x] UI показывает короткое сообщение, Retry, Diagnostics и Copy text.
- [x] Ошибка вставки не удаляет transcript.

## 27. Производительность

- [x] Число потоков ограничено, ожидания имеют timeout.
- [x] CPU threads выбираются автоматически с запасом системе.
- [x] Есть energy/balanced/maximum power modes.
- [x] Измеряются RTF, latency, backlog, RAM, CPU и spool bytes.
- [x] При backlog UI показывает warning.
- [x] Beam/best-of и необязательная postprocessing временно упрощаются.
- [x] Аудио продолжает сохраняться, chunks не удаляются.
- [x] После drain возвращаются обычные параметры.
- [x] Модель не переключается посреди active session.
- [x] Временные ресурсы освобождаются, модель не выгружается на каждой паузе.

## 28. Безопасность и приватность

- [x] Приложение и installer работают без administrator privileges.
- [x] Нет inbound port, local HTTP server, telemetry или crash upload.
- [x] Аудио не загружается в интернет.
- [x] Продиктованный текст никогда не выполняется как команда или код.
- [x] Пути normalise/resolve и проверяются на ownership/traversal.
- [x] Используются platformdirs.
- [x] Models, settings, sessions и spool находятся вне source tree.

## 29. Структура

- [x] Созданы `src/voxtype`, `tests`, `tools`, `scripts`, `packaging`, `docs`.
- [x] Core/audio/vad/asr/transcript/windows/sessions/ui/settings/monitoring разделены.
- [x] Созданы все обязательные scripts и docs.
- [x] `whisper_cpp_backend.py` не создан обоснованно: адаптер optional, Vulkan на
  target не подтверждён, стабильный CPU backend удовлетворяет RTF.
- [x] Benchmark реализован в `asr/benchmark.py`, UI и `evaluate_wer.py`; отдельный
  дублирующий CLI `benchmark_models.py` не нужен, что разрешено фразой
  «структуру можно изменить».

## 30. Качество кода

- [x] Публичные функции типизированы, строгий mypy проходит.
- [x] Модули небольшие и имеют понятные имена.
- [x] Циклические импорты не обнаружены.
- [x] Тяжёлые сервисы lazy/DI на controller/UI boundary.
- [x] Потоки корректно завершаются; ресурсы используют context managers.
- [x] Межпоточные ожидания ограничены timeout.
- [x] Нет bare `except`, silent infinite retry или production `print`.
- [x] Settings и domain exceptions централизованы.
- [x] Merge-алгоритм документирован; комментарии объясняют причины.
- [x] `ruff format --check`, `ruff check`, `mypy`, `pytest` проходят.

## 31. Unit-тесты

- [x] State machine, ring buffer, bounded queue и disk spool.
- [x] VAD, forced segmentation и overlap.
- [x] TranscriptMerger и все десять обязательных edge cases.
- [x] Formatter, voice commands и vocabulary.
- [x] Hallucination guard.
- [x] Settings migration и atomic write.
- [x] Session journal, crash recovery и retention cleanup.
- [x] Target-window policy и clipboard save/restore.
- [x] Insertion spacing и Unicode fallback.
- [x] Language switching и performance metrics.
- [x] Fixtures deterministic и не требуют реального microphone.

## 32. Интеграционные тесты

- [x] WAV source подаёт PCM как microphone dispatcher.
- [x] Проверены RU и EN WAV.
- [x] Проверены quiet/loud speech и clipping.
- [x] Проверены long pause и continuous speech.
- [x] Проверены background noise и language switching.
- [x] Проверен stop во время processing.
- [x] Проверены temporary ASR lag и backlog.
- [x] Проверены worker crash, inference hang и recovery.
- [x] Проверено восстановление FIFO с disk spool.
- [x] Fixtures собственные/синтетические, чужих записей нет.

## 33. Windows insertion smoke

- [x] Реально открыт Notepad и установлена каретка.
- [x] Вставлен заранее известный текст production service.
- [x] Проверены Unicode, Cyrillic, English и punctuation.
- [x] Проверена новая строка.
- [x] Clipboard восстановлен.
- [x] Overlay не получил focus.
- [x] При смене окна locked target поставил текст в очередь и вернул FIFO.
- [x] Проверено сохранённое содержимое Notepad, а не return value `SendInput`.
- [x] Дополнительная матрица реально прошла Word/Chrome/VS Code/Telegram 4/4.

## 34. 60-минутный soak

- [x] Wall clock 3 600.937 с, 361 samples с шагом 10 с.
- [x] Эмулированы speech, pauses, forced chunks, processing, journal и injection.
- [x] Эмулирован временный backlog с disk spool.
- [x] Измерены RAM, CPU, threads, queue, drops и temporary bytes.
- [x] Измерены chunks, RTF, latency, duplicates, retries и exceptions.
- [x] Приложение не упало; exceptions=0.
- [x] Dropped frames=0, FIFO pass, duplicates=0.
- [x] Все 3600 chunks/57 600 000 frames обработаны.
- [x] Queue и spool полностью опустошены.
- [x] RAM не росла линейно; threads не утекали.
- [x] Temporary files очищены; journal валиден.
- [x] Реальный JSON и цифры сохранены без выдумывания.

## 35. Точность

- [x] Создан `tools/evaluate_wer.py`.
- [x] Принимает audio/reference/language/profile.
- [x] Считает WER, substitutions, deletions, insertions, time и RTF.
- [x] Есть case/punctuation/ё-е/number normalization switches.
- [x] Raw reference/hypothesis сохраняются.
- [x] Документирован личный набор из 10–20 фраз всех требуемых типов.
- [x] Выполнены реальные RU/EN benchmarks small и large-v3-turbo.
- [x] Превосходство над Windows Dictation без сравнения не заявляется.

## 36. Критерии приёмки

- [x] RU pipeline, explicit language, tail flush и Notepad insertion проверены
  детерминированным WAV + реальным Windows target.
- [x] EN pipeline и отсутствие перевода на русский проверены реальным EN WAV.
- [x] 60-минутная работа, zero loss, bounded RAM, progressive text и drain пройдены.
- [x] Quiet speech, clipping warning и устойчивость проверены fixtures/интеграцией.
- [x] Смена цели, отсутствие wrong-window insertion и сохранность FIFO пройдены.
- [x] Crash/unfinished session detection и committed-text recovery пройдены.
- [x] Установленная модель прошла inference с `HF_HUB_OFFLINE=1` и нерабочими proxy.
- [!] Не выполнялась именно живая многoабзацная RU/EN диктовка голосом пользователя
  в этой сессии; функциональные части сценария проверены отдельно реальным ASR,
  WAV pipeline и реальным Notepad.

## 37. Windows-сборка

- [x] Создан стабильный Nuitka one-folder portable.
- [x] Portable запускается с намеренно неверными `PYTHONHOME/PYTHONPATH`.
- [x] Создан per-user Inno Setup installer.
- [x] Включены Qt plugins, PortAudio, CTranslate2/ONNX DLL, VAD, icons и manifest.
- [x] В EXE записаны version information и icon.
- [x] Build scripts fail-fast, очищают свои build targets и печатают этапы.
- [x] Скрипты запускают lint/test (если явно не передан внутренний `SkipChecks`).
- [x] Готовый EXE проходит self/inference/UI smoke.
- [x] Release ZIP/installer имеют SHA-256 files.
- [x] Autostart реализован и выключен по умолчанию.
- [x] Поддельной подписи нет; unsigned limitation документирован.
- [!] Запуск на другом физически чистом Windows-ПК не выполнялся; standalone
  без Python подтверждён на текущей системе с испорченными Python env и полным
  offline inference.

## 38. Документация

- [x] `README_RU.md`: назначение, запуск, установка, onboarding, hotkeys,
  модели и ограничения.
- [x] `docs/USER_GUIDE_RU.md`: dictation, RU/EN, microphone, safe mode,
  history, recovery и deletion.
- [x] `docs/TROUBLESHOOTING_RU.md`: все девять требуемых проблем.
- [x] `docs/ARCHITECTURE.md`: components, threads, queues, chunk lifecycle,
  merge и recovery.
- [x] `docs/PRIVACY.md`: local data, files и полное удаление.
- [x] `docs/TEST_PLAN.md`: unit, integration, Windows, soak и WER.
- [x] `docs/BENCHMARK_RESULTS.md`: hardware, commands, results, profile,
  release и limitations.

## 39. Финальный review

- [x] Независимо проверены audio loss, races, deadlocks и queues.
- [x] Проверены thread leaks, shutdown и inference timeout.
- [x] Проверены clipboard corruption и wrong-window insertion.
- [x] Проверены chunk duplicates и merge.
- [x] Проверены network/privacy/logging.
- [x] Проверены error handling, packaging и placeholders.
- [x] P0/P1 замечания исправлены.
- [x] После исправлений повторены ruff format/check, mypy и pytest.
- [x] Повторены integration и Windows app matrix.
- [x] Повторно собраны portable/installer и проверен packaged EXE.

## 40. Финальный результат

- [x] Путь к source указан.
- [x] Путь к portable указан.
- [x] Путь к installer указан.
- [x] Команда запуска указана.
- [x] Автоматически выбранная модель указана.
- [x] Переключение RU/EN указано.
- [x] Hotkey указан.
- [x] Реально выполненные тесты перечислены.
- [x] Число тестов указано.
- [x] 60-минутный soak указан.
- [x] Реальный RTF указан.
- [x] Реальная RAM указана.
- [x] Ограничения и непроверенное указаны.
- [x] Созданные файлы перечислены.
- [x] Финальный Git commit указан после его создания.
- [x] Приоритеты zero loss, continuous work, accuracy, correct target,
  responsiveness, stability, recovery, local-only и usable UI соблюдены.
