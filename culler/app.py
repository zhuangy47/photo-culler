import sys

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from .window import MainWindow


def main() -> int:
    QCoreApplication.setOrganizationName("culler")
    QCoreApplication.setApplicationName("culler")

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
