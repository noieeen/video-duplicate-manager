import sys
from PyQt5.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QPushButton, QLabel, QProgressBar, QFileDialog, QWidget

class VideoDuplicateManagerUI(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Video Duplicate Manager")
        self.setGeometry(100, 100, 600, 400)

        # Main layout
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout()
        self.central_widget.setLayout(self.layout)

        # Buttons
        self.select_folder_button = QPushButton("Select Source Folders")
        self.start_button = QPushButton("Start Processing")
        self.stop_button = QPushButton("Stop")

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)

        # Status label
        self.status_label = QLabel("Status: Ready")

        # Add widgets to layout
        self.layout.addWidget(self.select_folder_button)
        self.layout.addWidget(self.start_button)
        self.layout.addWidget(self.stop_button)
        self.layout.addWidget(self.progress_bar)
        self.layout.addWidget(self.status_label)

        # Connect buttons to actions
        self.select_folder_button.clicked.connect(self.select_folders)
        self.start_button.clicked.connect(self.start_processing)
        self.stop_button.clicked.connect(self.stop_processing)

    def select_folders(self):
        folders = QFileDialog.getExistingDirectory(self, "Select Source Folders", "")
        if folders:
            self.status_label.setText(f"Selected Folder: {folders}")

    def start_processing(self):
        self.status_label.setText("Processing started...")
        self.progress_bar.setValue(0)
        # TODO: Connect to backend logic

    def stop_processing(self):
        self.status_label.setText("Processing stopped.")
        # TODO: Implement stop functionality

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = VideoDuplicateManagerUI()
    window.show()
    sys.exit(app.exec_())
