"""
Generates two SYNTHETIC sample documents so you can test the /extract
endpoint immediately, without needing a real SSM certificate or IC on hand:

  sample_docs/sample_ssm_certificate.pdf   -> template: business_registration_ssm
  sample_docs/sample_ic_photocopy.png      -> template: ic_photocopies

These are plain generated text/shapes, NOT real documents or real people --
purely to exercise the PDF and image extraction paths end to end.

Run:  python notebooks/make_sample_docs.py
Requires: reportlab, pillow (in requirements-dev.txt)
"""
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "sample_docs"
OUT.mkdir(exist_ok=True)


def make_sample_pdf():
    path = OUT / "sample_ssm_certificate.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4

    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(width / 2, height - 80, "SURUHANJAYA SYARIKAT MALAYSIA")
    c.setFont("Helvetica-Bold", 13)
    c.drawCentredString(width / 2, height - 105, "CERTIFICATE OF INCORPORATION OF PRIVATE COMPANY")
    c.drawCentredString(width / 2, height - 120, "(Section 15, Companies Act 2016)")

    c.setFont("Helvetica", 11)
    lines = [
        "Company Name:  PRISMA NIAGA SDN. BHD.",
        "Registration Number:  202301045678 (1534291-W)",
        "Date of Incorporation:  14 March 2023",
        "",
        "This is to certify that PRISMA NIAGA SDN. BHD. is on the date shown above",
        "incorporated under the Companies Act 2016 as a company limited by shares",
        "and that the company is a private company.",
    ]
    y = height - 170
    for line in lines:
        c.drawString(90, y, line)
        y -= 22

    c.showPage()
    c.save()
    print(f"wrote {path}")


def make_sample_image():
    path = OUT / "sample_ic_photocopy.png"
    img = Image.new("RGB", (900, 560), color=(235, 240, 245))
    draw = ImageDraw.Draw(img)

    try:
        font_big = ImageFont.truetype("DejaVuSans-Bold.ttf", 26)
        font = ImageFont.truetype("DejaVuSans.ttf", 20)
    except OSError:
        font_big = ImageFont.load_default()
        font = ImageFont.load_default()

    draw.rectangle([20, 20, 880, 540], outline=(30, 60, 110), width=4)
    draw.text((50, 50), "MYKAD", font=font_big, fill=(20, 40, 80))
    draw.text((50, 110), "AHMAD FAIZAL BIN MOHD NOOR", font=font, fill=(0, 0, 0))
    draw.text((50, 150), "880615-14-5523", font=font, fill=(0, 0, 0))
    draw.text((50, 190), "NO 12, JALAN SETIA 5, TAMAN SETIA,", font=font, fill=(0, 0, 0))
    draw.text((50, 220), "40170 SHAH ALAM, SELANGOR", font=font, fill=(0, 0, 0))
    draw.text((50, 260), "WARGANEGARA", font=font, fill=(0, 0, 0))
    draw.text((50, 300), "LELAKI", font=font, fill=(0, 0, 0))

    img.save(path)
    print(f"wrote {path}")


if __name__ == "__main__":
    make_sample_pdf()
    make_sample_image()
