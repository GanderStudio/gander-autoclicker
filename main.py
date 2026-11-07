
import os
import shlex
import socket
import stat
import sys

from pathlib import Path

# =====================================================
# LOCAL SHORTCUT CLIENT
# =====================================================

def get_socket_path():
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")

    if not runtime_dir:
        raise RuntimeError("XDG_RUNTIME_DIR is unavailable.")

    return os.path.join(
        runtime_dir,
        "gander-autoclicker.sock",
    )


def send_toggle():
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1)
            client.connect(get_socket_path())
            client.sendall(b"toggle\n")

        return 0

    except (OSError, RuntimeError):
        return 1


# Desktops run this lightweight client when the shortcut is pressed.
# No Qt application or virtual mouse is created in client mode.

if __name__ == "__main__" and "--toggle" in sys.argv:
    sys.exit(send_toggle())


# =====================================================
# APPLICATION IMPORTS
# =====================================================

from PySide6.QtCore import Qt, QTimer, QSettings, Slot
from PySide6.QtGui import QColor, QKeySequence, QPalette
from PySide6.QtNetwork import QLocalServer

from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from linux_mouse import LinuxMouseBackend

from linux_hotkeys import (
    Hotkey,
    ManualBackend,
    ShortcutError,
    start_shortcut,
)


# =====================================================
# GLOBAL SHORTCUT
# =====================================================

DEFAULT_HOTKEY = "F6"

# Command that desktops run when the shortcut is pressed. It only sends
# "toggle" to the running app through the local socket.

TOGGLE_COMMAND = shlex.join([
    sys.executable,
    str(Path(__file__).resolve()),
    "--toggle",
])


# =====================================================
# UI HELPERS
# =====================================================

# Friendlier names on the shortcut key caps.
KEY_LABELS = {
    "Meta": "Super",
    "Return": "Enter",
}


def section_label(text):
    label = QLabel(text)
    label.setObjectName("sectionLabel")
    return label


def set_style_property(widget, name, value):
    """Set a property used by style.qss and re-apply the style."""

    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def apply_theme(app):
    """
    Fusion + a dark palette gives the same look on every distro and
    desktop; style.qss adds the Gander Studio details on top.
    """

    app.setStyle("Fusion")

    palette = QPalette()

    colors = {
        QPalette.ColorRole.Window: "#11130D",
        QPalette.ColorRole.WindowText: "#F3E7D0",
        QPalette.ColorRole.Base: "#202417",
        QPalette.ColorRole.AlternateBase: "#1A1D14",
        QPalette.ColorRole.Text: "#F3E7D0",
        QPalette.ColorRole.Button: "#202417",
        QPalette.ColorRole.ButtonText: "#F3E7D0",
        QPalette.ColorRole.Highlight: "#D98C3F",
        QPalette.ColorRole.HighlightedText: "#11130D",
        QPalette.ColorRole.ToolTipBase: "#202417",
        QPalette.ColorRole.ToolTipText: "#F3E7D0",
        QPalette.ColorRole.PlaceholderText: "#8A8D72",
        QPalette.ColorRole.Link: "#D98C3F",
    }

    for role, color in colors.items():
        palette.setColor(role, QColor(color))

    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            role,
            QColor("#5A5D4A"),
        )

    app.setPalette(palette)

    # Load the visual details from style.qss.

    style_path = Path(__file__).with_name("style.qss")

    if style_path.exists():
        assets = Path(__file__).with_name("assets").as_posix()

        app.setStyleSheet(
            style_path.read_text(encoding="utf-8")
            .replace("@ASSETS@", assets)
        )


# =====================================================
# MAIN WINDOW
# =====================================================

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()

        self.is_running = False
        self.is_configuring_shortcut = False
        self.mouse_backend = None

        self.setWindowTitle("Gander AutoClicker")
        self.resize(440, 460)
        self.setMinimumWidth(400)

        # -------------------------------------------------
        # Settings
        # -------------------------------------------------

        self.settings = QSettings(
            "Gander Studio",
            "Gander AutoClicker",
        )

        # -------------------------------------------------
        # Main container
        # -------------------------------------------------

        container = QWidget()

        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(15)

        self.setCentralWidget(container)

        # -------------------------------------------------
        # Header
        # -------------------------------------------------

        eyebrow = QLabel("GANDER STUDIO  /  UTILITY")
        eyebrow.setObjectName("eyebrow")

        title = QLabel("Gander AutoClicker")
        title.setObjectName("title")

        subtitle = QLabel(
            "Simple, lightweight mouse automation."
        )
        subtitle.setObjectName("subtitle")

        header = QVBoxLayout()
        header.setSpacing(4)
        header.addWidget(eyebrow)
        header.addWidget(title)
        header.addWidget(subtitle)

        layout.addLayout(header)
        layout.addSpacing(4)

        # -------------------------------------------------
        # Settings panel
        # -------------------------------------------------

        panel = QFrame()
        panel.setObjectName("panel")

        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(18, 16, 18, 18)
        panel_layout.setSpacing(12)

        # Clicking section

        panel_layout.addWidget(section_label("CLICKING"))

        form = QGridLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        form.setColumnStretch(0, 1)

        self.cps_input = QSpinBox()
        self.cps_input.setRange(1, 500)
        self.cps_input.setSuffix("  CPS")
        self.cps_input.setFixedWidth(150)

        self.cps_input.setValue(
            self.settings.value("cps", 10, type=int)
        )

        self.mouse_button_input = QComboBox()
        self.mouse_button_input.setFixedWidth(150)

        self.mouse_button_input.addItems([
            "Left",
            "Right",
            "Middle",
        ])

        self.mouse_button_input.setCurrentText(
            self.settings.value("mouse_button", "Left", type=str)
        )

        for row, (text, field) in enumerate([
            ("Clicks per second", self.cps_input),
            ("Mouse button", self.mouse_button_input),
        ]):
            label = QLabel(text)
            label.setObjectName("fieldLabel")

            form.addWidget(label, row, 0, Qt.AlignmentFlag.AlignVCenter)
            form.addWidget(field, row, 1, Qt.AlignmentFlag.AlignRight)

        panel_layout.addLayout(form)

        divider = QFrame()
        divider.setObjectName("divider")

        panel_layout.addSpacing(4)
        panel_layout.addWidget(divider)
        panel_layout.addSpacing(4)

        # Global shortcut section

        panel_layout.addWidget(section_label("GLOBAL SHORTCUT"))

        shortcut_row = QHBoxLayout()
        shortcut_row.setSpacing(10)

        # Filled with key caps by show_shortcut().
        self.keys_layout = QHBoxLayout()
        self.keys_layout.setSpacing(5)

        self.backend_tag = QLabel()
        self.backend_tag.setObjectName("backendTag")

        self.configure_button = QPushButton("Change")
        self.configure_button.setObjectName("secondary")
        self.configure_button.setEnabled(False)
        self.configure_button.setCursor(
            Qt.CursorShape.PointingHandCursor
        )

        shortcut_row.addLayout(self.keys_layout)
        shortcut_row.addWidget(self.backend_tag)
        shortcut_row.addStretch()
        shortcut_row.addWidget(self.configure_button)

        panel_layout.addLayout(shortcut_row)

        self.hint_label = QLabel()
        self.hint_label.setObjectName("hint")
        self.hint_label.setWordWrap(True)
        self.hint_label.hide()

        self.hint_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        panel_layout.addWidget(self.hint_label)

        layout.addWidget(panel)

        # -------------------------------------------------
        # Start / Stop
        # -------------------------------------------------

        self.state_button = QPushButton("Start clicking")
        self.state_button.setObjectName("primary")
        self.state_button.setCursor(
            Qt.CursorShape.PointingHandCursor
        )

        layout.addWidget(self.state_button)

        layout.addStretch()

        # -------------------------------------------------
        # Status and footer
        # -------------------------------------------------

        self.status_label = QLabel()
        self.status_label.setObjectName("status")

        footer = QLabel("LINUX BETA")
        footer.setObjectName("footer")

        bottom = QHBoxLayout()
        bottom.addWidget(self.status_label)
        bottom.addStretch()
        bottom.addWidget(footer)

        layout.addLayout(bottom)

        self.set_status("Ready")

        # Don't start with the CPS field focused.
        self.state_button.setFocus()

        # -------------------------------------------------
        # Clicking timer
        # -------------------------------------------------

        self.click_timer = QTimer(self)

        self.click_timer.setTimerType(
            Qt.TimerType.PreciseTimer
        )

        self.click_timer.timeout.connect(
            self.timer_tick
        )

        self.update_timer(
            self.cps_input.value()
        )

        # -------------------------------------------------
        # Mouse backend
        # -------------------------------------------------

        try:
            self.mouse_backend = LinuxMouseBackend()

        except Exception as error:
            self.state_button.setEnabled(False)

            self.set_status(
                f"Mouse input unavailable: {error}",
                "error",
            )

        # -------------------------------------------------
        # Local shortcut server
        # -------------------------------------------------

        self.socket_path = get_socket_path()

        self.shortcut_server = QLocalServer(self)

        self.shortcut_server.setSocketOptions(
            QLocalServer.SocketOption.UserAccessOption
        )

        self.prepare_shortcut_socket()

        self.shortcut_server.newConnection.connect(
            self.accept_shortcut_connection
        )

        if not self.shortcut_server.listen(
            self.socket_path
        ):
            raise RuntimeError(
                self.shortcut_server.errorString()
            )

        # -------------------------------------------------
        # Configure global shortcut
        # -------------------------------------------------

        self.shortcut_backend = None
        self.hotkey = None

        self.start_global_shortcut()

        # -------------------------------------------------
        # Signal connections
        # -------------------------------------------------

        self.state_button.clicked.connect(
            self.toggle_autoclicker
        )

        self.configure_button.clicked.connect(
            self.configure_shortcut
        )

        self.cps_input.valueChanged.connect(
            self.update_timer
        )

        self.cps_input.valueChanged.connect(
            self.save_cps
        )

        self.mouse_button_input.currentTextChanged.connect(
            self.save_mouse_button
        )

    # =====================================================
    # LOCAL SOCKET
    # =====================================================

    def prepare_shortcut_socket(self):
        if not os.path.lexists(self.socket_path):
            return

        info = os.lstat(self.socket_path)

        if not stat.S_ISSOCK(info.st_mode):
            raise RuntimeError(
                "The shortcut socket path is not a socket."
            )

        if info.st_uid != os.getuid():
            raise RuntimeError(
                "The shortcut socket belongs to another user."
            )

        # Never remove a socket belonging to a running instance.

        with socket.socket(
            socket.AF_UNIX,
            socket.SOCK_STREAM,
        ) as client:

            client.settimeout(0.5)

            try:
                client.connect(self.socket_path)

            except ConnectionRefusedError:
                # Socket left behind by a crashed application.

                QLocalServer.removeServer(
                    self.socket_path
                )

            except FileNotFoundError:
                pass

            else:
                raise RuntimeError(
                    "Gander AutoClicker is already running."
                )

    def accept_shortcut_connection(self):
        while self.shortcut_server.hasPendingConnections():

            client = (
                self.shortcut_server.nextPendingConnection()
            )

            client.readyRead.connect(
                lambda client=client:
                self.read_shortcut_message(client)
            )

            client.disconnected.connect(
                client.deleteLater
            )

            if client.bytesAvailable():
                self.read_shortcut_message(client)

    def read_shortcut_message(self, client):
        message = bytes(
            client.readAll()
        )

        if (
            message == b"toggle\n"
            and not self.is_configuring_shortcut
        ):
            self.toggle_autoclicker()

        client.disconnectFromServer()

    # =====================================================
    # START / STOP
    # =====================================================

    @Slot()
    def toggle_autoclicker(self):
        if self.mouse_backend is None:
            return

        self.is_running = not self.is_running

        if self.is_running:
            self.click_timer.start()
        else:
            self.click_timer.stop()

        self.state_button.setText(
            "Stop clicking" if self.is_running else "Start clicking"
        )

        set_style_property(
            self.state_button,
            "running",
            self.is_running,
        )

        self.update_running_status()

    def update_running_status(self):
        if self.is_running:
            self.set_status(
                f"Clicking · {self.cps_input.value()} CPS",
                "running",
            )
        else:
            self.set_status("Ready")

    def set_status(self, text, state="ready"):
        self.status_label.setText(f"●  {text}")
        set_style_property(self.status_label, "state", state)

    # =====================================================
    # MOUSE CLICKING
    # =====================================================

    def timer_tick(self):
        button = (
            self.mouse_button_input.currentText()
        )

        self.mouse_backend.click(button)

    # =====================================================
    # CONFIGURE GLOBAL SHORTCUT
    # =====================================================

    def start_global_shortcut(self):
        try:
            requested = Hotkey.parse(
                self.settings.value(
                    "hotkey",
                    DEFAULT_HOTKEY,
                    type=str,
                )
            )
        except ShortcutError:
            requested = Hotkey.parse(DEFAULT_HOTKEY)

        backend, active, notes = start_shortcut(
            requested,
            TOGGLE_COMMAND,
            self.on_shortcut_activated,
            self.on_shortcut_status,
        )

        self.shortcut_backend = backend
        self.hotkey = active

        if active:
            self.settings.setValue("hotkey", active.text())

        self.show_shortcut(
            active.text() if active else "Not set",
            notes[-1] if notes and not active else "",
        )

        self.configure_button.setEnabled(
            backend.configurable
            or hasattr(backend, "open_settings")
        )

        if not backend.configurable:
            self.configure_button.setText("Settings")

        if isinstance(backend, ManualBackend):
            self.configure_button.hide()

    def show_shortcut(self, text, note=""):
        # Remove the previous key caps.
        while self.keys_layout.count():
            item = self.keys_layout.takeAt(0)

            if item.widget():
                item.widget().deleteLater()

        # Show a real shortcut as key caps, anything else as text.
        try:
            keys = Hotkey.parse(text).text().split("+")
        except ShortcutError:
            keys = None

        if keys:
            for index, key in enumerate(keys):
                if index:
                    plus = QLabel("+")
                    plus.setObjectName("keyPlus")
                    self.keys_layout.addWidget(plus)

                cap = QLabel(KEY_LABELS.get(key, key))
                cap.setObjectName("keyCap")
                self.keys_layout.addWidget(cap)
        else:
            label = QLabel(text)
            label.setObjectName("shortcutText")
            self.keys_layout.addWidget(label)

        self.backend_tag.setText(
            f"via {self.shortcut_backend.name}"
        )

        hint = "\n".join(
            part
            for part in (note, self.shortcut_backend.hint())
            if part
        )

        self.hint_label.setText(hint)
        self.hint_label.setVisible(bool(hint))

        self.state_button.setToolTip(
            f"Global shortcut: {text}" if keys else ""
        )

    @Slot()
    def on_shortcut_activated(self):
        if not self.is_configuring_shortcut:
            self.toggle_autoclicker()

    @Slot(str)
    def on_shortcut_status(self, text):
        self.show_shortcut(text)

    def configure_shortcut(self):
        backend = self.shortcut_backend

        if not backend.configurable:
            # The desktop owns the shortcut (XDG portal).
            try:
                backend.open_settings()
            except Exception as error:
                QMessageBox.information(
                    self,
                    "Shortcut configuration",
                    str(error),
                )
            return

        self.is_configuring_shortcut = True

        try:
            dialog = QDialog(self)
            dialog.setObjectName("shortcutDialog")
            dialog.setWindowTitle("Global shortcut")
            dialog.setMinimumWidth(360)

            dialog_layout = QVBoxLayout(dialog)
            dialog_layout.setContentsMargins(22, 20, 22, 18)
            dialog_layout.setSpacing(10)

            heading = QLabel("Change global shortcut")
            heading.setObjectName("dialogTitle")

            instruction = QLabel(
                "Press the keys you want to use to start "
                "and stop clicking."
            )
            instruction.setObjectName("hint")
            instruction.setWordWrap(True)

            editor = QKeySequenceEdit()
            editor.setObjectName("shortcutEditor")
            editor.setMaximumSequenceLength(1)

            if self.hotkey:
                editor.setKeySequence(
                    QKeySequence(self.hotkey.text())
                )

            rules = QLabel(
                "Use F1–F24, or a letter or number "
                "combined with Ctrl, Alt, Shift or Super."
            )
            rules.setObjectName("hint")
            rules.setWordWrap(True)

            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Save
                | QDialogButtonBox.StandardButton.Cancel
            )

            buttons.button(
                QDialogButtonBox.StandardButton.Save
            ).setObjectName("shortcutSave")

            buttons.button(
                QDialogButtonBox.StandardButton.Cancel
            ).setObjectName("shortcutCancel")

            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)

            dialog_layout.addWidget(heading)
            dialog_layout.addWidget(instruction)
            dialog_layout.addSpacing(4)
            dialog_layout.addWidget(editor)
            dialog_layout.addWidget(rules)
            dialog_layout.addSpacing(8)
            dialog_layout.addWidget(buttons)

            editor.setFocus()

            # ---------------------------------------------
            # Handle dialog result
            # ---------------------------------------------

            if dialog.exec() != QDialog.DialogCode.Accepted:
                return

            try:
                requested = Hotkey.parse(
                    editor.keySequence().toString(
                        QKeySequence.SequenceFormat.PortableText
                    )
                )

                saved = backend.change(requested, self.hotkey)

                self.hotkey = saved
                self.settings.setValue("hotkey", saved.text())
                self.show_shortcut(saved.text())

            except Exception as error:
                QMessageBox.warning(
                    self,
                    "Shortcut unavailable",
                    str(error),
                )

        finally:
            self.is_configuring_shortcut = False

    # =====================================================
    # SETTINGS
    # =====================================================

    def update_timer(self, cps):
        if self.is_running:
            self.update_running_status()

        new_interval = max(
            1,
            round(1000 / cps),
        )

        self.click_timer.setInterval(
            new_interval
        )

    def save_cps(self, cps):
        self.settings.setValue(
            "cps",
            cps,
        )

    def save_mouse_button(self, button):
        self.settings.setValue(
            "mouse_button",
            button,
        )

    # =====================================================
    # CLEANUP
    # =====================================================

    def closeEvent(self, event):
        self.click_timer.stop()

        self.shortcut_server.close()

        if self.shortcut_backend:
            self.shortcut_backend.stop()

        if self.mouse_backend:
            self.mouse_backend.close()

        super().closeEvent(event)


# =====================================================
# START APPLICATION
# =====================================================

app = QApplication(sys.argv)

app.setDesktopFileName(
    "be.ganderstudio.GanderAutoClicker"
)

app.setApplicationName(
    "Gander AutoClicker"
)

apply_theme(app)

window = MainWindow()
window.show()

sys.exit(app.exec())