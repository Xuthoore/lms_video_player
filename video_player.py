import sys
import time
import shutil
import os
import sqlite3

from PyQt6.QtCore import (QRect, QRectF, QStandardPaths, QUrl, Qt, QTimer,
                          QPropertyAnimation, QObject, QEvent)
from PyQt6.QtGui import (QKeySequence, QShortcut, QIcon, QPainter,
                         QPainterPath, QColor, QPen, QRegion)
from PyQt6.QtWidgets import (
    QApplication, QGraphicsOpacityEffect, QMainWindow, QDialog, QVBoxLayout, QHBoxLayout,
    QGridLayout, QMessageBox, QSlider, QPushButton, QLabel, QTextEdit,
    QLineEdit, QComboBox, QSizePolicy, QWidget, QFileDialog
)
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_PATH = os.path.join(BASE_DIR, "icons", "app_icon.png")

# FFmpeg-бэкенд даёт качественное масштабирование видео.
# Переменную нужно задать до создания QApplication.
os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")

DB_PATH = os.path.join(BASE_DIR, "database")
VIDEO_DIR = os.path.join(BASE_DIR, "videos")

# шаг перемотки стрелками, секунды
SEEK_STEP = 10

# радиус скругления углов всех окон, пиксели
RADIUS = 12

BG_COLOR = "#1e1f22"

# У QWidget фон прозрачный: скруглённую заливку рисует RoundedWindow,
# а дочерние виджеты не перекрывают её квадратными фонами.
DARK_STYLE = """
    QWidget {
        background: transparent;
        color: #e0e0e0;
        font-size: 14px;
    }
    QLineEdit, QTextEdit, QComboBox {
        background-color: #2b2d31;
        border: 1px solid #3f4147;
        border-radius: 4px;
        padding: 4px;
    }
    QComboBox QAbstractItemView {
        background-color: #2b2d31;
        selection-background-color: #4a5568;
    }
    QPushButton {
        background-color: #3c3f45;
        color: white;
        border: none;
        border-radius: 4px;
        padding: 5px 14px;
    }
    QPushButton:hover {
        background-color: #555a63;
    }
    QPushButton:pressed {
        background-color: #2e3138;
    }
    QSlider::groove:horizontal {
        height: 6px;
        background: #3f4147;
        border-radius: 3px;
    }
    QSlider::handle:horizontal {
        width: 14px;
        margin: -5px 0;
        border-radius: 7px;
        background: #4f8cff;
    }
    QSlider::sub-page:horizontal {
        background: #4f8cff;
        border-radius: 3px;
    }
    QLabel {
        background: transparent;
    }
"""

TITLE_BAR_STYLE = """
    QPushButton {
        background: transparent;
        color: #e0e0e0;
        border: none;
        border-radius: 4px;
        font-size: 14px;
        padding: 0;
    }
    QPushButton:hover {
        background-color: #3c3f45;
    }
    QPushButton#btn_close:hover {
        background-color: #c0392b;
        color: white;
    }
"""


class RoundedMaskFilter(QObject):
    """Обрезает окно маской со скруглёнными углами.

    WA_TranslucentBackground на этой системе не работает (окно
    становилось полностью прозрачным), поэтому скругление делается
    обрезкой формы окна маской — она не зависит от прозрачности ОС.
    Минус метода: край без сглаживания.
    """

    def __init__(self, window):
        super().__init__(window)
        self._window = window
        window.installEventFilter(self)
        self.apply()

    def apply(self):
        w, h = self._window.width(), self._window.height()
        if w <= 0 or h <= 0:
            return
        path = QPainterPath()
        path.addRoundedRect(0, 0, w, h, RADIUS, RADIUS)
        # QRegion в PyQt6 не принимает QPainterPath напрямую —
        # конвертируем путь в полигон
        self._window.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def eventFilter(self, obj, event):
        # Resize покрывает растягивание и разворот, Show — первый показ
        if obj is self._window and event.type() in (
                QEvent.Type.Resize, QEvent.Type.Show):
            self.apply()
        return False


class RoundedWindow(QWidget):
    """Контейнер, заливающий фон окна и рисующий рамку по контуру.

    Работает в паре с RoundedMaskFilter: маска обрезает форму,
    paintEvent рисует фон. Не полагается на stylesheet — QPainter
    гарантирует заливку в любых конфигурациях стилей.
    """

    def paintEvent(self, event):
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        fill_path = QPainterPath()
        fill_path.addRoundedRect(0, 0, w, h, RADIUS, RADIUS)
        p.fillPath(fill_path, QColor(BG_COLOR))

        # рамка строится от прямоугольника, суженного на полпикселя,
        # чтобы линия не обрезалась краями маски
        border_rect = QRectF(0.5, 0.5, w - 1, h - 1)
        border_path = QPainterPath()
        border_path.addRoundedRect(border_rect, RADIUS, RADIUS)
        p.setPen(QPen(QColor("#3f4147"), 1))
        p.drawPath(border_path)


def setup_rounded_window(window):
    """Подготавливает окно к скруглённому виду и возвращает контейнер.

    У QMainWindow layout уже занят внутренней структурой: прямая
    QVBoxLayout на него молча игнорируется, и виджеты не размещаются.
    Поэтому главному окну контейнер ставится через setCentralWidget,
    а остальным окнам — обычным layout.
    """
    RoundedMaskFilter(window)
    container = RoundedWindow()
    if isinstance(window, QMainWindow):
        window.setCentralWidget(container)
    else:
        outer = QVBoxLayout(window)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(container)
    return container


# ширина зоны захвата краёв для растягивания, пиксели
RESIZE_MARGIN = 6


class WindowResizer(QObject):
    """Растягивание безрамочного окна за края и углы.

    Фильтр ставится на QApplication, а не на окно: мышь над дочерними
    виджетами (видео, слайдер) событие к самому окну не доводит.
    """

    L, R, T, B = 1, 2, 4, 8

    _CURSORS = {
        L: Qt.CursorShape.SizeHorCursor,
        R: Qt.CursorShape.SizeHorCursor,
        T: Qt.CursorShape.SizeVerCursor,
        B: Qt.CursorShape.SizeVerCursor,
        L | T: Qt.CursorShape.SizeFDiagCursor,
        R | B: Qt.CursorShape.SizeFDiagCursor,
        R | T: Qt.CursorShape.SizeBDiagCursor,
        L | B: Qt.CursorShape.SizeBDiagCursor,
    }

    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self._edges = 0
        self._active = False
        self._start_geo = None
        self._start_pos = None
        QApplication.instance().installEventFilter(self)

    def _belongs(self, obj):
        # фильтр уровня приложения получает события и не-виджетов
        # (например, QVideoSink) — их пропускаем
        if not isinstance(obj, QWidget):
            return False
        w = obj
        while w is not None:
            if w is self._window:
                return True
            if w.isWindow():
                break   # дальше родителей-виджетов нет
            w = w.parentWidget()
        return False

    def _edges_at(self, gpos):
        if self._window.isMaximized():
            return 0
        rect = self._window.frameGeometry()
        m = RESIZE_MARGIN
        edges = 0
        if gpos.x() <= rect.left() + m:
            edges |= self.L
        if gpos.x() >= rect.right() - m:
            edges |= self.R
        if gpos.y() <= rect.top() + m:
            edges |= self.T
        if gpos.y() >= rect.bottom() - m:
            edges |= self.B
        return edges

    def _cursor_for(self, edges):
        return self._CURSORS.get(edges, Qt.CursorShape.ArrowCursor)

    def eventFilter(self, obj, event):
        if not self._belongs(obj):
            return False
        et = event.type()

        if et == QEvent.Type.MouseButtonPress and \
                event.button() == Qt.MouseButton.LeftButton:
            edges = self._edges_at(event.globalPosition().toPoint())
            if edges:
                self._active = True
                self._edges = edges
                self._start_geo = self._window.frameGeometry()
                self._start_pos = event.globalPosition().toPoint()
                # grabMouse: тянуть можно и за пределами окна
                self._window.grabMouse()
                return True
            return False

        if et == QEvent.Type.MouseButtonRelease:
            if self._active:
                self._active = False
                self._window.releaseMouse()
                return True
            return False

        if et == QEvent.Type.MouseMove:
            gpos = event.globalPosition().toPoint()
            if self._active:
                self._apply(gpos)
                return True
            self._window.setCursor(self._cursor_for(self._edges_at(gpos)))
            return False

        return False

    def _apply(self, gpos):
        dx = gpos.x() - self._start_pos.x()
        dy = gpos.y() - self._start_pos.y()
        geo = QRect(self._start_geo)
        min_w = max(self._window.minimumWidth(), 300)
        min_h = max(self._window.minimumHeight(), 200)
        e = self._edges
        if e & self.L:
            geo.setLeft(min(geo.right() - min_w, geo.left() + dx))
        elif e & self.R:
            geo.setWidth(max(min_w, geo.width() + dx))
        if e & self.T:
            geo.setTop(min(geo.bottom() - min_h, geo.top() + dy))
        elif e & self.B:
            geo.setHeight(max(min_h, geo.height() + dy))
        self._window.setGeometry(geo)


class Toast(QWidget):
    """Плашка-уведомление внутри якорного окна.

    Дочерний виджет, а не отдельное окно: живёт в координатах
    родителя, двигается и закрывается вместе с ним. 3 секунды висит,
    затем растворяется за секунду.
    """

    TOAST_RADIUS = 10
    PAUSE_MS = 3000
    FADE_MS = 1000

    def __init__(self, text, anchor):
        super().__init__(anchor)   # родитель — окно, к которому привязана
        self._anchor = anchor

        lbl = QLabel(text)
        lbl.setStyleSheet("color: #e0e0e0; font-size: 13px;")
        lbl.adjustSize()
        self.setFixedSize(lbl.width() + 32, 40)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.addWidget(lbl, 0, Qt.AlignmentFlag.AlignCenter)

        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(1.0)
        self.setGraphicsEffect(self._effect)
        self._anim = None

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        fill_path = QPainterPath()
        fill_path.addRoundedRect(0, 0, w, h,
                                 self.TOAST_RADIUS, self.TOAST_RADIUS)
        p.fillPath(fill_path, QColor("#2b2d31"))

        border_rect = QRectF(0.5, 0.5, w - 1, h - 1)
        border_path = QPainterPath()
        border_path.addRoundedRect(border_rect,
                                   self.TOAST_RADIUS, self.TOAST_RADIUS)
        p.setPen(QPen(QColor("#3f4147"), 1))
        p.drawPath(border_path)

    def popup(self):
        """Показывает плашку у нижнего края родительского окна."""
        parent = self.parentWidget()
        if parent is not None:
            pw, ph = parent.width(), parent.height()
            # координаты относительно родителя, с зажимом внутри окна
            x = max(4, (pw - self.width()) // 2)
            y = ph - self.height() - 8
        else:
            geo = QApplication.primaryScreen().availableGeometry()
            x = geo.center().x() - self.width() // 2
            y = geo.bottom() - self.height() - 24
        self.move(x, y)
        self.show()
        self.raise_()
        QTimer.singleShot(self.PAUSE_MS, self._start_fade)

    def _start_fade(self):
        self._anim = QPropertyAnimation(self._effect, b"opacity", self)
        self._anim.setDuration(self.FADE_MS)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.finished.connect(self.hide)
        self._anim.finished.connect(self.deleteLater)
        self._anim.start()


class TitleBar(QWidget):
    """Кастомная панель заголовка с перетаскиванием окна.

    with_maximize — рисовать ли кнопку разворота (у главного окна
    она отключена, у окна видео включена).
    """

    def __init__(self, window, title, with_maximize=False):
        super().__init__(window)
        self._window = window
        self._drag_pos = None
        self.setFixedHeight(36)
        # фон прозрачный — скруглённые углы рисует RoundedWindow
        self.setStyleSheet(
            "background: transparent; border-bottom: 1px solid #2b2d31;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 4, 0)
        layout.setSpacing(2)

        if os.path.exists(ICON_PATH):
            icon_label = QLabel()
            icon_label.setPixmap(QIcon(ICON_PATH).pixmap(18, 18))
            layout.addWidget(icon_label)

        lbl_title = QLabel(title)
        lbl_title.setStyleSheet(
            "color: #e0e0e0; font-weight: bold; background: transparent;")
        layout.addWidget(lbl_title)
        layout.addStretch(1)

        btn_style = TITLE_BAR_STYLE

        btn_min = QPushButton("\u2013")
        btn_min.setFixedSize(40, 28)
        btn_min.setStyleSheet(btn_style)
        btn_min.clicked.connect(window.showMinimized)
        layout.addWidget(btn_min)

        if with_maximize:
            self.btn_max = QPushButton("\u25A1")
            self.btn_max.setFixedSize(40, 28)
            self.btn_max.setStyleSheet(btn_style)
            self.btn_max.clicked.connect(self.toggle_maximize)
            layout.addWidget(self.btn_max)

        btn_close = QPushButton("\u2715")
        btn_close.setObjectName("btn_close")
        btn_close.setFixedSize(40, 28)
        btn_close.setStyleSheet(btn_style)
        btn_close.clicked.connect(window.close)
        layout.addWidget(btn_close)

    def toggle_maximize(self):
        if not hasattr(self, "btn_max"):
            return
        if self._window.isMaximized():
            self._window.showNormal()
            self.btn_max.setText("\u25A1")
        else:
            self._window.showMaximized()
            self.btn_max.setText("\u29C9")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - \
                self._window.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and \
                event.buttons() & Qt.MouseButton.LeftButton:
            # перетаскивание развёрнутого окна возвращает его в обычный режим
            if self._window.isMaximized():
                self.toggle_maximize()
            self._window.move(
                event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if hasattr(self, "btn_max"):
            self.toggle_maximize()
        super().mouseDoubleClickEvent(event)


class ConfirmDialog(QDialog):
    """Окно подтверждения в стилистике приложения — вместо QMessageBox."""

    def __init__(self, title, message):
        super().__init__(None)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setModal(True)
        self.resize(380, 170)

        container = setup_rounded_window(self)

        inner = QVBoxLayout(container)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(0)

        inner.addWidget(TitleBar(self, title))

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(16, 14, 16, 14)
        body_layout.setSpacing(14)

        lbl = QLabel(message)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("background: transparent;")
        body_layout.addWidget(lbl, 1)

        buttons = QHBoxLayout()
        self.btn_no = QPushButton("Оставить")
        self.btn_no.setMinimumWidth(110)
        self.btn_yes = QPushButton("Удалить")
        self.btn_yes.setMinimumWidth(110)
        # красная кнопка — акцент опасного действия
        self.btn_yes.setStyleSheet("""
            QPushButton {
                background-color: #c0392b;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 5px 14px;
            }
            QPushButton:hover {
                background-color: #e74c3c;
            }
            QPushButton:pressed {
                background-color: #a93226;
            }
        """)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_no)
        buttons.addWidget(self.btn_yes)
        body_layout.addLayout(buttons)

        inner.addWidget(body, 1)

        self.btn_yes.clicked.connect(self.accept)
        self.btn_no.clicked.connect(self.reject)

    def ask(self):
        return self.exec() == QDialog.DialogCode.Accepted


class VideoWindow(QDialog):
    """Окно видеоплеера: полоса времени, пауза, запоминание позиции."""

    def __init__(self, path, video_id, conn, cursor, start_pos=0.0):
        super().__init__(None)   # без родителя: независимое окно
        self.video_id = video_id
        self.conn = conn
        self.cursor = cursor
        self._resume = max(float(start_pos or 0.0), 0.0)
        self._duration_ms = 0
        self._seeking = False
        self._need_seek = self._resume > 0
        self._finished = False

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle(os.path.basename(path))
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))
        self.resize(1000, 640)

        # ссылка хранится: фильтр на QApplication должен жить,
        # пока живо окно
        self._resizer = WindowResizer(self)
        self.setMinimumSize(400, 300)

        container = setup_rounded_window(self)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)

        self.title_bar = TitleBar(
            self, os.path.basename(path), with_maximize=True)
        layout.addWidget(self.title_bar)

        self.video_widget = QVideoWidget()
        self.video_widget.setSizePolicy(QSizePolicy.Policy.Expanding,
                                        QSizePolicy.Policy.Expanding)
        layout.addWidget(self.video_widget, 1)

        controls = QHBoxLayout()
        controls.setContentsMargins(6, 4, 6, 4)
        controls.setSpacing(8)

        self.btn_pause = QPushButton("Пауза")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.label_time = QLabel("0:00 / 0:00")
        self.label_time.setMinimumWidth(90)
        self.label_time.setAlignment(Qt.AlignmentFlag.AlignCenter)

        controls.addWidget(self.btn_pause)
        controls.addWidget(self.slider, 1)
        controls.addWidget(self.label_time)
        layout.addLayout(controls)

        self.audio_output = QAudioOutput()
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        self.player.setSource(QUrl.fromLocalFile(path))
        self.audio_output.setVolume(80)

        self.player.durationChanged.connect(self.on_duration_changed)
        self.player.positionChanged.connect(self.on_position_changed)

        self.slider.sliderPressed.connect(self.on_slider_pressed)
        self.slider.sliderReleased.connect(self.on_slider_released)
        self.slider.sliderMoved.connect(self.on_slider_moved)

        self.btn_pause.clicked.connect(self.toggle_pause)
        self.player.playbackStateChanged.connect(self.on_state_changed)

        QShortcut(QKeySequence(Qt.Key.Key_Space),
                  self).activated.connect(self.toggle_pause)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self).activated.connect(
            lambda: self.seek_by(-SEEK_STEP))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self).activated.connect(
            lambda: self.seek_by(SEEK_STEP))

        self.player.mediaStatusChanged.connect(self.on_media_status)

    def showEvent(self, event):
        super().showEvent(event)
        self.player.play()

    def closeEvent(self, event):
        self.save_position()
        self.player.stop()
        # сброс источников освобождает файл: иначе Windows держит его
        # занятым, и последующее удаление падает с WinError 32
        self.player.setVideoOutput(None)
        self.player.setSource(QUrl())
        super().closeEvent(event)

    def save_position(self):
        if self._finished:
            return
        seconds = self.player.position() / 1000.0
        self.cursor.execute(
            "UPDATE FileLink SET curtimeline = ? WHERE id = ?",
            (seconds, self.video_id)
        )
        self.conn.commit()

    def on_duration_changed(self, duration_ms):
        self._duration_ms = duration_ms
        self.slider.setRange(0, max(int(duration_ms), 1))

    def on_position_changed(self, pos_ms):
        if self._seeking:
            return
        self.slider.blockSignals(True)   # слайдер не должен двигать плеер
        self.slider.setValue(int(pos_ms))
        self.slider.blockSignals(False)
        self.label_time.setText(
            self.format_time(pos_ms) + " / " +
            self.format_time(self._duration_ms)
        )

    def on_state_changed(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.btn_pause.setText("Пауза")
        else:
            self.btn_pause.setText("Продолжить")

    def on_media_status(self, status):
        # разовая перемотка на сохранённую позицию после загрузки видео
        if status == QMediaPlayer.MediaStatus.LoadedMedia and self._need_seek:
            self._need_seek = False
            self.player.setPosition(int(self._resume * 1000))
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._finished = True
            self.cursor.execute(
                "UPDATE FileLink SET curtimeline = 0 WHERE id = ?",
                (self.video_id,)
            )
            self.conn.commit()
            self.slider.blockSignals(True)
            self.slider.setValue(0)
            self.slider.blockSignals(False)
            self.btn_pause.setText("Продолжить")

    def on_slider_pressed(self):
        self._seeking = True

    def on_slider_moved(self, value):
        self.label_time.setText(
            self.format_time(value) + " / " +
            self.format_time(self._duration_ms)
        )

    def on_slider_released(self):
        self.player.setPosition(self.slider.value())
        self._seeking = False
        self._finished = False

    def seek_by(self, seconds):
        new_pos = self.player.position() + seconds * 1000
        new_pos = max(0, min(new_pos, self._duration_ms))
        self.player.setPosition(int(new_pos))
        self._finished = False   # отмотка снимает признак «досмотрено»

    def toggle_pause(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    @staticmethod
    def format_time(ms):
        s = int(ms // 1000)
        return f"{s // 60}:{s % 60:02d}"


class UploadDialog(QDialog):
    """Окно загрузки: выбор файла и ввод названия."""

    def __init__(self):
        super().__init__(None)
        self.source_path = None

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle("Загрузка видео")
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))
        self.resize(420, 200)

        container = setup_rounded_window(self)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)

        layout.addWidget(TitleBar(self, "Загрузка видео"))

        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(10, 8, 10, 10)
        inner_layout.setSpacing(8)

        file_row = QHBoxLayout()
        self.btn_choose = QPushButton("Выбрать файл…")
        self.label_file = QLabel("Файл не выбран")
        self.label_file.setStyleSheet("color: #9a9a9a;")
        file_row.addWidget(self.btn_choose)
        file_row.addWidget(self.label_file, 1)
        inner_layout.addLayout(file_row)

        inner_layout.addWidget(QLabel("Название видео:"))
        self.edit_name = QLineEdit()
        self.edit_name.setPlaceholderText("Например, Satisfactory")
        inner_layout.addWidget(self.edit_name)

        buttons = QHBoxLayout()
        self.btn_upload = QPushButton("Загрузить")
        self.btn_upload.setEnabled(False)
        self.btn_cancel = QPushButton("Отмена")
        buttons.addStretch(1)
        buttons.addWidget(self.btn_cancel)
        buttons.addWidget(self.btn_upload)
        inner_layout.addLayout(buttons)

        layout.addWidget(inner, 1)

        self.btn_choose.clicked.connect(self.choose_file)
        self.btn_upload.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)

    def choose_file(self):
        # стартовая папка диалога — домашняя папка пользователя
        start_dir = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DownloadLocation)

        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите видеофайл", start_dir,
            "Видеофайлы (*.mp4 *.avi *.mkv *.mov *.webm);;Все файлы (*.*)"
        )

        if not path:
            return
        self.source_path = path
        self.label_file.setText(os.path.basename(path))
        self.label_file.setStyleSheet("")
        # пустое название подставляем из имени файла
        if not self.edit_name.text().strip():
            self.edit_name.setText(os.path.splitext(os.path.basename(path))[0])
        self.btn_upload.setEnabled(True)

    def get_data(self):
        """Возвращает (исходный путь, название) или None при пустой форме."""
        name = self.edit_name.text().strip()
        if not self.source_path or not name:
            return None
        return self.source_path, name


class VideoPlayer(QMainWindow):
    """Главное окно: список видео, поиск, запуск, загрузка и удаление."""

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle("Проигрыватель видео")
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))
        self.resize(460, 220)

        container = setup_rounded_window(self)

        inner = QVBoxLayout(container)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(0)

        inner.addWidget(TitleBar(self, "Проигрыватель видео"))

        body = QWidget()
        root = QHBoxLayout(body)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        inner.addWidget(body, 1)

        left = QGridLayout()
        left.setSpacing(8)

        self.Search_video = QLabel("Фильтр видео:")
        self.Search_video.setFixedHeight(24)
        left.addWidget(self.Search_video, 0, 0)

        self.btn = QPushButton("Запуск")
        self.btn.setFixedHeight(24)
        left.addWidget(self.btn, 0, 1)

        self.Input_search = QTextEdit()
        self.Input_search.setMaximumHeight(60)
        self.Input_search.setPlaceholderText("Введите название видео…")
        left.addWidget(self.Input_search, 1, 0, 1, 2)
        left.setRowStretch(1, 1)

        self.comboBox = QComboBox()
        left.addWidget(self.comboBox, 2, 0, 1, 2)
        left.setRowStretch(2, 1)

        right = QVBoxLayout()
        right.setSpacing(8)

        self.btn_add = QPushButton("+")
        self.btn_add.setToolTip("Загрузить новое видео")
        self.btn_add.setSizePolicy(QSizePolicy.Policy.Expanding,
                                   QSizePolicy.Policy.Expanding)
        self.btn_add.setStyleSheet("QPushButton { font-size: 20px; }")

        self.btn_delete = QPushButton("\U0001F5D1")
        self.btn_delete.setToolTip("Удалить выбранное видео")
        self.btn_delete.setSizePolicy(QSizePolicy.Policy.Expanding,
                                      QSizePolicy.Policy.Expanding)
        self.btn_delete.setStyleSheet("QPushButton { font-size: 16px; }")

        right.addWidget(self.btn_add, 1)
        right.addWidget(self.btn_delete, 1)

        root.addLayout(left, 1)
        root.addLayout(right, 0)

        self.conn = sqlite3.connect(DB_PATH)
        self.cursor = self.conn.cursor()

        # {videoname: (id, filelocate, curtimeline)}
        self._results = {}
        # живые плашки — иначе их соберёт сборщик мусора
        self._toasts = []

        self.Input_search.textChanged.connect(self.on_search_changed)
        self.btn.clicked.connect(self.on_play)
        self.btn_add.clicked.connect(self.on_upload)
        self.btn_delete.clicked.connect(self.on_delete)

        self.load_all()

    def show_toast(self, text):
        toast = Toast(text, self)
        self._toasts.append(toast)
        toast.destroyed.connect(
            lambda: self._toasts.remove(toast) if toast in self._toasts else None)
        toast.popup()

    def query_videos(self, text):
        self.cursor.execute(
            "SELECT id, videoname, filelocate, curtimeline FROM FileLink "
            "WHERE videoname LIKE ? ORDER BY videoname",
            (f"%{text}%",)
        )
        return {row[1]: (row[0], row[2], row[3] or 0.0)
                for row in self.cursor.fetchall()}

    def load_all(self):
        self._results = self.query_videos("")
        self._reload_combo(list(self._results.keys()))

    def _reload_combo(self, names):
        self.comboBox.clear()
        if not names:
            self.comboBox.addItem("нет")
            self.comboBox.setItemData(0, 0, Qt.ItemDataRole.UserRole - 1)
        else:
            self.comboBox.addItems(names)

    def on_search_changed(self):
        text = self.Input_search.toPlainText().strip()
        self._results = self.query_videos(text)
        self._reload_combo(list(self._results.keys()))

    def on_upload(self):
        dialog = UploadDialog()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        data = dialog.get_data()
        if not data:
            self.show_toast("Укажите файл и название")
            return
        source_path, name = data

        # если файл с таким названием уже есть — добавляем номер
        ext = os.path.splitext(source_path)[1] or ".mp4"
        stored_name = name + ext
        dest_path = os.path.join(VIDEO_DIR, stored_name)
        counter = 1
        while os.path.exists(dest_path):
            stored_name = f"{name} ({counter}){ext}"
            dest_path = os.path.join(VIDEO_DIR, stored_name)
            counter += 1

        try:
            os.makedirs(VIDEO_DIR, exist_ok=True)
            shutil.copy2(source_path, dest_path)
        except OSError as e:
            QMessageBox.critical(self, "Загрузка видео",
                                 f"Не удалось скопировать файл:\n{e}")
            return

        self.cursor.execute(
            "INSERT INTO FileLink (videoname, filelocate, curtimeline) "
            "VALUES (?, ?, ?)",
            (name, stored_name, 0.0)
        )
        self.conn.commit()

        self.load_all()
        self.show_toast(f"Видео «{name}» добавлено")

    def on_delete(self):
        name = self.comboBox.currentText()
        item = self._results.get(name)
        if not item:
            self.show_toast("Выберите видео из списка")
            return

        confirm = ConfirmDialog(
            "Удаление видео",
            f"Удалить видео «{name}»?\nФайл также будет стёрт с диска."
        )
        if not confirm.ask():
            return

        video_id, rel_path, _ = item
        path = os.path.join(VIDEO_DIR, rel_path.strip())

        self.cursor.execute("DELETE FROM FileLink WHERE id = ?", (video_id,))
        self.conn.commit()

        # файл может держаться плеером — несколько попыток с паузой
        if os.path.exists(path):
            deleted = False
            for _ in range(3):
                try:
                    os.remove(path)
                    deleted = True
                    break
                except OSError:
                    QApplication.processEvents()
                    time.sleep(0.1)
            if deleted:
                self.show_toast(f"Видео «{name}» удалено")
            else:
                QMessageBox.warning(
                    self, "Удаление видео",
                    f"Запись удалена из базы, но файл не стёрт:\n{path}"
                )
        else:
            self.show_toast(f"Видео «{name}» удалено")

        self.load_all()

    def on_play(self):
        name = self.comboBox.currentText()
        item = self._results.get(name)
        if not item:
            self.show_toast("Выберите видео из списка")
            return
        video_id, rel_path, start_pos = item
        rel_path = rel_path.strip()
        path = os.path.join(VIDEO_DIR, rel_path)
        if not os.path.exists(path):
            QMessageBox.warning(self, "Проигрыватель видео",
                                f"Файл не найден:\n{path}")
            return
        self.win = VideoWindow(path, video_id, self.conn, self.cursor,
                               start_pos)
        # после закрытия окна подтягиваем свежую позицию из базы
        self.win.finished.connect(lambda: self.update_video_position(name))
        self.win.show()

    def update_video_position(self, name):
        self.cursor.execute(
            "SELECT id, videoname, filelocate, curtimeline "
            "FROM FileLink WHERE videoname = ?",
            (name,)
        )
        row = self.cursor.fetchone()
        if row:
            self._results[name] = (row[0], row[2], row[3] or 0.0)

    def closeEvent(self, event):
        self.conn.close()
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLE)
    # иконка на уровне приложения наследуется всеми окнами и QMessageBox
    if os.path.exists(ICON_PATH):
        app.setWindowIcon(QIcon(ICON_PATH))
    window = VideoPlayer()
    window.show()
    sys.exit(app.exec())
