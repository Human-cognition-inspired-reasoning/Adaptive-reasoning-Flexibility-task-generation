#!/usr/bin/env python3
"""Build a self-contained human assessment HTML; no server or API required."""
from __future__ import annotations

import argparse
import hashlib
import json
from html import escape
from pathlib import Path

from hanoi.human import BUNDLE_FORMAT, HUMAN_DIR, load_config

ASSETS = ("index.html", "style.css", "engine.js", "offline.js", "app.js")


def participant_text() -> dict:
    return json.loads((HUMAN_DIR / "participant.ko.json").read_text(encoding="utf-8"))


def make_bundle(config: Path) -> dict:
    settings = load_config(config)
    assets = {
        name: hashlib.sha256((HUMAN_DIR / "web" / name).read_bytes()).hexdigest() for name in ASSETS
    }
    bundle = {
        "format": BUNDLE_FORMAT,
        "dataset": settings["dataset"],
        "dataset_sha256": settings["dataset_sha256"],
        "config": {
            "max_attempts": settings["T"],
            "success_streak": settings["k"],
            "r1_apply_k": settings["r1_apply_k"],
            "last_attempt_correct_passes": True,
        },
        "feedback_policy": settings["feedback_policy"],
        "instructions": participant_text()["instructions"],
        "source_sha256": assets,
    }
    encoded = json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    bundle["bundle_id"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return bundle


def render_html(bundle: dict) -> str:
    source = HUMAN_DIR / "web"
    html = (source / "index.html").read_text(encoding="utf-8")
    css = (source / "style.css").read_text(encoding="utf-8")
    html = html.replace('<link rel="stylesheet" href="/style.css">', f"<style>\n{css}\n</style>")
    html = html.replace('<script src="/app.js" defer></script>', "")
    html = html.replace('class="brand" href="/"', 'class="brand" href="#"')
    policy = (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "img-src data: blob:; connect-src 'none'; base-uri 'none'; form-action 'none'"
    )
    html = html.replace(
        "<head>", f'<head>\n<meta http-equiv="Content-Security-Policy" content="{policy}">', 1
    )
    html = html.replace(
        "<main>",
        f'<main>\n<noscript>{escape(participant_text()["javascript_required"])}</noscript>',
        1,
    )
    encoded = json.dumps(bundle, ensure_ascii=False, separators=(",", ":"))
    encoded = (
        encoded.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    )
    scripts = [f"window.HANOI_BUNDLE={encoded};"]
    scripts.extend(
        (source / name).read_text(encoding="utf-8")
        for name in ("engine.js", "offline.js", "app.js")
    )
    for script in scripts:
        if "</script" in script.lower():
            raise ValueError("Inline JavaScript contains an unsafe script closing tag")
    return html.replace(
        "</body>", "\n".join(f"<script>\n{s}\n</script>" for s in scripts) + "\n</body>"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HUMAN_DIR / "config.toml")
    parser.add_argument("--output", type=Path, default=HUMAN_DIR / "Hanoi_Human_Evaluation.html")
    args = parser.parse_args(argv)
    if args.output.suffix.lower() != ".html":
        parser.error("--output must end in .html")
    content = render_html(make_bundle(args.config))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")
    print(f"HTML: {args.output.resolve()}")
    print("Open the HTML file in a browser. Use the JSON download button to save results.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
