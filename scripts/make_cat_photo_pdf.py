from __future__ import annotations

import os
import shutil
import zipfile
from typing import Tuple, TypeAlias

from PIL import Image, ImageEnhance
from fpdf import FPDF

PillowImage: TypeAlias = Image.Image

# -----------------------------
# CONFIGURATION
# -----------------------------

ASSETS_DIR = r"./assets"
OUTPUT_DIR = "./output"
EXTRACT_DIR = r"./assets/extracted"
OUTPUT_FILE = "catio_cat_photos.pdf"
IMAGE_EXTS: Tuple[str, ...] = (".png", ".jpg", ".jpeg")

# Settings
COLS, ROWS = 2, 3
IMG_SIZE_IN = 3.0         # each box is 3" x 3"
SPACING_IN  = 0.5         # space between boxes
BORDER_PT   = 2.5         # border thickness
BRIGHTEN    = 1.3         # 1.0=no change; >1.0 brighter
CONTRAST    = 1.4         # small pop
ADAPTIVE_BRIGHTNESS = False  # set True to brighten only dark photos

# Derived constants
MM_PER_IN = 25.4
PT_TO_MM  = 0.352778
img_size_mm = IMG_SIZE_IN * MM_PER_IN
spacing_mm  = SPACING_IN  * MM_PER_IN

# US Letter page (8.5 x 11 in) in mm
page_w, page_h = 215.9, 279.4
grid_w = COLS * img_size_mm + (COLS - 1) * spacing_mm
grid_h = ROWS * img_size_mm + (ROWS - 1) * spacing_mm
x_margin = (page_w - grid_w) / 2
y_margin = (page_h - grid_h) / 2

# Pillow resampling fallback for older versions
try:
    RESAMPLING = Image.Resampling  # type: ignore[attr-defined]
except AttributeError:
    RESAMPLING = Image

LANCZOS = getattr(RESAMPLING, "LANCZOS", Image.LANCZOS)

# Create output folder if it doesn't exist
os.makedirs(EXTRACT_DIR, exist_ok=True)


# -----------------------------
# HELPER FUNCTIONS
# -----------------------------

def is_dark(im: PillowImage) -> bool:
    """Return True if the image appears dark based on mean pixel luminance.

    This supports adaptive brightness behavior, allowing us to brighten
    only especially dark photos rather than everything.
    """
    small = im.resize((64, 64)).convert("L")
    mean = sum(small.getdata()) / (64 * 64)
    return mean < 95  # tweak if needed


def extract_all_zip_images() -> None:
    """Extract all images from zip files found in ASSETS_DIR into EXTRACT_DIR.

    - Only ZIP files are processed
    - Only supported image extensions are copied out
    - Avoids __MACOSX garbage
    """
    for entry in os.scandir(ASSETS_DIR):
        # skip anything not a .zip
        if not (entry.is_file() and entry.name.lower().endswith(".zip")):
            continue

        with zipfile.ZipFile(entry.path) as zip_ref:
            for info in zip_ref.infolist():
                name = info.filename

                # skip directories or macOS junk
                if info.is_dir() or "__MACOSX" in name:
                    continue

                # skip things that are not images
                if not name.lower().endswith(IMAGE_EXTS):
                    continue

                dest_name = os.path.basename(name)
                dest_path = os.path.join(EXTRACT_DIR, dest_name)

                # use shutil to stream safely
                with zip_ref.open(info) as src, open(dest_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)


def collect_extracted_images() -> list[str]:
    """Return a sorted list of extracted image files.

    This is run after extraction, so all files present
    in EXTRACT_DIR are assumed to be ready to use.
    """
    # collect & sort images (ignore junk)
    return sorted(
        [f for f in os.listdir(EXTRACT_DIR) if f.lower().endswith(IMAGE_EXTS)],
        key=str.lower
    )


def process_image(path: str) -> PillowImage:
    """Open, normalize, optionally brighten, and scale one image.

    Returns:
        PIL.Image.Image — ready for PDF insertion
    """
    with Image.open(path) as im:

        # normalize mode
        if im.mode in ("RGBA", "P"):
            im = im.convert("RGB")

        # brightness logic
        if ADAPTIVE_BRIGHTNESS:
            if is_dark(im):
                im = ImageEnhance.Brightness(im).enhance(BRIGHTEN)
                im = ImageEnhance.Contrast(im).enhance(CONTRAST)
        else:
            im = ImageEnhance.Brightness(im).enhance(BRIGHTEN)
            im = ImageEnhance.Contrast(im).enhance(CONTRAST)

        # scale to fit inside 3"x3" box (keep aspect)
        box_px = int(300 * IMG_SIZE_IN)  # 300dpi target
        im.thumbnail((box_px, box_px), LANCZOS)

        return im.copy()


def build_pdf(images: list[str]) -> FPDF:
    """Populate a multi-page PDF using provided images.

    Args:
        images: list of filenames inside EXTRACT_DIR

    Returns:
        FPDF object populated with all pages
    """
    pdf = FPDF(unit="mm", format=[page_w, page_h])
    pdf.set_auto_page_break(auto=False)

    count = 0
    page_num = 0

    for img_file in images:

        # start new page when full
        if count % (COLS * ROWS) == 0:
            pdf.add_page()
            page_num += 1

        col = count % COLS
        row = (count // COLS) % ROWS

        path = os.path.join(EXTRACT_DIR, img_file)
        im = process_image(path)

        # save uniquely named temp for FPDF
        temp_path = f"__tmp_{count}.jpg"
        im.save(temp_path, "JPEG", quality=95)

        # box position (with spacing)
        x = x_margin + col * (img_size_mm + spacing_mm)
        y = y_margin + row * (img_size_mm + spacing_mm)

        img_w = im.width  * MM_PER_IN / 300.0
        img_h = im.height * MM_PER_IN / 300.0

        # center inside square box
        img_x = x + (img_size_mm - img_w) / 2.0
        img_y = y + (img_size_mm - img_h) / 2.0

        # draw image
        pdf.image(temp_path, x=img_x, y=img_y, w=img_w, h=img_h)

        # draw 3pt border
        pdf.set_draw_color(0, 0, 0)
        pdf.set_line_width(BORDER_PT * PT_TO_MM)
        pdf.rect(x, y, img_size_mm, img_size_mm)

        os.remove(temp_path)
        count += 1

        # page number footer
        if count % (COLS * ROWS) == 0 or count == len(images):
            pdf.set_font("Arial", size=10)
            pdf.set_text_color(100, 100, 100)
            pdf.text(x=page_w - 20, y=page_h - 10, txt=str(page_num))

    return pdf


def cleanup_extracted_images() -> None:
    """Delete extracted files after PDF output completes.

    Prevents disk clutter & accidental git commits.
    """
    for entry in os.scandir(EXTRACT_DIR):
        if entry.is_file() and entry.name.lower().endswith(IMAGE_EXTS):
            os.remove(entry.path)


def main() -> None:
    """Run full PDF pipeline: extract, collect, build, output, clean."""
    extract_all_zip_images()
    images = collect_extracted_images()
    pdf = build_pdf(images)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    pdf.output(os.path.join(OUTPUT_DIR, OUTPUT_FILE))

    cleanup_extracted_images()


if __name__ == "__main__":
    main()
