"""
app/services/staging.py
-------------------------
Sprint 3 (Multi-file Projects) foundation, shared by every router that
accepts an uploaded RTL project.

This consolidates the staging logic that already exists in
code/synthesis_runner.py and code/simulation_runner.py into one place, and
brings synthesis_runner.py's version up to the security bar
simulation_runner.py already met -- per 11_security_roadmap.md's note that
"synthesis_runner.py should be brought up to the same standard" (ZIP size,
file count, and extracted-size caps, checked BEFORE extractall()).
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from app.config import settings

RTL_EXTENSIONS = {".v", ".sv", ".vh", ".svh"}


class StagingError(Exception):
    pass


def stage_project(input_path: Path, work_dir: Path) -> list[Path]:
    """
    Stages a single RTL file or a ZIP project into work_dir and returns the
    list of staged RTL source files. Same contract as both existing runner
    modules so this can sit behind one shared upload endpoint.
    """
    work_dir.mkdir(parents=True, exist_ok=True)

    if input_path.suffix.lower() == ".zip":
        return _stage_zip_project(input_path, work_dir)

    if input_path.suffix.lower() in RTL_EXTENSIONS:
        dest = work_dir / input_path.name
        shutil.copy(input_path, dest)
        return [dest]

    raise StagingError(
        f"Unsupported input type '{input_path.suffix}'. Expected a .v/.sv file or a .zip project."
    )


def _stage_zip_project(zip_path: Path, work_dir: Path) -> list[Path]:
    zip_size = zip_path.stat().st_size
    if zip_size > settings.max_zip_size_bytes:
        raise StagingError(
            f"ZIP file is {zip_size / 1024 / 1024:.1f} MB. "
            f"Max allowed is {settings.max_zip_size_bytes / 1024 / 1024:.0f} MB."
        )

    extract_dir = work_dir / "rtl_src"
    extract_dir.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path) as zf:
            infolist = zf.infolist()

            if len(infolist) > settings.max_zip_file_count:
                raise StagingError(
                    f"ZIP contains {len(infolist)} files. "
                    f"Max allowed is {settings.max_zip_file_count}."
                )

            # Sum uncompressed sizes BEFORE extracting -- this is what
            # actually stops a ZIP bomb, since compressed size alone
            # doesn't reveal decompressed size.
            total_uncompressed = sum(member.file_size for member in infolist)
            if total_uncompressed > settings.max_extracted_size_bytes:
                raise StagingError(
                    f"ZIP would extract to {total_uncompressed / 1024 / 1024:.0f} MB, "
                    f"over the {settings.max_extracted_size_bytes / 1024 / 1024:.0f} MB limit."
                )

            # ZIP-slip / path-traversal guard.
            for member in infolist:
                member_path = extract_dir / member.filename
                if not str(member_path.resolve()).startswith(str(extract_dir.resolve())):
                    raise StagingError("Unsafe path in ZIP archive, aborting.")

            zf.extractall(extract_dir)
    except zipfile.BadZipFile as exc:
        raise StagingError(f"Could not read ZIP archive: {exc}") from exc

    rtl_files = sorted(
        p for p in extract_dir.rglob("*") if p.suffix.lower() in RTL_EXTENSIONS and p.is_file()
    )
    if not rtl_files:
        raise StagingError("ZIP project contained no .v/.sv RTL files.")

    return rtl_files
