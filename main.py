import os
import subprocess
import shutil
import torch
from PIL import Image
import clip
import glob
from tqdm import tqdm
from collections import defaultdict
import io

# --- Settings ---
SOURCE_DIRS =[
              r"C:\Users\Noie\Downloads\Video",
              r"D:\new",
              r"D:\new-2",
              r"D:\new-3",
              r"D:\new-4",
              r"D:\new-5",
              r"D:\new-6",
              r"D:\new-7",
              r"D:\new-8",
              r"D:\new-9",
              r"D:\new-10",
              r"D:\big-fav",
              r"D:\video",
              ]

FRAME_DIR = r"D:\vid-frame"
DUPLICATE_DIR = r"D:\vid-duplicated"  # New folder for duplicates
CLIP_MODEL = "ViT-B/32"
SIMILARITY_THRESHOLD = 0.95
KEEP_BEST = True

# --- Setup ---
device = "cuda" if torch.cuda.is_available() else "cpu"
model, preprocess = clip.load(CLIP_MODEL, device=device)

# Create necessary directories
os.makedirs(FRAME_DIR, exist_ok=True)
os.makedirs(DUPLICATE_DIR, exist_ok=True)  # Create duplicates folder

# --- Video Processing ---
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
            subprocess.run(cmd, check=True, stderr=subprocess.PIPE, stdout=subprocess.PIPE, timeout=10)
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
        result = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        if result.stdout:
            img = Image.frombytes('RGB', (1920, 1080), result.stdout)
            img.save(output_path, "JPEG", quality=90)
            return True
    except:
        return False

# Process all videos
video_files = []
for dir_path in SOURCE_DIRS:
        video_files.extend(glob.glob(os.path.join(dir_path, "**", "*.mp4"), recursive=True))
        video_files.extend(glob.glob(os.path.join(dir_path, "**", "*.ts"), recursive=True))

video_embeddings = {}

print("Extracting frames...")
for video_path in tqdm(video_files):
    filename = os.path.basename(video_path)
    frame_path = os.path.join(FRAME_DIR, os.path.splitext(filename)[0] + ".jpg")
    
    if extract_frame(video_path, frame_path):
        try:
            image = preprocess(Image.open(frame_path)).unsqueeze(0).to(device)
            with torch.no_grad():
                embedding = model.encode_image(image).cpu().numpy()
            video_embeddings[video_path] = embedding[0]
        except:
            continue

# --- Deduplication ---
def cosine_similarity(a, b):
    return float(torch.nn.functional.cosine_similarity(
        torch.tensor(a), torch.tensor(b), dim=0
    ))

# Group by similarity
groups = defaultdict(list)
processed = set()

print("Finding duplicates...")
for path1, emb1 in video_embeddings.items():
    if path1 in processed:
        continue
    group = [path1]
    for path2, emb2 in video_embeddings.items():
        if path2 not in processed and path2 != path1:
            sim = cosine_similarity(emb1, emb2)
            if sim > SIMILARITY_THRESHOLD:
                group.append(path2)
                processed.add(path2)
    groups[path1] = group

# --- Move duplicates instead of deleting ---
def get_video_score(filepath):
    """Score based on duration, resolution and bitrate"""
    try:
        cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=width,height,bit_rate", "-of", "csv=p=0", filepath]
        result = subprocess.run(cmd, capture_output=True, text=True)
        width, height, bitrate = map(int, result.stdout.strip().split(","))
        
        cmd = ["ffprobe", "-v", "error", "-show_entries",
               "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", filepath]
        duration = float(subprocess.run(cmd, capture_output=True, text=True).stdout.strip())
        
        return width * height * duration * (bitrate if bitrate > 0 else 1)
    except:
        return 0

print("Processing duplicates...")
for group in groups.values():
    if len(group) <= 1:
        continue
    
    # Score and keep best
    scored = sorted([(get_video_score(path), path) for path in group], reverse=True)
    best = scored[0][1]
    print(f"\nKeeping best version: {os.path.basename(best)}")
    
    if KEEP_BEST:
        for score, path in scored[1:]:
            try:
                dest_path = os.path.join(DUPLICATE_DIR, os.path.basename(path))
                shutil.move(path, dest_path)
                print(f"Moved to duplicates: {os.path.basename(path)} (score: {score})")
            except Exception as e:
                print(f"Failed to move {os.path.basename(path)}: {str(e)}")

print("\nDone. Remaining files in source folders:")
for folder in SOURCE_DIRS:
    print(f"\nIn folder: {folder}")
    for f in os.listdir(folder):
        if f.lower().endswith(('.mp4', '.ts', '.mkv', '.avi', '.mov')):
            print(f" - {f}")


print("\nFiles moved to duplicates folder:")
for f in os.listdir(DUPLICATE_DIR):
    print(f" - {f}")