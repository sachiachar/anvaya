"""A tiny PDF writer for the synthetic certificates: one page of text lines, each in one of the
standard PDF fonts. No library needed; Gemini reads PDFs like photos."""

from __future__ import annotations

FONTS = {"sans": "Helvetica", "serif": "Times-Roman", "mono": "Courier", "bold": "Helvetica-Bold"}


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def page(lines: list[tuple[str, str, int]]) -> bytes:
    """A one-page A4 PDF. Each line is (text, font name from FONTS, size); lines run down the page."""
    ops, y = [], 790
    for text, font, size in lines:
        ops.append(f"BT /F{list(FONTS).index(font) + 1} {size} Tf 60 {y} Td ({_escape(text)}) Tj ET")
        y -= size + 9
    stream = "\n".join(ops).encode("latin-1", "replace")
    fonts = " ".join(f"/F{i + 1} {i + 5} 0 R" for i in range(len(FONTS)))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << {fonts} >> >> "
        f"/Contents 4 0 R >>".encode(),
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        *(f"<< /Type /Font /Subtype /Type1 /BaseFont /{name} >>".encode() for name in FONTS.values()),
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)
