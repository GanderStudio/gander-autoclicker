import sys

from linux_mouse import (
    LinuxMouseBackend,
)

from PySide6.QtGui import (
    QKeySequence,
    QShortcut,
)

from PySide6.QtCore import (
    QTimer, 
    Qt,
    QSettings,
)

from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QLabel,
    QKeySequenceEdit,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.is_running = False

        self.setWindowTitle("Gander AutoClicker")
        self.resize(360, 300)

        # -------------------------
        # Main container
        # -------------------------

        container = QWidget()
        layout = QVBoxLayout(container)

        # -------------------------
        # Title
        # -------------------------

        title = QLabel("Gander AutoClicker")
        layout.addWidget(title)

        # -------------------------
        # Settings
        # -------------------------

        self.settings = QSettings(
        "Gander Studio",
        "Gander AutoClicker"
        )

        settings_layout = QFormLayout()

        self.cps_input = QSpinBox()
        self.cps_input.setRange(1, 500)
        saved_cps = self.settings.value("cps", 10, type=int)
        self.cps_input.setValue(saved_cps)

        self.mouse_button_input = QComboBox()
        self.mouse_button_input.addItems([
            "Left",
            "Right",
            "Middle",
        ])
        saved_button = self.settings.value(
        "mouse_button",
        "Left",
        type=str
        )
        self.mouse_button_input.setCurrentText(saved_button)

        hotkey = self.settings.value(
        "hotkey",
        "F6",
        type=str
        )
        self.hotkey_input = QKeySequenceEdit()
        self.hotkey_input.setKeySequence(QKeySequence(hotkey))
        settings_layout.addRow("Clicks per second:", self.cps_input)
        settings_layout.addRow("Mouse button:", self.mouse_button_input)
        settings_layout.addRow("Hotkey:", self.hotkey_input)

        layout.addLayout(settings_layout)

        # -------------------------
        # Start / Stop
        # -------------------------

        self.state_button = QPushButton("Start")
        self.state_button.clicked.connect(self.toggle_autoclicker)

        layout.addWidget(self.state_button)

        self.toggle_shortcut = QShortcut(
            QKeySequence(hotkey),
            self
        )

        # -------------------------
        # Connection to functions
        # -------------------------

        self.hotkey_input.keySequenceChanged.connect(self.update_hotkey)
        self.toggle_shortcut.activated.connect(self.toggle_autoclicker)
        self.cps_input.valueChanged.connect(self.update_timer)
        self.cps_input.valueChanged.connect(self.save_cps)
        self.mouse_button_input.currentTextChanged.connect(self.save_mouse_button)



        # -------------------------
        # Status
        # -------------------------

        self.status_label = QLabel("Status: Stopped")
        layout.addWidget(self.status_label)
        self.setCentralWidget(container)

        # -------------------------
        # Clicking timer
        # -------------------------
        
        self.click_timer = QTimer(self)
        self.mouse_backend = LinuxMouseBackend()

        self.click_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.click_timer.timeout.connect(self.timer_tick)

        self.update_timer(self.cps_input.value())



        # -------------------------
        # functions
        # -------------------------

    def toggle_autoclicker(self):
        self.is_running = not self.is_running

        if self.is_running:
            self.state_button.setText("Stop")
            self.status_label.setText("Status: Running")
            self.click_timer.start()
        else:
            self.state_button.setText("Start")
            self.status_label.setText("Status: Stopped")
            self.click_timer.stop()


    def update_hotkey(self, new_hotkey):
        self.toggle_shortcut.setKey(new_hotkey)
        self.settings.setValue("hotkey", new_hotkey.toString())


    def update_timer(self, cps):
        new_interval = max(
        1,
        round(1000 / cps)
        )
        self.click_timer.setInterval(new_interval)


    def save_cps(self, cps):
        self.settings.setValue("cps", cps)

    def save_mouse_button(self, button):
        self.settings.setValue("mouse_button", button)


    def timer_tick(self):
        button = self.mouse_button_input.currentText()
        self.mouse_backend.click(button)



app = QApplication(sys.argv)

window = MainWindow()
window.show()

sys.exit(app.exec())