# =====================================================================
#  Quran Light post generator
#  ---------------------------------------------------------------
#  HOW TO USE:
#   1) Edit CONTENT below with your new hadith/quote text.
#   2) (Optional) put a photo file in this same folder and set PHOTO
#      to its filename, to replace the profile picture circle.
#   3) Run:   python3 generate_post.py
#   4) Find the result as final_post.png in this same folder.
#
#  Requirements: Python 3 + Pillow  ->  pip install pillow
#  (also pip install arabic-reshaper python-bidi — used automatically
#   as a fallback on systems where Pillow's Raqm text engine isn't
#   available, e.g. some Windows Python installs)
# =====================================================================
CONTENT=" الْمَلِكَ فَجَلَسَ إِلَيْهِ كَمَا كَانَ يَجْلَسُ، فَقَالَ لَهُ الْمَلِكُ: مَنْ رَدَّ عَلَيْكَ بَصَرَكَ؟ قَالَ: رَبِّي. قَالَ: وَلَكَ رَبٌّ غَيْرِي؟ قَالَ: رَبِّي وَرَبُّكَ اللَّهُ. فَأَخَذَهُ فَلَمْ يَزَلْ يُعَذِّبُهُ حَتَّى دَلَّ عَلَى الْغُلاَمِ، فَجِيءَ بِالْغُلاَمِ، فَقَالَ لَهُ الْمَلِكُ: أَيْ بُنَيَّ قَدْ بَلَغَ مِنْ سِحْرِكَ مَا تُبْرِئُ الأَكْمَهَ وَالأَبْرَصَ وَتَفْعَلُ وَتَفعلُ! فَقَالَ: إِنِّي لاَ أَشْفِي أَحَدًا، إِنَّمَا يَشْفِي اللَّهُ. فَأَخَذَهُ فَلَمْ يَزَلْ يُعَذِّبُهُ حَتَّى دَلَّ عَلَى الرَّاهِبِ، فَجِيءَ بِالرَّاهِبِ فَقِيلَ لَهُ: ارْجِعْ عَنْ دِينِكَ. فَأَبَى، فَدَعَا بِالْمِشْشَارِ، فَوَضَعَ الْمِشْشَارَ فِي مَفْرِقِ رَأْسِهِ، فَشَقَّهُ حَتَّى وَقَعَ شِقَّاهُ، ثُمَّ جِيءَ بِجَلِيسِ الْمَلِكِ فَقِيلَ لَهُ: ارْجِعْ عَنْ دِينِكَ. فَأَبَى فَوَضَعَ الْمِشْشَارَ فِي مَفْرِقِ رَأْسِهِ، فَشَقَّهُ بِهِ حَتَّى وَقَعَ شِقَّاهُ"
PHOTO = ""   # e.g. "myphoto.jpg" — leave as None to keep the original photo

# =====================================================================
# Nothing below this line needs to be touched.
# =====================================================================

import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
BG_PATH = os.path.join(HERE, "background.png")
CAIRO_PATH = os.path.join(HERE, "Cairo.ttf")
AMIRI_PATH = os.path.join(HERE, "Amiri-Bold.ttf")
OUTPUT_PATH = os.path.join(HERE, "final_post.png")

# Measured box (text panel) coordinates on the 1254x1254 artwork
BOX = (179, 611, 1084, 1054)          # left, top, right, bottom
PANEL_COLOR = (2, 24, 15)             # matches the artwork's dark panel
TEXT_COLOR = (243, 230, 198)          # cream/gold text color

# Profile photo circle coordinates (square bounding box)
CIRCLE = (552, 34, 552 + 148, 34 + 148)


# ---------------------------------------------------------------------
# Detect whether this Pillow build has working Raqm support. If yes, we
# use the Cairo font directly (closest match to the original artwork).
# If not, we fall back to arabic-reshaper + python-bidi with the Amiri
# font, which works with plain Pillow and no extra system libraries.
# ---------------------------------------------------------------------
def _detect_raqm():
    try:
        f = ImageFont.truetype(CAIRO_PATH, 40, layout_engine=ImageFont.Layout.RAQM)
        Image.new("RGB", (10, 10)).convert("RGB")
        d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        d.textlength("ا", font=f, direction="rtl")
        return True
    except Exception:
        return False


HAS_RAQM = _detect_raqm()

if not HAS_RAQM:
    import arabic_reshaper
    from bidi.algorithm import get_display


def load_font(size):
    if HAS_RAQM:
        font = ImageFont.truetype(CAIRO_PATH, size, layout_engine=ImageFont.Layout.RAQM)
        try:
            font.set_variation_by_axes([800, 0])   # bold weight on the Cairo variable font
        except Exception:
            pass
        return font
    else:
        return ImageFont.truetype(AMIRI_PATH, size)


def shape(text):
    """Return the text ready to draw with PIL's default (non-Raqm) layout."""
    if HAS_RAQM:
        return text
    return get_display(arabic_reshaper.reshape(text))


def measure(draw, text, font):
    if HAS_RAQM:
        return draw.textlength(text, font=font, direction="rtl")
    return draw.textlength(shape(text), font=font)


def draw_line(draw, xy, text, font, fill):
    if HAS_RAQM:
        draw.text(xy, text, font=font, fill=fill, direction="rtl")
    else:
        draw.text(xy, shape(text), font=font, fill=fill)


def line_bbox(draw, text, font):
    if HAS_RAQM:
        return draw.textbbox((0, 0), text, font=font, direction="rtl")
    return draw.textbbox((0, 0), shape(text), font=font)


def wrap_text(text, font, max_width, draw):
    words = text.split(" ")
    lines, current = [], []
    for w in words:
        trial = " ".join(current + [w])
        width = measure(draw, trial, font)
        if width <= max_width or not current:
            current.append(w)
        else:
            lines.append(" ".join(current))
            current = [w]
    if current:
        lines.append(" ".join(current))
    return lines


def fit_text(text, draw, max_width, max_height, start_size=64, min_size=28, line_height_ratio=1.55):
    size = start_size
    while size >= min_size:
        font = load_font(size)
        lines = wrap_text(text, font, max_width, draw)
        line_height = int(size * line_height_ratio)
        if line_height * len(lines) <= max_height:
            return font, lines, line_height
        size -= 2
    font = load_font(min_size)
    lines = wrap_text(text, font, max_width, draw)
    return font, lines, int(min_size * line_height_ratio)


def circle_mask(size):
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    return mask


def generate(content_text, photo_path=None, output_path=OUTPUT_PATH):
    im = Image.open(BG_PATH).convert("RGB")
    draw = ImageDraw.Draw(im)

    left, top, right, bottom = BOX



    pad_x, pad_y = 45, 20
    max_width = (right - left) - 2 * pad_x
    max_height = (bottom - top) - 2 * pad_y

    full_text = "\u201D" + content_text.strip() + "\u201C"
    font, lines, line_height = fit_text(full_text, draw, max_width, max_height)

    total_height = line_height * len(lines)
    cy = top + (bottom - top) // 2 - total_height // 2
    cx = left + (right - left) // 2

    for line in lines:
        bbox = line_bbox(draw, line, font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = cx - w // 2 - bbox[0]
        y = cy + (line_height - h) // 2 - bbox[1]
        draw_line(draw, (x, y), line, font, TEXT_COLOR)
        cy += line_height

    if photo_path:
        cl, ct, cr, cb = CIRCLE
        size = cr - cl
        photo = Image.open(photo_path).convert("RGB")
        w, h = photo.size
        m = min(w, h)
        photo = photo.crop(((w - m) // 2, (h - m) // 2, (w - m) // 2 + m, (h - m) // 2 + m))
        photo = photo.resize((size, size), Image.LANCZOS)
        im.paste(photo, (cl, ct), circle_mask(size))

    im.save(output_path)
    return output_path


if __name__ == "__main__":
    if not HAS_RAQM:
        print("(Using the Amiri fallback font — install/upgrade Pillow with Raqm support for the Cairo font look.)")
    photo_full_path = os.path.join(HERE, PHOTO) if PHOTO else None
    result = generate(CONTENT, photo_full_path)
    print("Saved:", result)