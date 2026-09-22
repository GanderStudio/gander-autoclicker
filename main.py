
import ast
import os
import re
import shlex
import socket
import stat
import subprocess
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


# GNOME runs this lightweight client when the shortcut is pressed.
# No Qt application or virtual mouse is created in client mode.

if __name__ == "__main__" and "--toggle" in sys.argv:
    sys.exit(send_toggle())


# =====================================================
# APPLICATION IMPORTS
# =====================================================

from PySide6.QtCore import Qt, QTimer, QSettings, Slot
from PySide6.QtGui import QKeySequence
from PySide6.QtNetwork import QLocalServer

from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
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


# =====================================================
# GNOME SHORTCUT CONFIGURATION
# =====================================================

GNOME_SCHEMA = (
    "org.gnome.settings-daemon.plugins.media-keys"
)

CUSTOM_SCHEMA = GNOME_SCHEMA + ".custom-keybinding"

CUSTOM_PATH = (
    "/org/gnome/settings-daemon/plugins/media-keys/"
    "custom-keybindings/gander-autoclicker/"
)

SHORTCUT_SCHEMA = f"{CUSTOM_SCHEMA}:{CUSTOM_PATH}"


def gsettings(*arguments):
    result = subprocess.run(
        ["gsettings", *arguments],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )

    return result.stdout.strip()


def read_shortcut_list():
    value = gsettings(
        "get",
        GNOME_SCHEMA,
        "custom-keybindings",
    )

    if value == "@as []":
        return []

    return ast.literal_eval(value)


def read_gnome_shortcut():
    value = gsettings(
        "get",
        SHORTCUT_SCHEMA,
        "binding",
    )

    return ast.literal_eval(value)


def to_gnome_accelerator(sequence):
    text = sequence.toString(
        QKeySequence.SequenceFormat.PortableText
    )

    if not text:
        raise ValueError("Choose a keyboard shortcut.")

    parts = text.split("+")

    if len(parts) > 4:
        raise ValueError("Too many modifiers.")

    modifiers = {
        "Ctrl": "<Control>",
        "Alt": "<Alt>",
        "Shift": "<Shift>",
        "Meta": "<Super>",
    }

    key = parts[-1]

    if not key:
        raise ValueError("Choose a valid key.")

    function_key = re.fullmatch(
        r"F(?:[1-9]|1[0-9]|2[0-4])",
        key,
    )

    if not function_key and len(parts) == 1:
        raise ValueError(
            "Use a function key or a shortcut with modifiers."
        )

    if not (
        function_key
        or (len(key) == 1 and key.isalnum())
        or key in ("Space", "Return", "Esc")
    ):
        raise ValueError(
            "Use F1-F24, or a letter/number with modifiers."
        )

    prefix = ""

    for modifier in parts[:-1]:
        if modifier not in modifiers:
            raise ValueError(
                f"Unsupported modifier: {modifier}"
            )

        prefix += modifiers[modifier]

    key_names = {
        "Space": "space",
        "Esc": "Escape",
    }

    return prefix + key_names.get(key, key)


def to_display_shortcut(accelerator):
    modifiers = {
        "Control": "Ctrl",
        "Primary": "Ctrl",
        "Shift": "Shift",
        "Alt": "Alt",
        "Super": "Meta",
    }

    def replace_modifier(match):
        name = match.group(1)
        return modifiers.get(name, name) + "+"

    return re.sub(
        r"<([^>]+)>",
        replace_modifier,
        accelerator,
    )


def install_gnome_shortcut():
    shortcuts = read_shortcut_list()

    command = shlex.join([
        sys.executable,
        str(Path(__file__).resolve()),
        "--toggle",
    ])

    # Create our shortcut without overwriting existing shortcuts.

    if CUSTOM_PATH not in shortcuts:
        shortcuts.append(CUSTOM_PATH)

        gsettings(
            "set",
            SHORTCUT_SCHEMA,
            "name",
            repr("Gander AutoClicker"),
        )

        gsettings(
            "set",
            SHORTCUT_SCHEMA,
            "command",
            repr(command),
        )

        gsettings(
            "set",
            SHORTCUT_SCHEMA,
            "binding",
            repr("F6"),
        )

        gsettings(
            "set",
            GNOME_SCHEMA,
            "custom-keybindings",
            repr(shortcuts),
        )

    else:
        # Keep the command updated if the project location changes.

        gsettings(
            "set",
            SHORTCUT_SCHEMA,
            "command",
            repr(command),
        )

    return read_gnome_shortcut()


def save_gnome_shortcut(accelerator):
    gsettings(
        "set",
        SHORTCUT_SCHEMA,
        "binding",
        repr(accelerator),
    )

    return read_gnome_shortcut()


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
        self.resize(430, 430)
        self.setMinimumWidth(390)

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

        eyebrow = QLabel("GANDER STUDIO / UTILITY")
        eyebrow.setObjectName("eyebrow")

        title = QLabel("Gander AutoClicker")
        title.setObjectName("title")

        subtitle = QLabel(
            "Simple, lightweight mouse automation."
        )
        subtitle.setObjectName("subtitle")

        layout.addWidget(eyebrow)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        # -------------------------------------------------
        # Settings panel
        # -------------------------------------------------

        panel = QFrame()
        panel.setObjectName("panel")

        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(16, 16, 16, 16)
        panel_layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(12)

        # CPS

        self.cps_input = QSpinBox()
        self.cps_input.setRange(1, 500)

        saved_cps = self.settings.value(
            "cps",
            10,
            type=int,
        )

        self.cps_input.setValue(saved_cps)

        form.addRow(
            "Clicks per second",
            self.cps_input,
        )

        # Mouse button

        self.mouse_button_input = QComboBox()

        self.mouse_button_input.addItems([
            "Left",
            "Right",
            "Middle",
        ])

        saved_button = self.settings.value(
            "mouse_button",
            "Left",
            type=str,
        )

        self.mouse_button_input.setCurrentText(
            saved_button
        )

        form.addRow(
            "Mouse button",
            self.mouse_button_input,
        )

        panel_layout.addLayout(form)

        # -------------------------------------------------
        # Global hotkey
        # -------------------------------------------------

        self.hotkey_label = QLabel(
            "Configuring global shortcut..."
        )

        self.hotkey_label.setWordWrap(True)

        self.configure_button = QPushButton(
            "Configure shortcut"
        )

        self.configure_button.setObjectName(
            "secondary"
        )

        self.configure_button.setEnabled(False)

        panel_layout.addWidget(
            QLabel("Global shortcut")
        )

        panel_layout.addWidget(
            self.hotkey_label
        )

        panel_layout.addWidget(
            self.configure_button
        )

        layout.addWidget(panel)

        # -------------------------------------------------
        # Start / Stop
        # -------------------------------------------------

        self.state_button = QPushButton("Start")
        self.state_button.setObjectName("primary")

        layout.addWidget(self.state_button)

        # -------------------------------------------------
        # Status
        # -------------------------------------------------

        self.status_label = QLabel("● Ready")
        self.status_label.setObjectName("status")

        layout.addWidget(self.status_label)

        layout.addStretch()

        # -------------------------------------------------
        # Footer
        # -------------------------------------------------

        footer = QLabel(
            "GANDER STUDIO  ·  LINUX BETA"
        )

        footer.setObjectName("footer")

        footer.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        layout.addWidget(footer)

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

            self.status_label.setText(
                f"Linux input unavailable: {error}"
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
        # Configure GNOME shortcut
        # -------------------------------------------------

        try:
            accelerator = install_gnome_shortcut()

            self.hotkey_label.setText(
                f"{to_display_shortcut(accelerator)} · Global"
            )

            self.configure_button.setEnabled(True)

        except Exception as error:
            self.hotkey_label.setText(
                f"Global shortcut unavailable: {error}"
            )

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
            self.state_button.setText("Stop")

            self.status_label.setText(
                "● Clicking"
            )

            self.click_timer.start()

        else:
            self.click_timer.stop()

            self.state_button.setText("Start")

            self.status_label.setText(
                "● Ready"
            )

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

    def configure_shortcut(self):
        self.is_configuring_shortcut = True

        try:
            current = read_gnome_shortcut()

            # ---------------------------------------------
            # Dialog
            # ---------------------------------------------

            dialog = QDialog(self)

            dialog.setObjectName(
                "shortcutDialog"
            )

            dialog.setWindowTitle(
                "Configure global shortcut"
            )

            dialog.setMinimumWidth(340)

            dialog_layout = QVBoxLayout(dialog)
            dialog_layout.setSpacing(14)

            # ---------------------------------------------
            # Instructions
            # ---------------------------------------------

            instruction = QLabel(
                "Press the new keyboard shortcut:"
            )

            dialog_layout.addWidget(instruction)

            # ---------------------------------------------
            # Shortcut editor
            # ---------------------------------------------

            editor = QKeySequenceEdit()

            editor.setObjectName(
                "shortcutEditor"
            )

            editor.setMaximumSequenceLength(1)

            editor.setKeySequence(
                QKeySequence(
                    to_display_shortcut(current)
                )
            )

            dialog_layout.addWidget(editor)

            # ---------------------------------------------
            # Save / Cancel buttons
            # ---------------------------------------------

            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Save
                | QDialogButtonBox.StandardButton.Cancel
            )

            save_button = buttons.button(
                QDialogButtonBox.StandardButton.Save
            )

            save_button.setObjectName(
                "shortcutSave"
            )

            cancel_button = buttons.button(
                QDialogButtonBox.StandardButton.Cancel
            )

            cancel_button.setObjectName(
                "shortcutCancel"
            )

            buttons.accepted.connect(
                dialog.accept
            )

            buttons.rejected.connect(
                dialog.reject
            )

            dialog_layout.addWidget(buttons)

            # ---------------------------------------------
            # Handle dialog result
            # ---------------------------------------------

            if dialog.exec() != QDialog.DialogCode.Accepted:
                return

            try:
                accelerator = to_gnome_accelerator(
                    editor.keySequence()
                )

                # Check existing custom shortcuts.

                for path in read_shortcut_list():
                    if path == CUSTOM_PATH:
                        continue

                    schema = f"{CUSTOM_SCHEMA}:{path}"

                    existing = gsettings(
                        "get",
                        schema,
                        "binding",
                    )

                    if ast.literal_eval(existing) == accelerator:
                        raise ValueError(
                            "This shortcut is already used by "
                            "another custom GNOME shortcut."
                        )

                # Save the new shortcut.

                saved = save_gnome_shortcut(
                    accelerator
                )

                if saved != accelerator:
                    raise RuntimeError(
                        "GNOME did not save the requested shortcut."
                    )

                # Update the displayed shortcut.

                self.hotkey_label.setText(
                    f"{to_display_shortcut(saved)} · Global"
                )

            except Exception as error:
                QMessageBox.warning(
                    self,
                    "Shortcut unavailable",
                    str(error),
                )

        except Exception as error:
            QMessageBox.warning(
                self,
                "Shortcut configuration",
                str(error),
            )

        finally:
            self.is_configuring_shortcut = False

    # =====================================================
    # SETTINGS
    # =====================================================

    def update_timer(self, cps):
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

# Load all visual styling from style.qss.

style_path = Path(__file__).with_name(
    "style.qss"
)

if style_path.exists():
    app.setStyleSheet(
        style_path.read_text(encoding="utf-8")
    )

window = MainWindow()
window.show()

sys.exit(app.exec())