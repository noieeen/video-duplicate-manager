"""Headless entry point for Docker: runs main.py's pipeline on env-configured paths.

main.py is imported unchanged; its hard-coded Windows paths are overridden here.
"""
import os
import main

source_dirs = os.environ.get("SOURCE_DIRS", "/videos").split(os.pathsep)
main.FRAME_DIR = os.environ.get("FRAME_DIR", "/output/frames")
duplicate_dir = os.environ.get("DUPLICATE_DIR", "/output/duplicates")
threshold = float(os.environ.get("SIMILARITY_THRESHOLD", "0.95"))
os.makedirs(main.FRAME_DIR, exist_ok=True)
os.makedirs(duplicate_dir, exist_ok=True)

embeddings = main.process_videos(source_dirs)
groups = main.find_duplicates(embeddings, threshold)
dupes = [g for g in groups.values() if len(g) > 1]
print(f"{len(embeddings)} videos embedded, {len(dupes)} duplicate groups")
for g in dupes:
    print("  group:", *g, sep="\n    ")
main.process_duplicates(groups, True, duplicate_dir)
print("Moved duplicates:", sorted(os.listdir(duplicate_dir)))
