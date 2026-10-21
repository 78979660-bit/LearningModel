from __future__ import annotations

import io
import subprocess
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

from study_app.core import subject_pdf_pipeline as pipeline


TSV = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    "1\t1\t0\t0\t0\t0\t0\t0\t200\t100\t-1\t\n"
    "5\t1\t1\t1\t1\t1\t20\t10\t80\t20\t92\t数学\n"
    '5\t1\t1\t1\t1\t2\t100\t10\t60\t20\t88\t"2+3=5"\n'
)


def image_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (200, 100), "white").save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.parametrize("directory", ["中文语言包", "中文 用户 OCR 语言包"])
def test_pdf_ocr_preserves_path_at_real_process_boundary(tmp_path, monkeypatch, directory):
    tessdata = tmp_path / directory
    tessdata.mkdir()
    engine = tmp_path / "模拟 OCR 引擎.py"
    engine.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "argv=sys.argv[1:]\n"
        "assert argv[:2]==['stdin','stdout']\n"
        "assert Path(argv[argv.index('--tessdata-dir')+1]).is_dir()\n"
        "assert argv[argv.index('-l')+1]=='chi_sim+eng'\n"
        "assert sys.stdin.buffer.read().startswith(b'\\x89PNG')\n"
        f"sys.stdout.buffer.write({TSV.encode('utf-8')!r})\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(pipeline, "_local_tesseract_runtime", lambda: (sys.executable, tessdata, "chi_sim+eng"))
    run = subprocess.run

    def engine_process(argv, **kwargs):
        # Keep production arguments intact through a real native subprocess;
        # replace only the external engine with a deterministic test program.
        return run([argv[0], str(engine), *argv[1:]], **kwargs)

    monkeypatch.setattr(pipeline.subprocess, "run", engine_process)
    result = pipeline._default_ocr(image_bytes())
    assert result.text == '数学\n"2+3=5"'
    assert result.confidence == pytest.approx(0.9)
    assert result.blocks[0].bbox == pytest.approx((0.1, 0.1, 0.5, 0.3))
    assert len(result.blocks) == 2


def configure_runtime(monkeypatch, result=None, error=None):
    monkeypatch.setattr(pipeline, "_local_tesseract_runtime", lambda: ("engine.exe", "中文 语言包", "chi_sim"))

    def run(*_args, **_kwargs):
        if error:
            raise error
        return result

    monkeypatch.setattr(pipeline.subprocess, "run", run)


def test_pdf_ocr_reports_localized_engine_failure_without_decode_error(monkeypatch):
    configure_runtime(monkeypatch, SimpleNamespace(returncode=1, stdout=b"", stderr="缺少语言包".encode("gb18030")))
    monkeypatch.setattr(pipeline.locale, "getencoding", lambda: "gb18030")
    with pytest.raises(RuntimeError, match="本地 OCR 不可用") as failure:
        pipeline._default_ocr(image_bytes())
    assert isinstance(failure.value.__cause__, RuntimeError)
    assert "缺少语言包" in str(failure.value.__cause__)


def test_pdf_ocr_timeout_is_reported_as_unavailable(monkeypatch):
    configure_runtime(monkeypatch, error=subprocess.TimeoutExpired("engine.exe", 30))
    with pytest.raises(RuntimeError, match="TimeoutExpired") as failure:
        pipeline._default_ocr(image_bytes())
    assert isinstance(failure.value.__cause__, subprocess.TimeoutExpired)


def test_pdf_ocr_rejects_success_output_without_tsv_columns(monkeypatch):
    configure_runtime(monkeypatch, SimpleNamespace(returncode=0, stdout=b"not TSV", stderr=b""))
    with pytest.raises(RuntimeError, match="ValueError"):
        pipeline._default_ocr(image_bytes())


def test_pdf_ocr_empty_page_has_no_fabricated_blocks(monkeypatch):
    configure_runtime(monkeypatch, SimpleNamespace(returncode=0, stdout=TSV.splitlines()[0].encode()+b"\n", stderr=b""))
    result = pipeline._default_ocr(image_bytes())
    assert result.text == ""
    assert result.confidence == 0
    assert result.blocks == ()
