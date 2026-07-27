#!/usr/bin/env python3
"""Local audio/reference reviewer for producing human-checked ground truth."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSignalBlocker, Qt, QUrl
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True, slots=True)
class ReviewItem:
    audio: Path
    supplied_reference: Path


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio-dir", type=Path, required=True)
    parser.add_argument("--references-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--pattern", default="*_voice_*.ogg")
    return parser.parse_args()


def _items(audio_dir: Path, references_dir: Path, pattern: str) -> tuple[ReviewItem, ...]:
    items: list[ReviewItem] = []
    for audio in sorted(audio_dir.glob(pattern)):
        exact = references_dir / f"{audio.stem}.txt"
        matches = (
            [exact] if exact.is_file() else sorted(references_dir.glob(f"{audio.stem[:3]}*.txt"))
        )
        if len(matches) == 1:
            items.append(ReviewItem(audio.resolve(), matches[0].resolve()))
    if not items:
        raise FileNotFoundError("no audio/reference pairs matched")
    return tuple(items)


def _time_label(milliseconds: int) -> str:
    seconds = max(0, milliseconds // 1_000)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class ReferenceReviewer(QMainWindow):
    def __init__(self, items: tuple[ReviewItem, ...], output_directory: Path) -> None:
        super().__init__()
        self.items = items
        self.output_directory = output_directory.resolve()
        self.output_directory.mkdir(parents=True, exist_ok=True)
        self.current_index = -1
        self.dirty = False

        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.8)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.playbackStateChanged.connect(self._playback_state_changed)

        self.file_list = QListWidget()
        self.file_list.addItems([item.audio.name for item in items])
        self.file_list.currentRowChanged.connect(self._select)
        self.editor = QPlainTextEdit()
        self.editor.textChanged.connect(self._mark_dirty)
        self.reviewed = QCheckBox("Прослушано полностью и исправлено вручную")
        self.reviewed.stateChanged.connect(self._mark_dirty)
        self.status = QLabel("Непроверенный внешний ASR-эталон")
        self.status.setWordWrap(True)
        self.position = QLabel("00:00 / 00:00")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(self.player.setPosition)
        self.play_button = QPushButton("Воспроизвести")
        self.play_button.clicked.connect(self._toggle_playback)
        rewind = QPushButton("−5 с")
        rewind.clicked.connect(
            lambda: self.player.setPosition(max(0, self.player.position() - 5_000))
        )
        forward = QPushButton("+5 с")
        forward.clicked.connect(lambda: self.player.setPosition(self.player.position() + 5_000))
        save = QPushButton("Сохранить проверенную копию")
        save.clicked.connect(self._save)
        export = QPushButton("Открыть папку…")
        export.clicked.connect(self._choose_output)

        controls = QHBoxLayout()
        controls.addWidget(self.play_button)
        controls.addWidget(rewind)
        controls.addWidget(forward)
        controls.addWidget(self.position)
        controls.addStretch(1)
        controls.addWidget(save)
        controls.addWidget(export)
        right_layout = QVBoxLayout()
        right_layout.addWidget(self.status)
        right_layout.addWidget(self.slider)
        right_layout.addLayout(controls)
        right_layout.addWidget(self.editor, 1)
        right_layout.addWidget(self.reviewed)
        right = QWidget()
        right.setLayout(right_layout)
        splitter = QSplitter()
        splitter.addWidget(self.file_list)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)
        self.setWindowTitle("VoxType — ручная проверка эталонов")
        self.resize(1_150, 720)

        save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        save_shortcut.activated.connect(self._save)
        play_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        play_shortcut.activated.connect(self._toggle_playback)
        rewind_shortcut = QShortcut(QKeySequence("Alt+Left"), self)
        rewind_shortcut.activated.connect(rewind.click)
        forward_shortcut = QShortcut(QKeySequence("Alt+Right"), self)
        forward_shortcut.activated.connect(forward.click)
        self.shortcuts = (
            save_shortcut,
            play_shortcut,
            rewind_shortcut,
            forward_shortcut,
        )
        self.file_list.setCurrentRow(0)

    def _mark_dirty(self) -> None:
        self.dirty = True

    def _select(self, index: int) -> None:
        if index < 0 or index >= len(self.items):
            return
        if self.dirty and self.current_index >= 0:
            answer = QMessageBox.question(
                self,
                "Несохранённые изменения",
                "Сохранить изменения перед переходом?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if answer is QMessageBox.StandardButton.Cancel:
                with QSignalBlocker(self.file_list):
                    self.file_list.setCurrentRow(self.current_index)
                return
            if answer is QMessageBox.StandardButton.Save:
                self._save()
        self.current_index = index
        item = self.items[index]
        output = self.output_directory / f"{item.audio.stem}.txt"
        source = output if output.is_file() else item.supplied_reference
        metadata = self._metadata_path(item)
        reviewed = False
        if metadata.is_file():
            try:
                reviewed = bool(
                    json.loads(metadata.read_text(encoding="utf-8")).get("human_reviewed")
                )
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                reviewed = False
        with QSignalBlocker(self.editor):
            self.editor.setPlainText(source.read_text(encoding="utf-8").strip())
        with QSignalBlocker(self.reviewed):
            self.reviewed.setChecked(reviewed)
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(str(item.audio)))
        self.status.setText(
            f"{item.audio.name}\nИсточник: {item.supplied_reference.name}\n"
            "Сохраняется отдельная human-reviewed копия."
        )
        self.dirty = False

    def _metadata_path(self, item: ReviewItem) -> Path:
        return self.output_directory / f"{item.audio.stem}.review.json"

    def _save(self) -> None:
        if self.current_index < 0:
            return
        item = self.items[self.current_index]
        destination = self.output_directory / f"{item.audio.stem}.txt"
        temporary = destination.with_name(f".{destination.name}.tmp")
        temporary.write_text(self.editor.toPlainText().strip() + "\n", encoding="utf-8")
        temporary.replace(destination)
        metadata = {
            "audio": item.audio.name,
            "source_reference": item.supplied_reference.name,
            "human_reviewed": self.reviewed.isChecked(),
            "last_position_ms": self.player.position(),
        }
        metadata_path = self._metadata_path(item)
        metadata_temporary = metadata_path.with_name(f".{metadata_path.name}.tmp")
        metadata_temporary.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        metadata_temporary.replace(metadata_path)
        self.dirty = False
        self.status.setText(f"{item.audio.name}\nСохранено: {destination}")

    def _choose_output(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Папка для проверенных эталонов",
            str(self.output_directory),
        )
        if selected:
            self.output_directory = Path(selected).resolve()
            self.output_directory.mkdir(parents=True, exist_ok=True)
            self._select(self.current_index)

    def _toggle_playback(self) -> None:
        if self.player.playbackState() is QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _position_changed(self, milliseconds: int) -> None:
        if not self.slider.isSliderDown():
            self.slider.setValue(milliseconds)
        self.position.setText(
            f"{_time_label(milliseconds)} / {_time_label(self.player.duration())}"
        )

    def _duration_changed(self, milliseconds: int) -> None:
        self.slider.setRange(0, max(0, milliseconds))
        self._position_changed(self.player.position())

    def _playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        self.play_button.setText(
            "Пауза" if state is QMediaPlayer.PlaybackState.PlayingState else "Воспроизвести"
        )

    def closeEvent(self, event: Any) -> None:
        if not self.dirty:
            event.accept()
            return
        answer = QMessageBox.question(
            self,
            "Несохранённые изменения",
            "Закрыть без сохранения?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer is QMessageBox.StandardButton.Yes:
            event.accept()
        else:
            event.ignore()


def main() -> int:
    arguments = _arguments()
    application = QApplication.instance() or QApplication([])
    window = ReferenceReviewer(
        _items(arguments.audio_dir, arguments.references_dir, arguments.pattern),
        arguments.output_dir,
    )
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
