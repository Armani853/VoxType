# Архитектура VoxType

## Потоки и владение

```text
PortAudio callback -> bounded callback queue -> audio dispatcher
                                           -> RAM queue / disk spool
                                           -> VAD + StreamingSegmenter
                                           -> single ASR worker
                                           -> HallucinationGuard (max 1 retry)
                                           -> TranscriptMerger
                                           -> SQLite SessionJournal
                                           -> target-safe injection FIFO
Qt main thread     <- bounded events/snapshot <- ApplicationController
```

- Qt main thread владеет окнами, tray и native-event dispatch.
- Callback только копирует PCM и обновляет простые счётчики.
- Audio dispatcher владеет resampler/preprocessor и пишет lossless queue.
- Один transcription worker последовательно обрабатывает chunks; это сохраняет
  порядок и ограничивает RAM/число потоков.
- Controller владеет lifecycle, но получает тяжёлые сервисы через конструктор.

## Lifecycle

Строгие состояния: `STARTING → MODEL_LOADING → IDLE → LISTENING →
SPEECH_DETECTED/TRANSCRIBING → FINISHING → IDLE`, плюс
`PAUSED_TARGET_LOST`, recoverable/fatal error и `SHUTTING_DOWN`. Переход
фиксируется до уведомления UI; неизвестный переход отклоняется.

При Stop callback закрывается первым, dispatcher дренирует ingress, segmenter
выдаёт `FLUSH`, ASR queue дренируется, committed tail журналируется/доставляется,
затем сессия помечается completed. Если inference не завершился за shutdown
budget, SQLite и injection не закрываются под живым worker: процесс оставляет
сессию незавершённой и durable spool доступным следующему запуску. Принудительно
убивать Python thread небезопасно, поэтому отдельного ложного «успеха» нет.

## Жизненный цикл audio chunk

1. Mono float32 с source sample rate получает sequence.
2. Streaming polyphase resampler преобразует в 16 кГц без разрыва между blocks.
3. Preprocessor удаляет DC, ограниченно high-pass/normalize/limit; уровни
   считаются до потери диагностической информации.
4. VAD получает 30-мс frames с pre/post-roll.
5. Естественная пауза завершает segment; непрерывная речь принудительно режется
   около 12 секунд, максимум 18 секунд, overlap около 1 секунды.
6. PCM segment передаётся ASR как ограниченный ndarray, не как вся сессия.

## Disk spool

Каждая запись имеет sequence, sample rate, capture timestamp, frame count,
checksum и atomic final filename. Lease удаляется только после `ack`; при crash
готовые записи перечитываются по sequence. Temporary incomplete files не
считаются очередью. Spool и RAM имеют явные лимиты/метрики.

## Overlap merge

Whisper word timestamps переводятся в абсолютную шкалу сессии. Merger сравнивает
нормализованный suffix committed и prefix нового chunk до 20 слов, допускает
пунктуацию/регистр и одну локальную ошибку. Совпадение удаляется только если audio
chunks действительно пересекались во времени; одинаковая фраза в следующем
неперекрывающемся chunk остаётся реальным повтором.

## Target и clipboard

Target identity включает HWND и PID. Перед каждой вставкой policy повторно
проверяет существование, foreground, собственное окно и integrity. Clipboard
snapshot содержит доступные eager formats и sequence number. Ctrl+V идёт одним
SendInput batch; затем snapshot восстанавливается, только если clipboard всё ещё
содержит нашу временную запись. Unicode SendInput — fallback, safe-copy — режим
для UIPI/пользователя.

## Recovery и приватность

SQLite WAL постепенно хранит committed text, warnings, metrics и final status.
Callback ingress использует основную и emergency bounded RAM-очереди. Переход в
emergency-очередь однонаправленный до конца сессии, поэтому новый PCM не может
обогнать старый; исчерпание обеих очередей немедленно завершает сессию как
recoverable error, а не маскируется как успешная запись. Spool-файлы удаляются
после commit+ack. При включённом сохранении аудио параллельно
создаётся локальный 16-bit WAV. Rotating log хранит состояния и технические ошибки
без текста/PCM; версии, RTF и ресурсы доступны в Diagnostics. Model cache,
settings, logs и sessions размещаются через `platformdirs`.
