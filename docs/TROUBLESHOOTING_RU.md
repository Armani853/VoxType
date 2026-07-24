# Решение проблем

## Микрофон не виден

Проверьте Windows Settings → Privacy & security → Microphone, затем перезапустите
VoxType и self-test. Закройте программу, монопольно занявшую endpoint; выберите
WASAPI input, а не HDMI/output. При отключении USB-микрофона сессия сохраняется,
после подключения нажмите «Повторить».

## Текст не вставляется

Вернитесь к Locked target и проверьте название overlay. Если цель elevated,
используйте safe-copy; не запускайте VoxType администратором без необходимости.
Для conflict/remote desktop попробуйте Unicode fallback. Любой undelivered текст
остаётся в history/pending queue.

## Clipboard не восстановился

VoxType не затирает clipboard, если он изменён другим приложением после paste.
Откройте history и убедитесь, что текст сохранён. Для clipboard manager включите
Unicode input либо safe-copy.

## Горячая клавиша занята

RegisterHotKey вернёт конфликт без падения. Настройки → Горячие клавиши: задайте
другую комбинацию с Ctrl/Alt/Shift. F12 и Windows system shortcuts запрещены.

## Приложение медленное / backlog растёт

Запустите model benchmark. Нужен RTF < 1; выберите «Быстро», уменьшите beam или
закройте тяжёлые задачи. Audio продолжает уходить в disk spool; следите за
свободным местом. Не выбирайте large-v3 только по названию.

## Модель не скачивается

Проверьте сеть, свободное место и дату/сертификаты Windows. Нажмите «Повторить»:
partial directory не считается готовой моделью. Proxy/антивирус может блокировать
Hugging Face; скачивание не нужно после успешной установки.

## Слишком тихо или клиппинг

Повторите калибровку. Для тихой речи оставьте Normal, увеличивайте Windows input
level умеренно. При clipping уменьшите gain; limiter не восстанавливает уже
срезанные пики.

## Дублируются слова

Экспортируйте diagnostic report без приватного текста и сохраните session ID.
Попробуйте стандартный overlap/segment settings; слишком большой overlap и
сломанные timestamps ухудшают merge. Текст можно исправить в history вручную.

## Большой disk spool

Остановите запись штатно и дождитесь «Готово». Проверьте model RTF и место. После
успешного ack временные chunks удаляются; незавершённые оставлены для recovery.

## Где лог

Настройки → Диагностика → «Открыть лог». В нём нет полного transcript и PCM.
Приложите также версии, hardware probe, dropped frames, RTF и traceback-free
human-readable error.
