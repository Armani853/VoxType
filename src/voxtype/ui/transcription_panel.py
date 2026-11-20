"""File transcription queue and timestamped transcript editor."""

from __future__ import annotations

from dataclasses import replace
from importlib.util import find_spec
from pathlib import Path
from sqlite3 import Error as SQLiteError
from threading import Thread

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QTextBlockFormat, QTextCursor
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QListWidget, QMessageBox, QPushButton,
    QApplication, QProgressBar, QSplitter, QStackedWidget, QTextEdit, QVBoxLayout, QWidget,
)

from voxtype.asr.file_transcription import FileSegment, FileTranscript, export_transcript
from voxtype.asr.transcription_queue import FileTranscriptionQueue, TranscriptionJob
from voxtype.windows.local_paths import is_local_path

from .obsidian import VoxButton, VoxEmptyState, VoxSectionHeader
from .records_view import RecordsView


class TranscriptionPanel(QWidget):
    transcript_ready = Signal(object, object, str)
    write_finished = Signal(object, str, bool)
    _PAGE_SIZE = 200

    def __init__(self, queue: FileTranscriptionQueue, parent: QWidget) -> None:
        super().__init__(parent)
        self.queue = queue
        self._revision = -1
        self._jobs: tuple[TranscriptionJob, ...] = ()
        self._job: TranscriptionJob | None = None
        self._transcript: FileTranscript | None = None
        self._working_segments: list[FileSegment] = []
        self._page = 0
        self._segment_index = -1
        self._opening = False
        self._writing = False
        self._player = None
        root = QVBoxLayout(self)
        self.setAcceptDrops(True)
        bar = QHBoxLayout()
        self.language = QComboBox(self)
        for label, value in (("Русский", "ru"), ("English", "en")):
            self.language.addItem(label, value)
        self.profile = QComboBox(self)
        self.priority = QComboBox(self)
        for label, value in (("Обычный", 0), ("Высокий", 10), ("Низкий", -10)):
            self.priority.addItem(label, value)
        self.automatic_export = QComboBox(self)
        for label, suffix in (("Без автоэкспорта", ""), ("TXT", ".txt"), ("JSON", ".json"),
                              ("SRT", ".srt"), ("WebVTT", ".vtt")):
            self.automatic_export.addItem(label, suffix)
        if find_spec("docx"):
            self.automatic_export.addItem("DOCX", ".docx")
        for combo in (self.language, self.profile, self.priority, self.automatic_export):
            bar.addWidget(combo)
        self.add_button = self._button(bar, "Добавить файлы…", self._enqueue)
        root.addLayout(bar)
        queue_actions = QHBoxLayout()
        self.pause = self._button(queue_actions, "Пауза очереди", self._pause)
        self._button(queue_actions, "Отменить ожидающие", self._cancel_pending)
        self._button(queue_actions, "Отменить выбранное", self._cancel)
        self._button(queue_actions, "Открыть транскрипт", self._open)
        root.addLayout(queue_actions)
        self.jobs_pane = QStackedWidget(self)
        self.jobs = QListWidget(self.jobs_pane)
        self.jobs.setAccessibleName("Очередь транскрипции")
        self.jobs.itemDoubleClicked.connect(lambda _item: self._open())
        self.jobs_pane.addWidget(self.jobs)
        self.empty = VoxEmptyState("files", "Нет транскрипций",
                                   "Добавьте аудиофайлы или перетащите их в эту область.", self.jobs_pane)
        self.empty.action.setText("Добавить файлы…")
        self.empty.action.clicked.connect(self._enqueue)
        self.empty.action.show()
        self.jobs_pane.addWidget(self.empty)
        self.jobs_pane.setCurrentWidget(self.empty)
        root.addWidget(self.jobs_pane)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("Выполняется распознавание аудиофайла")
        self.progress.hide()
        root.addWidget(self.progress)

        self.editor_split = QSplitter(self)
        self.segments = RecordsView(("Начало", "Фрагмент"), self.editor_split)
        self.segments.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.segments.setSortingEnabled(False)
        self.segments.setAccessibleName("Сегменты с временными метками")
        self.segment_edit = QTextEdit(self.editor_split)
        self.segment_edit.setAcceptRichText(False)
        self.segment_edit.setAccessibleName("Текст выбранного сегмента")
        self.segment_edit.setPlaceholderText("Выберите сегмент транскрипта")
        self.segment_edit.setEnabled(False)
        self.editor_split.addWidget(self.segments)
        self.editor_split.addWidget(self.segment_edit)
        self.editor_split.setStretchFactor(1, 2)
        self.segments.selectionModel().currentChanged.connect(self._segment_selected)
        root.addWidget(self.editor_split, 2)
        editor_actions = QHBoxLayout()
        self.previous = self._button(editor_actions, "←", lambda: self._change_page(-1))
        self.page_number = QLabel("—", self)
        editor_actions.addWidget(self.page_number)
        self.next = self._button(editor_actions, "→", lambda: self._change_page(1))
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self.seek_button = self._button(editor_actions, "К аудио", self._seek)
        self.seek_button.setEnabled(False)
        self._button(editor_actions, "Копировать", self._copy)
        self._button(editor_actions, "Найти…", self._search)
        self._button(editor_actions, "Сохранить правки", self._save)
        self._button(editor_actions, "Экспорт…", self._export)
        root.addLayout(editor_actions)
        self.feedback = QLabel(self)
        self.feedback.setWordWrap(True)
        root.addWidget(self.feedback)
        self.transcript_ready.connect(self._show_transcript)
        self.write_finished.connect(self._finish_write)

    def _button(self, row, label, action):
        button = VoxButton(label, self)
        button.clicked.connect(action)
        row.addWidget(button)
        return button

    def refresh(self) -> None:
        if not self.isVisible():
            return
        selected_profile = self.profile.currentData() or self.queue.controller.profiles.default_id
        profiles = tuple((item.name, item.id) for item in self.queue.controller.profiles.profiles()
                         if self.queue.controller.profile_allowed(item))
        existing = tuple((self.profile.itemText(i), self.profile.itemData(i))
                         for i in range(self.profile.count()))
        if profiles != existing:
            self.profile.clear()
            for label, value in profiles:
                self.profile.addItem(label, value)
            index = self.profile.findData(selected_profile)
            self.profile.setCurrentIndex(max(0, index))
        self.pause.setText("Продолжить очередь" if self.queue.paused else "Пауза очереди")
        revision = self.queue.revision
        if revision == self._revision:
            return
        try:
            jobs = self.queue.recent()
        except (OSError, SQLiteError):
            self.feedback.setText("Очередь недоступна")
            return
        self._revision = revision
        if jobs == self._jobs:
            return
        current = self._selected()
        self._jobs = jobs
        self.jobs_pane.setCurrentWidget(self.jobs if jobs else self.empty)
        self.progress.setVisible(any(job.status == "RUNNING" for job in jobs))
        self.jobs.clear()
        for row, job in enumerate(jobs):
            label = f"{job.status} · {job.language.upper()} · {Path(job.input_path).name}"
            self.jobs.addItem(f"{label} · {job.error}" if job.error else label)
            if current and job.id == current.id:
                self.jobs.setCurrentRow(row)

    def _selected(self) -> TranscriptionJob | None:
        row = self.jobs.currentRow()
        return self._jobs[row] if 0 <= row < len(self._jobs) else None

    def _enqueue(self) -> None:
        self.refresh()
        if not self.profile.currentData():
            return
        sources, _ = QFileDialog.getOpenFileNames(
            self, "Добавить аудио", "", "Аудио (*.wav *.mp3 *.m4a *.flac)",
        )
        if sources:
            self._enqueue_sources([Path(name) for name in sources])

    def dragEnterEvent(self, event: object) -> None:
        if self._local_audio(event):
            event.acceptProposedAction()

    def dropEvent(self, event: object) -> None:
        paths = self._local_audio(event)
        if paths:
            self._enqueue_sources(paths)
            event.acceptProposedAction()

    @staticmethod
    def _local_audio(event: object) -> list[Path]:
        if not event.mimeData().hasUrls():
            return []
        return [Path(url.toLocalFile()) for url in event.mimeData().urls()[:100]
                if url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() in {".wav", ".mp3", ".m4a", ".flac"}]

    def _enqueue_sources(self, sources: list[Path]) -> None:
        if not self.profile.currentData():
            self.feedback.setText("Выберите доступный профиль")
            return
        suffix = self.automatic_export.currentData()
        failures = []
        for source in sources:
            destination = None
            if suffix:
                selected, _ = QFileDialog.getSaveFileName(
                    self, "Новый файл транскрипта", str(source.with_suffix(suffix)),
                    f"{suffix[1:].upper()} (*{suffix})", options=QFileDialog.Option.DontConfirmOverwrite,
                )
                if not selected:
                    continue
                destination = Path(selected)
                if not destination.suffix:
                    destination = destination.with_suffix(suffix)
            try:
                self.queue.enqueue(source, destination, self.language.currentData(),
                                   self.profile.currentData(), self.priority.currentData())
            except (OSError, ValueError, SQLiteError) as exc:
                failures.append(str(exc))
        self.feedback.setText("\n".join(failures[:3]) if failures else "Очередь обновлена")
        self.refresh()

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)
        if hasattr(self, "editor_split"):
            flow = Qt.Orientation.Vertical if self.width() < 620 else Qt.Orientation.Horizontal
            if flow != self.editor_split.orientation():
                self.editor_split.setOrientation(flow)

    def _pause(self) -> None:
        self.queue.set_paused(not self.queue.paused)
        self.refresh()

    def _cancel_pending(self) -> None:
        self.queue.cancel_pending()
        self.refresh()

    def _cancel(self) -> None:
        job = self._selected()
        if job is not None:
            self.queue.cancel(job.id, active=True)
            self.refresh()

    def _open(self) -> None:
        job = self._selected()
        if job is None or self._opening or self._writing:
            return
        self._capture_page_edits()
        if self._transcript and tuple(self._working_segments) != self._transcript.segments:
            if QMessageBox.question(self, "Несохранённые правки", "Открыть другой транскрипт?") != QMessageBox.StandardButton.Yes:
                return
        self._opening = True
        self.segments.setEnabled(False)

        def read() -> None:
            try:
                transcript = self.queue.get_result(job.id)
            except (OSError, SQLiteError, ValueError, TypeError, KeyError):
                self.transcript_ready.emit(job, None, "Не удалось прочитать транскрипт")
            else:
                self.transcript_ready.emit(job, transcript, "")

        Thread(target=read, name="voxtype-file-transcript-read", daemon=True).start()

    def _show_transcript(self, job: TranscriptionJob, transcript: FileTranscript | None, error: str) -> None:
        self._opening = False
        self.segments.setEnabled(True)
        if transcript is None:
            self.feedback.setText(error or "Для этого задания пока нет транскрипта")
            return
        if self._player:
            self._player.stop()
        self._job, self._transcript = job, transcript
        self._working_segments = list(transcript.segments)
        self._page = 0
        self._show_page()
        self.feedback.setText(Path(job.input_path).name)

    def _capture_page_edits(self) -> None:
        if 0 <= self._segment_index < len(self._working_segments):
            current = self._working_segments[self._segment_index]
            value = self.segment_edit.toPlainText()
            if value != current.text:
                self._working_segments[self._segment_index] = replace(current, text=value)

    def _segment_selected(self, current: object, _previous: object) -> None:
        self._capture_page_edits()
        value = current.data(Qt.ItemDataRole.UserRole) if current.isValid() else None
        self._segment_index = int(value) if value is not None else -1
        active = 0 <= self._segment_index < len(self._working_segments)
        self.segment_edit.setEnabled(active)
        self.segment_edit.setPlainText(self._working_segments[self._segment_index].text if active else "")
        if active:
            cursor = self.segment_edit.textCursor()
            cursor.select(QTextCursor.SelectionType.Document)
            format = QTextBlockFormat()
            format.setLineHeight(135, 1)
            cursor.mergeBlockFormat(format)
        self.seek_button.setEnabled(active and self._job is not None
                                    and Path(self._job.input_path).is_file())

    def _change_page(self, step: int) -> None:
        destination = self._page + step
        if not 0 <= destination * self._PAGE_SIZE < len(self._working_segments):
            return
        self._capture_page_edits()
        self._page = destination
        self._show_page()

    def _show_page(self) -> None:
        self._segment_index = -1
        offset = self._page * self._PAGE_SIZE
        visible = self._working_segments[offset:offset + self._PAGE_SIZE]
        self.segments.records.replace([
            (str(index), (f"{segment.start:08.3f} с", " ".join(segment.text.split())[:90]))
            for index, segment in enumerate(visible, start=offset)
        ])
        if visible:
            self.segments.selectRow(0)
        else:
            self.segment_edit.clear()
            self.segment_edit.setEnabled(False)
            self.seek_button.setEnabled(False)
        total = max(1, (len(self._working_segments) + self._PAGE_SIZE - 1) // self._PAGE_SIZE)
        self.page_number.setText(f"{self._page + 1} / {total}")
        self.previous.setEnabled(self._page > 0)
        self.next.setEnabled(self._page + 1 < total)

    def _copy(self) -> None:
        transcript = self._edited()
        if transcript is not None:
            QApplication.clipboard().setText(self.segment_edit.textCursor().selectedText() or transcript.text)

    def _search(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        query, ok = QInputDialog.getText(self, "Поиск по транскрипту", "Текст")
        if not ok or not query.strip():
            return
        self._capture_page_edits()
        needle = query.strip().casefold()
        for index, segment in enumerate(self._working_segments):
            if needle in segment.text.casefold():
                self._page = index // self._PAGE_SIZE
                self._show_page()
                self.segments.selectRow(index % self._PAGE_SIZE)
                self.segment_edit.find(query.strip())
                return
        self.feedback.setText("Фрагменты не найдены")

    def _edited(self) -> FileTranscript | None:
        if self._transcript is None:
            return None
        self._capture_page_edits()
        return replace(self._transcript, segments=tuple(self._working_segments))

    def _save(self) -> None:
        if self._opening or self._writing:
            return
        transcript = self._edited()
        if self._job is not None and transcript is not None:
            job_id = self._job.id
            self._write(lambda: self.queue.save_result(job_id, transcript),
                        transcript, "Правки сохранены")

    def _export(self) -> None:
        if self._opening or self._writing:
            return
        transcript = self._edited()
        if self._job is None or transcript is None:
            return
        formats = "TXT (*.txt);;JSON (*.json);;SRT (*.srt);;WebVTT (*.vtt)"
        if find_spec("docx"):
            formats += ";;DOCX (*.docx)"
        selected, selected_filter = QFileDialog.getSaveFileName(
            self, "Экспорт транскрипта", str(Path(self._job.input_path).with_suffix(".txt")),
            formats, options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if not selected:
            return
        target = Path(selected)
        if not target.suffix:
            target = target.with_suffix(selected_filter.split("*")[-1].rstrip(")"))
        if self.queue.controller.policy.enforce_offline_only and (
            not is_local_path(target.absolute()) or not is_local_path(target.resolve())
        ):
            self.feedback.setText("Политика организации разрешает только локальные файлы")
            return
        overwrite = target.exists()
        if overwrite and QMessageBox.question(
            self, "Заменить файл?", target.name
        ) != QMessageBox.StandardButton.Yes:
            return
        source = Path(self._job.input_path)
        self._write(lambda: export_transcript(transcript, target, source=source, overwrite=overwrite),
                    None, "Транскрипт экспортирован")

    def _write(self, action, saved: FileTranscript | None, message: str) -> None:
        self._writing = True

        def run() -> None:
            try:
                action()
            except (OSError, ValueError, ImportError, SQLiteError) as exc:
                self.write_finished.emit(None, str(exc), False)
            else:
                self.write_finished.emit(saved, message, True)

        Thread(target=run, name="voxtype-transcript-write", daemon=True).start()

    def _finish_write(self, saved: FileTranscript | None, message: str, success: bool) -> None:
        self._writing = False
        if success and saved is not None:
            self._transcript = saved
        self.feedback.setText(message)

    def _seek(self) -> None:
        row = self._segment_index
        if self._job is None or self._transcript is None or row < 0:
            return
        source = Path(self._job.input_path)
        if not source.is_file():
            self.feedback.setText("Исходное аудио недоступно")
            return
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        except ImportError:
            self.feedback.setText("Воспроизведение Qt Multimedia недоступно")
            return
        if self._player is None:
            self._player = QMediaPlayer(self)
            self._player.setAudioOutput(QAudioOutput(self._player))
        url = QUrl.fromLocalFile(str(source))
        when = round(self._working_segments[row].start * 1000)
        player = self._player
        if player.source() == url and player.isSeekable():
            player.setPosition(when)
        else:
            self._pending_position = when
            if not self.property("seekConnected"):
                player.seekableChanged.connect(
                    lambda ready: player.setPosition(self._pending_position) if ready else None
                )
                self.setProperty("seekConnected", True)
            player.setSource(url)
        player.play()
