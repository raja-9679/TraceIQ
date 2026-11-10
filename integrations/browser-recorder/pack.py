#!/usr/bin/env python3
"""Build the recorder zip the app offers for download.

    integrations/browser-recorder/pack.py            write frontend/public/recorder/traceiq-recorder.zip
    integrations/browser-recorder/pack.py --check    exit 1 if that zip is out of date

The frontend image is built from frontend/ alone, so the zip is committed
there rather than produced by the image build. It is byte-for-byte
reproducible (fixed order, fixed timestamps), which is what lets --check
compare it. The same zip is what gets uploaded to the Chrome Web Store.
"""
import io
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parents[1] / "frontend" / "public" / "recorder" / "traceiq-recorder.zip"
FILES = [
    "manifest.json", "background.js", "selectors.js", "content.js",
    "popup.html", "popup.js", "approve.html", "approve.js", "page.css",
    "icons/16.png", "icons/48.png", "icons/128.png",
]


def build() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in FILES:
            info = zipfile.ZipInfo(f"traceiq-recorder/{name}", date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, (HERE / name).read_bytes())
    return buf.getvalue()


def main() -> int:
    data = build()
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_bytes() != data:
            print(f"{OUT} is out of date: run integrations/browser-recorder/pack.py", file=sys.stderr)
            return 1
        print("recorder zip is up to date")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(data)
    print(f"wrote {OUT} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
