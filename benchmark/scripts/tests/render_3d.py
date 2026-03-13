"""
OpenSCAD wrapper to render 6-8 views per 3D model (.stl/.obj/.3mf).

Usage:
    python render_3d.py --source ./data/raw_3d --output ./data/rendered_images
    python render_3d.py --source s3://bucket/raw/ --output s3://bucket/processed/images/
"""

import argparse
import subprocess
import tempfile
import shutil
from pathlib import Path


OPENSCAD_BIN = shutil.which("openscad") or "/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD"

# Camera angles: [translate_x, translate_y, translate_z, rot_x, rot_y, rot_z, distance]
VIEWS = [
    ("front",       "0,0,0,0,0,0,200"),
    ("back",        "0,0,0,0,0,180,200"),
    ("left",        "0,0,0,0,0,90,200"),
    ("right",       "0,0,0,0,0,270,200"),
    ("top",         "0,0,0,90,0,0,200"),
    ("bottom",      "0,0,0,270,0,0,200"),
    ("front_high",  "0,0,0,45,0,30,200"),
    ("back_low",    "0,0,0,30,0,210,200"),
]

SCAD_TEMPLATE = 'import("{file_path}");'


def render_model(model_path: Path, output_dir: Path, views: list[tuple[str, str]], img_size: str = "800,600"):
    output_dir.mkdir(parents=True, exist_ok=True)

    # Write a temp .scad file that imports the 3D model
    scad_content = SCAD_TEMPLATE.format(file_path=str(model_path.resolve()))
    scad_file = output_dir / "_temp_render.scad"
    scad_file.write_text(scad_content)

    for view_name, camera in views:
        out_png = output_dir / f"{view_name}.png"
        cmd = [
            OPENSCAD_BIN,
            "-o", str(out_png),
            "--camera", camera,
            "--imgsize", img_size,
            "--colorscheme", "Tomorrow Night",
            str(scad_file),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=60)
            print(f"  ✓ {view_name}.png")
        except subprocess.CalledProcessError as e:
            print(f"  ✗ {view_name} failed: {e.stderr.decode()[:200]}")
        except FileNotFoundError:
            print(f"  ✗ OpenSCAD not found at {OPENSCAD_BIN}. Install it or update OPENSCAD_BIN.")
            return

    scad_file.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Render 3D models to multi-view images via OpenSCAD")
    parser.add_argument("--source", required=True, help="Directory of .stl/.obj/.3mf files (local path)")
    parser.add_argument("--output", required=True, help="Output directory for rendered images")
    parser.add_argument("--views", type=int, default=8, choices=range(1, 9), help="Number of views (1-8)")
    parser.add_argument("--img-size", default="800,600", help="Image size as W,H")
    args = parser.parse_args()

    source = Path(args.source)
    output = Path(args.output)
    exts = {".stl", ".obj", ".3mf"}
    models = sorted(p for p in source.rglob("*") if p.suffix.lower() in exts)

    if not models:
        print(f"No 3D files found in {source}")
        return

    selected_views = VIEWS[: args.views]
    print(f"Found {len(models)} models, rendering {len(selected_views)} views each\n")

    for model_path in models:
        sample_id = model_path.stem
        sample_out = output / sample_id
        print(f"[{sample_id}]")
        render_model(model_path, sample_out, selected_views, args.img_size)
        print()

    print("Done.")


if __name__ == "__main__":
    main()