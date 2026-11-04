"""Event output: one JSON object per line (default) or a readable line (human mode)."""
import json
import sys


class Emitter:
    def __init__(self, human: bool = False, stream=None):
        self.human = human
        self.stream = stream

    def emit(self, type: str, **data) -> None:
        out = self.stream if self.stream is not None else sys.stdout
        if self.human:
            parts = " ".join(f"{k}={v}" for k, v in data.items())
            line = f"[{type}] {parts}".rstrip()
        else:
            line = json.dumps({"type": type, **data}, ensure_ascii=False)
        out.write(line + "\n")
        out.flush()
