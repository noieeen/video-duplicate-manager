import sys
import os
import threading
import glob
from PyQt5.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QPushButton, QLabel, QProgressBar, QFileDialog, QWidget, QListWidget, QLineEdit
from PyQt5.QtCore import pyqtSignal, QObject
from PIL import Image
import torch
from main import process_videos, find_duplicates, process_duplicates, extract_frame, preprocess, model, device
import subprocess

class ProcessingWorker(QObject):
    progress_signal = pyqtSignal(int)
    stop_signal = pyqtSignal()

    def __init__(self, source_dirs, duplicate_folder_path):
        super().__init__()
        self.source_dirs = source_dirs
        self.duplicate_folder_path = duplicate_folder_path
        self._stop_requested = False

    def run(self):
        try:
            # Step 1: Process videos
            video_embeddings = {}
            video_files = []
            for dir_path in self.source_dirs:
                if self._stop_requested:
                    self.stop_signal.emit()
                    return
                video_files.extend(glob.glob(os.path.join(dir_path, "**", "*.mp4"), recursive=True))
                video_files.extend(glob.glob(os.path.join(dir_path, "**", "*.ts"), recursive=True))

            for i, video_path in enumerate(video_files):
                if self._stop_requested:
                    self.stop_signal.emit()
                    return
                filename = os.path.basename(video_path)
                frame_path = os.path.join("temp_data", os.path.splitext(filename)[0] + ".jpg")
                if extract_frame(video_path, frame_path):
                    try:
                        image = preprocess(Image.open(frame_path)).unsqueeze(0).to(device)
                        with torch.no_grad():
                            embedding = model.encode_image(image).cpu().numpy()
                        video_embeddings[video_path] = embedding[0]
                    except:
                        continue
                self.progress_signal.emit(int((i + 1) / len(video_files) * 33))

            # Step 2: Find duplicates
            groups = find_duplicates(video_embeddings, similarity_threshold=0.95)
            self.progress_signal.emit(66)

            if self._stop_requested:
                self.stop_signal.emit()
                return

            # Step 3: Process duplicates
            process_duplicates(groups, keep_best=True, duplicate_dir=self.duplicate_folder_path)
            self.progress_signal.emit(100)
        except Exception as e:
            print(f"Error: {str(e)}")

    def stop(self):
        self._stop_requested = True

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

        # Folder list
        self.folder_list = QListWidget()

        # Buttons
        self.add_folder_button = QPushButton("Add Source Folder")
        self.remove_folder_button = QPushButton("Remove Selected Folder")
        self.set_duplicate_folder_button = QPushButton("Set Duplicate Folder")
        self.start_button = QPushButton("Start Processing")
        self.stop_button = QPushButton("Stop")

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)

        # Status label
        self.status_label = QLabel("Status: Ready")

        # Temporary data location
        self.temp_data_label = QLabel(f"Temporary Data Location: {os.path.abspath('temp_data')}")

        # Add widgets to layout
        self.layout.addWidget(self.folder_list)
        self.layout.addWidget(self.add_folder_button)
        self.layout.addWidget(self.remove_folder_button)
        self.layout.addWidget(self.set_duplicate_folder_button)
        self.layout.addWidget(self.start_button)
        self.layout.addWidget(self.stop_button)
        self.layout.addWidget(self.progress_bar)
        self.layout.addWidget(self.status_label)
        self.layout.addWidget(self.temp_data_label)

        # Connect buttons to actions
        self.add_folder_button.clicked.connect(self.add_folder)
        self.remove_folder_button.clicked.connect(self.remove_folder)
        self.set_duplicate_folder_button.clicked.connect(self.set_duplicate_folder)
        self.start_button.clicked.connect(self.start_processing)
        self.stop_button.clicked.connect(self.stop_processing)

        # Duplicate folder path
        self.duplicate_folder_path = ""

    def add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Source Folder", "")
        if folder:
            self.folder_list.addItem(folder)

    def remove_folder(self):
        selected_items = self.folder_list.selectedItems()
        for item in selected_items:
            self.folder_list.takeItem(self.folder_list.row(item))

    def set_duplicate_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Duplicate Folder", "")
        if folder:
            self.duplicate_folder_path = folder
            self.status_label.setText(f"Duplicate Folder Set: {folder}")

    def start_processing(self):
        self.status_label.setText("Processing started...")
        self.progress_bar.setValue(0)

        # Collect source folders
        source_dirs = [self.folder_list.item(i).text() for i in range(self.folder_list.count())]
        if not source_dirs:
            self.status_label.setText("No source folders selected.")
            return

        if not self.duplicate_folder_path:
            self.status_label.setText("Duplicate folder not set.")
            return

        # Create worker and thread
        self.worker = ProcessingWorker(source_dirs, self.duplicate_folder_path)
        self.worker.progress_signal.connect(self.update_progress)
        self.worker.stop_signal.connect(self.processing_stopped)

        self.processing_thread = threading.Thread(target=self.worker.run)
        self.processing_thread.start()

    def stop_processing(self):
        if hasattr(self, 'worker') and self.worker:
            self.worker.stop()
            self.status_label.setText("Stopping processing...")

    def processing_stopped(self):
        self.status_label.setText("Processing stopped.")
        self.progress_bar.setValue(0)

    def update_progress(self, value):
        self.progress_bar.setValue(value)

def extract_frame(video_path, output_path):
    """Robust frame extraction with multiple fallback methods"""
    for seek_time in ["00:05:00", "00:02:00", "00:10:00"]:
        try:
            cmd = [
                "ffmpeg",
                "-ss", seek_time,
                "-i", video_path,
                "-vframes", "1",
                "-q:v", "2",
                "-f", "image2",
                "-y",
                output_path
            ]
            subprocess.run(cmd, check=True, stderr=subprocess.PIPE, stdout=subprocess.PIPE, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                return True
        except:
            continue
    
    try:
        cmd = [
            "ffmpeg",
            "-ss", "00:05:00",
            "-i", video_path,
            "-vframes", "1",
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "-s", "1920x1080",
            "-y",
            "-"
        ]
        result = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.stdout:
            img = Image.frombytes('RGB', (1920, 1080), result.stdout)
            img.save(output_path, "JPEG", quality=90)
            return True
    except:
        return False

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = VideoDuplicateManagerUI()
    window.show()
    sys.exit(app.exec_())
