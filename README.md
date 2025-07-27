# Video Management Tool

This project is a Python-based video management tool designed to help organize, deduplicate, and manage large collections of video files. It uses machine learning (CLIP model) to detect visually similar videos and moves duplicates to a separate folder, keeping only the best version based on video quality metrics.

## Features
- **Frame Extraction:** Extracts a frame from each video using robust fallback methods with FFmpeg.
- **Visual Similarity Detection:** Uses OpenAI's CLIP model to generate embeddings for video frames and compares them to find duplicates.
- **Deduplication:** Groups similar videos and keeps the best version based on resolution, duration, and bitrate. Moves duplicates to a dedicated folder.
- **Multi-Directory Support:** Scans multiple source directories for video files.
- **Progress Reporting:** Uses tqdm for progress bars and prints status updates.

## Requirements
- Python 3.8+
- CUDA-enabled GPU (optional, for faster CLIP inference)
- FFmpeg and FFprobe installed and available in PATH
- Python packages:
  - torch
  - pillow
  - clip
  - tqdm

Install dependencies:
```cmd
pip install -r requirements.txt
```

## Usage
1. **Configure Source Folders:**
   - Edit the `SOURCE_DIRS` list in `main.py` to include all folders containing your videos.
2. **Run the Script:**
   - Execute the script in your terminal:
     ```cmd
     python main.py
     ```
3. **Results:**
   - Frames are saved in the folder specified by `FRAME_DIR`.
   - Duplicates are moved to the folder specified by `DUPLICATE_DIR`.
   - The script prints a summary of files kept and files moved.

## How It Works
- For each video, a frame is extracted and processed by the CLIP model to generate an embedding.
- Videos are compared using cosine similarity of their embeddings.
- Groups of similar videos are identified (similarity > 0.95).
- Within each group, the best video is kept (highest score based on resolution, duration, and bitrate).
- Other videos in the group are moved to the duplicates folder.

## Customization
- **SIMILARITY_THRESHOLD:** Adjust this value in `main.py` to change how strict the duplicate detection is.
- **KEEP_BEST:** Set to `False` to keep all versions (no deduplication).

## Notes
- Only `.mp4` and `.ts` files are processed by default. You can add more extensions in the code if needed.
- Make sure FFmpeg and FFprobe are installed and accessible from your command line.

## License
This project is provided as-is for personal use. No warranty or support is provided.
