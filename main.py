import sys

from PySide6.QtWidgets import QApplication, QLabel, QMainWindow


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Gander AutoClicker")
        self.resize(360, 240)

        label = QLabel("Gander AutoClicker")
        self.setCentralWidget(label)


app = QApplication(sys.argv)

window = MainWindow()
window.show()

sys.exit(app.exec())
