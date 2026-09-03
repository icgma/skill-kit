"""Format conversion commands"""

import os
import shutil
import subprocess
from pathlib import Path
from pdf import Output


# Supported input formats
SUPPORTED_FORMATS = {
    # Office documents
    ".docx", ".doc", ".odt", ".rtf",
    # Presentations
    ".pptx", ".ppt", ".odp",
    # Spreadsheets
    ".xlsx", ".xls", ".ods", ".csv",
    # Other
    ".txt", ".html", ".htm",
}


def _find_libreoffice() -> str:
    """Find LibreOffice executable.

    Order: PATH lookup (all platforms) → macOS app bundle / common unix paths →
    Windows install dirs derived from environment variables.
    """
    # 1. PATH lookup (all platforms)
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found

    # 2. macOS app bundle and common unix locations
    unix_paths = [
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        "/opt/homebrew/bin/soffice",
        "/usr/local/bin/soffice",
    ]
    for p in unix_paths:
        if Path(p).exists():
            return p

    # 3. Windows default install locations (env-derived, not hardcoded)
    if os.name == "nt":
        for root in (
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("LOCALAPPDATA"),
        ):
            if not root:
                continue
            candidate = Path(root) / "LibreOffice" / "program" / "soffice.exe"
            if candidate.exists():
                return str(candidate)

    return None


def convert_to_pdf(input_path: str, output_path: str = None):
    """Convert file to PDF"""
    path = Output.check_file(input_path)

    # Check format support
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_FORMATS:
        Output.error(
            "UnsupportedFormat",
            f"Unsupported format: {suffix}",
            hint=f"Supported formats: {', '.join(sorted(SUPPORTED_FORMATS))}"
        )

    # Find LibreOffice
    soffice = _find_libreoffice()
    if not soffice:
        Output.error(
            "DependencyMissing",
            "LibreOffice not found",
            hint="Please install LibreOffice: https://www.libreoffice.org/download/"
        )

    # Determine output path
    if output_path:
        out_dir = Path(output_path).parent
        out_name = Path(output_path).stem
    else:
        out_dir = path.parent
        out_name = path.stem

    out_dir.mkdir(parents=True, exist_ok=True)

    # Build command
    cmd = [
        soffice,
        "--headless",
        "--convert-to", "pdf",
        "--outdir", str(out_dir),
        str(path)
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120
        )

        if result.returncode != 0:
            stderr = result.stderr.strip()
            Output.error("ConvertError", f"Conversion failed: {stderr or 'Unknown error'}", code=4)

    except subprocess.TimeoutExpired:
        Output.error("Timeout", "Conversion timeout (>120s)", code=4)
    except Exception as e:
        Output.error("ConvertError", f"Conversion failed: {e}", code=4)

    # LibreOffice output filename is fixed to original_name.pdf
    generated_pdf = out_dir / f"{path.stem}.pdf"

    # If a different output name was specified, move into place.
    # os.replace (not Path.rename) so an existing target is overwritten
    # atomically on both POSIX and Windows.
    if output_path and Path(output_path).name != generated_pdf.name:
        final_path = Path(output_path)
        os.replace(generated_pdf, final_path)
    else:
        final_path = generated_pdf

    if not final_path.exists():
        Output.error("ConvertError", "Converted PDF file was not generated", code=4)

    Output.success({
        "input": str(path),
        "output": str(final_path),
        "format": suffix
    })
