#!/usr/bin/env python3
"""Render a guide markdown file (produced by build_guide.py) into a
formatted offline PDF: pandoc (md -> HTML) -> inject style.css -> shrink
embedded images -> weasyprint (HTML -> PDF).

Requires: pandoc, weasyprint, and ImageMagick's `convert` on PATH.

Usage:
    ./render_pdf.py --md offline-guides/.build/guide-pt-BR.md \\
        --out offline-guides/rodeo-pt-BR-guia-offline.pdf
"""
import argparse
import datetime
import hashlib
import re
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Per-locale document chrome. Add an entry here when generating the guide
# for a new locale; falls back to a generic English one otherwise.
LOCALE_META = {
    "pt-BR": {
        "header": "SUSE Virtualization Rodeo — Guia Offline (pt-BR)",
        "title": "SUSE Virtualization Rodeo",
        "subtitle": "Guia Offline (pt-BR) — história e desafios para execução manual, sem o ambiente Instruqt",
        "date_prefix": "Gerado em",
    },
}
DEFAULT_META = {
    "header": "SUSE Virtualization Rodeo — Offline Guide",
    "title": "SUSE Virtualization Rodeo",
    "subtitle": "Offline Guide — story and challenges for running the lab manually, without Instruqt",
    "date_prefix": "Generated on",
}


def run(cmd):
    subprocess.run(cmd, check=True)


def check_deps():
    missing = [t for t in ("pandoc", "weasyprint", "convert") if shutil.which(t) is None]
    if missing:
        raise SystemExit(
            f"Missing required tool(s) on PATH: {', '.join(missing)}. "
            "Install pandoc, weasyprint and ImageMagick first."
        )


def pandoc_to_html(md_path, html_path, meta):
    today = datetime.date.today().strftime("%d/%m/%Y")
    run([
        "pandoc", str(md_path), "-f", "gfm+attributes", "--toc", "--toc-depth=2",
        "-o", str(html_path), "--standalone",
        "--metadata", f"title={meta['title']}",
        "--metadata", f"subtitle={meta['subtitle']}",
        "--metadata", f"date={meta['date_prefix']} {today}",
    ])


def inject_css(html_path, meta):
    css = (HERE / "style.css").read_text().replace("__HEADER_TITLE__", meta["header"])
    h = html_path.read_text()
    h = h.replace("</head>", f"<style>\n{css}\n</style>\n</head>")
    html_path.write_text(h)


def optimize_images(html_path, cache_dir):
    """Downscale/recompress every embedded image to keep the PDF small.
    Animated GIFs are reduced to their first frame (a static demo image is
    all a PDF needs)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    h = html_path.read_text()
    srcs = sorted(set(re.findall(r'src="(file://[^"]+)"', h)))
    for s in srcs:
        path = Path(s[len("file://"):])
        if not path.exists():
            print("MISSING image:", path)
            continue
        key = hashlib.md5(s.encode()).hexdigest()[:12]
        out = cache_dir / f"{key}.jpg"
        if not out.exists():
            src_spec = f"{path}[0]" if path.suffix.lower() == ".gif" else str(path)
            run([
                "convert", src_spec, "-resize", "900x900>", "-background", "white",
                "-flatten", "-quality", "70", str(out),
            ])
        h = h.replace(f'src="{s}"', f'src="file://{out}"')
    html_path.write_text(h)


def render(md_path, out_pdf, locale, keep_html):
    check_deps()
    meta = LOCALE_META.get(locale, DEFAULT_META)
    work_dir = out_pdf.parent / ".build"
    work_dir.mkdir(parents=True, exist_ok=True)
    html_path = work_dir / f"{out_pdf.stem}.html"
    cache_dir = work_dir / "imgcache"

    pandoc_to_html(md_path, html_path, meta)
    inject_css(html_path, meta)
    optimize_images(html_path, cache_dir)
    run(["weasyprint", str(html_path), str(out_pdf)])

    if not keep_html:
        html_path.unlink(missing_ok=True)
    print("Wrote", out_pdf)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--md", type=Path, required=True, help="input markdown from build_guide.py")
    ap.add_argument("--out", type=Path, required=True, help="output .pdf path")
    ap.add_argument("--locale", default="pt-BR", help="locale, selects document title/header text (default: pt-BR)")
    ap.add_argument("--keep-html", action="store_true", help="keep the intermediate HTML for debugging")
    args = ap.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    render(args.md, args.out, args.locale, args.keep_html)


if __name__ == "__main__":
    main()
