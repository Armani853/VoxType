"""File transcription queue and timestamped transcript editor."""

from __future__ import annotations

from dataclasses import replace
from importlib.util import find_spec
from pathlib import Path
from sqlite3 import Error as SQLiteError
from threading import Thread

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QListWidget, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from voxtype.asr.file_transcription import FileSegment, FileTranscript, export_transcript
from voxtype.asr.transcription_queue import FileTranscriptionQueue, TranscriptionJob
from voxtype.windows.local_paths import is_local_path


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
        self._opening = False
        self._writing = False
        self._player = None
        root = QVBoxLayout(self)
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
        self._button(bar, "Добавить аудио…", self._enqueue)
        root.addLayout(bar)
        queue_actions = QHBoxLayout()
        self.pause = self._button(queue_actions, "Пауза очереди", self._pause)
        self._button(queue_actions, "Отменить ожидающие", self._cancel_pending)
        self._button(queue_actions, "Отменить выбранное", self._cancel)
        self._button(queue_actions, "Открыть транскрипт", self._open)
        root.addLayout(queue_actions)
        self.jobs = QListWidget(self)
        self.jobs.itemDoubleClicked.connect(lambda _item: self._open())
        root.addWidget(self.jobs)
        self.segments = QTableWidget(0, 3, self)
        self.segments.setHorizontalHeaderLabels(("Начало, с", "Конец, с", "Текст"))
        self.segments.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.segments, 2)
        editor_actions = QHBoxLayout()
        self.previous = self._button(editor_actions, "←", lambda: self._change_page(-1))
        self.page_number = QLabel("—", self)
        editor_actions.addWidget(self.page_number)
        self.next = self._button(editor_actions, "→", lambda: self._change_page(1))
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self._button(editor_actions, "К выбранному участку аудио", self._seek)
        self._button(editor_actions, "Сохранить правки", self._save)
        self._button(editor_actions, "Экспорт…", self._export)
        root.addLayout(editor_actions)
        self.feedback = QLabel(self)
        self.feedback.setWordWrap(True)
        root.addWidget(self.feedback)
        self.transcript_ready.connect(self._show_transcript)
        self.write_finished.connect(self._finish_write)

    def _button(self, row, label, action):
        button = QPushButton(label, self)
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
        selected, _ = QFileDialog.getOpenFileName(
            self, "Аудиофайл", "", "Аудио (*.wav *.mp3 *.m4a *.flac)",
        )
        if not selected:
            return
        source = Path(selected)
        destination = None
        suffix = self.automatic_export.currentData()
        if suffix:
            selected, _ = QFileDialog.getSaveFileName(
                self, "Новый файл транскрипта", str(source.with_suffix(suffix)),
                f"{suffix[1:].upper()} (*{suffix})",
                options=QFileDialog.Option.DontConfirmOverwrite,
            )
            if not selected:
                return
            destination = Path(selected)
            if not destination.suffix:
                destination = destination.with_suffix(suffix)
        try:
            self.queue.enqueue(source, destination, self.language.currentData(),
                               self.profile.currentData(), self.priority.currentData())
        except (OSError, ValueError, SQLiteError) as exc:
            self.feedback.setText(str(exc))
            return
        self.feedback.setText("Задание добавлено")
        self.refresh()

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
        if self._transcript is None:
            return
        offset = self._page * self._PAGE_SIZE
        for row in range(self.segments.rowCount()):
            index = offset + row
            text = self.segments.item(row, 2).text()
            item = self._working_segments[index]
            if text != item.text:
                self._working_segments[index] = replace(item, text=text)

    def _change_page(self, step: int) -> None:
        destination = self._page + step
        if not 0 <= destination * self._PAGE_SIZE < len(self._working_segments):
            return
        self._capture_page_edits()
        self._page = destination
        self._show_page()

    def _show_page(self) -> None:
        offset = self._page * self._PAGE_SIZE
        visible = self._working_segments[offset:offset + self._PAGE_SIZE]
        self.segments.setRowCount(len(visible))
        for row, segment in enumerate(visible):
            for column, value in enumerate((f"{segment.start:.3f}", f"{segment.end:.3f}", segment.text)):
                item = QTableWidgetItem(value)
                if column < 2:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.segments.setItem(row, column, item)
        total = max(1, (len(self._working_segments) + self._PAGE_SIZE - 1) // self._PAGE_SIZE)
        self.page_number.setText(f"{self._page + 1} / {total}")
        self.previous.setEnabled(self._page > 0)
        self.next.setEnabled(self._page + 1 < total)

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
        row = self.segments.currentRow()
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
        when = round(self._working_segments[self._page * self._PAGE_SIZE + row].start * 1000)
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
