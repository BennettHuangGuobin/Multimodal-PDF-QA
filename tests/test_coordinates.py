"""Independent PDF content-stream geometry and observed raster boundaries."""

import json

import pymupdf
import pytest

from multimodal_pdf_qa.pdf import inspect_pdf, page_geometry, transform_bbox
from multimodal_pdf_qa.prepare import prepare_pdf


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("cropped", [False, True])
@pytest.mark.parametrize("origin", [(0, 0), (10, 20)])
def test_pdf_coordinates_match_raw_stream_and_pixels(tmp_path, rotation, cropped, origin):
    source = tmp_path / "geometry.pdf"
    mx, my = origin
    crop = [mx + 20, my + 30, mx + 220, my + 290] if cropped else [mx, my, mx + 240, my + 320]
    raw_box = [mx + 70, my + 120, mx + 110, my + 160]
    with pymupdf.open() as doc:
        page = doc.new_page(width=240, height=320)
        doc.xref_set_key(page.xref, "MediaBox", f"[{mx} {my} {mx + 240} {my + 320}]")
        if cropped:
            doc.xref_set_key(page.xref, "CropBox", "[" + " ".join(map(str, crop)) + "]")
        stream = doc.get_new_xref()
        doc.update_object(stream, "<<>>")
        # The PDF stream, rather than a PyMuPDF drawing transform, defines truth.
        doc.update_stream(stream, f"1 0 0 rg {raw_box[0]} {raw_box[1]} 40 40 re f".encode())
        page.set_contents(stream)
        page.set_rotation(rotation)
        doc.save(source)
    original = source.read_bytes()
    directory = prepare_pdf(source, tmp_path / "out", dpi=144)
    row = json.loads((directory / "pages.jsonl").read_text())
    expected_unrotated = [raw_box[0] - crop[0], crop[3] - raw_box[3],
                          raw_box[2] - crop[0], crop[3] - raw_box[1]]
    converted = transform_bbox(raw_box, row["pdf_to_mupdf_matrix"])
    assert converted == pytest.approx(expected_unrotated)

    width, height = crop[2] - crop[0], crop[3] - crop[1]
    def rotate(x, y):
        return {0: (x, y), 90: (height - y, x),
                180: (width - x, height - y), 270: (y, width - x)}[rotation]

    corners = [rotate(x, y) for x in expected_unrotated[::2] for y in expected_unrotated[1::2]]
    expected_pixels = [2 * min(p[0] for p in corners), 2 * min(p[1] for p in corners),
                       2 * max(p[0] for p in corners), 2 * max(p[1] for p in corners)]
    assert transform_bbox(converted, row["mupdf_to_image_matrix"]) == pytest.approx(expected_pixels)
    pix = pymupdf.Pixmap(directory / row["page_image_path"])
    pixels = pix.samples
    red = [i // 3 for i in range(0, len(pixels), 3)
           if pixels[i] > 240 and pixels[i + 1] < 10 and pixels[i + 2] < 10]
    actual_pixels = [min(i % pix.width for i in red), min(i // pix.width for i in red),
                     max(i % pix.width for i in red) + 1, max(i // pix.width for i in red) + 1]
    assert actual_pixels == expected_pixels
    with pymupdf.open(source) as doc:
        page_geometry(doc[0], 144)
        assert not doc.is_dirty
        assert doc[0].rotation == rotation
    assert source.read_bytes() == original


def test_rerun_replaces_legacy_matrix(sample_pdf, tmp_path):
    output = tmp_path / "out"
    directory = prepare_pdf(sample_pdf, output, pages="4")
    index = directory / "pages.jsonl"
    row = json.loads(index.read_text())
    corrected = row["pdf_to_mupdf_matrix"]
    row["pdf_to_mupdf_matrix"] = [1, 0, 0, -1, 0, 260]
    index.write_text(json.dumps(row) + "\n")
    prepare_pdf(sample_pdf, output, pages="4")
    assert json.loads(index.read_text())["pdf_to_mupdf_matrix"] == corrected
    assert json.loads((directory / "manifest.json").read_text())["last_run"]["rendered"] == 1


@pytest.mark.parametrize("rotation", [0, 90])
@pytest.mark.parametrize("special", ["UserUnit", "outside_crop"])
def test_unsupported_geometry_is_explicit(tmp_path, rotation, special):
    source = tmp_path / "special.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=240, height=320)
        if special == "UserUnit":
            doc.xref_set_key(page.xref, "UserUnit", "2")
        else:
            doc.xref_set_key(page.xref, "CropBox", "[-10 30 220 350]")
        page.set_rotation(rotation)
        doc.save(source)
    with pytest.raises(ValueError, match="Unsupported page geometry"):
        inspect_pdf(source)
    directory = prepare_pdf(source, tmp_path / "out")
    row = json.loads((directory / "pages.jsonl").read_text())
    assert row["render_status"] == "failed"
    assert "Unsupported page geometry" in row["errors"][0]["message"]
    assert row["pdf_to_mupdf_matrix"] is None
    assert not json.loads((directory / "audit.json").read_text())["render_complete"]
