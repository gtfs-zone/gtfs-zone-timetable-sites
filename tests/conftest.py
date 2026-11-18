import io
import zipfile
from pathlib import Path

from gtfs_zone_timetable_sites.messages import compile_catalogs


def make_zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return buffer.getvalue()


FIXTURES = Path(__file__).parent / "fixtures"


def fixture_files(name: str) -> dict[str, str]:
    """The text files in tests/fixtures/<name>/ by file name."""
    return {path.name: path.read_text() for path in (FIXTURES / name).glob("*.txt")}


def fixture_zip(name: str) -> bytes:
    return make_zip(fixture_files(name))


def pytest_sessionstart() -> None:
    compile_catalogs()
