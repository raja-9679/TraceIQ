#!/usr/bin/env python3
"""Turn each fresh recording into the MP4 the guide plays.

For every <name>.webm under the media folder: if capture.ts left a
<name>.narration.json next to it (the spoken caption lines and when each
started), the lines are placed at those offsets and mixed into an AAC track;
otherwise the MP4 is silent. The WebM and the JSON are removed afterwards —
only the MP4 is published.

    mux.py <media dir> <e2e dir>     (run.sh calls this; needs ffmpeg)
"""
import json
import subprocess
import sys
from pathlib import Path


def convert(webm: Path, e2e: Path) -> None:
    mp4 = webm.with_suffix(".mp4")
    meta = webm.with_suffix(".narration.json")
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(webm)]
    lines = json.loads(meta.read_text())["lines"] if meta.exists() else []
    for line in lines:
        cmd += ["-i", str(e2e / line["wav"])]
    video = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "26", "-preset", "slow",
             "-movflags", "+faststart"]
    if lines:
        delayed = "".join(f"[{i + 1}:a]adelay={int(l['at_ms'])}:all=1[a{i}];" for i, l in enumerate(lines))
        mixed = "".join(f"[a{i}]" for i in range(len(lines)))
        # normalize=0: amix would otherwise divide every line's volume by the
        # number of inputs. The lines never overlap (capture waits for each).
        # loudnorm: speech-level loudness (-16 LUFS) with peaks kept below
        # -1.5 dB, so it neither whispers nor crackles on laptop speakers.
        graph = (f"{delayed}{mixed}amix=inputs={len(lines)}:normalize=0:duration=longest,"
                 "loudnorm=I=-16:TP=-1.5:LRA=11,apad[aout]")
        cmd += ["-filter_complex", graph, "-map", "0:v", "-map", "[aout]", *video,
                "-c:a", "aac", "-b:a", "96k", "-ar", "44100", "-shortest", str(mp4)]
    else:
        cmd += [*video, "-an", str(mp4)]
    subprocess.run(cmd, check=True, stdin=subprocess.DEVNULL)
    webm.unlink()
    meta.unlink(missing_ok=True)
    print(f"{mp4.name}: {'narrated, ' + str(len(lines)) + ' lines' if lines else 'silent'}")


def main() -> None:
    media, e2e = Path(sys.argv[1]), Path(sys.argv[2])
    for webm in sorted(media.rglob("*.webm")):
        if ".tmp-video" in webm.parts:
            continue
        convert(webm, e2e)


if __name__ == "__main__":
    main()
