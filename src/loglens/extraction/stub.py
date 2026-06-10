"""Offline stub extractor.

Returns deterministic fixtures transcribed from the sample ``driver.pdf`` so the
whole pipeline (resolve, review, export) can be exercised with no API key. For
pages without a fixture it returns an empty sheet. Fields the real OCR would be
unsure about are given a low confidence so the review UI highlights them.
"""

from __future__ import annotations

from ..models import FieldValue, Sheet, TripRow

_HI = 96.0  # confidently read
_LO = 42.0  # messy / uncertain reading


def _f(value: str | None, low: bool = False) -> FieldValue:
    if value is None:
        return FieldValue()
    return FieldValue(value=value, confidence=_LO if low else _HI, source="ocr")


def _row(date, place, start, end, trailer, bol, code, uncertain=None):
    uncertain = set(uncertain or [])
    return TripRow(
        date=_f(date, "date" in uncertain),
        place_raw=_f(place, "place_raw" in uncertain),
        start_miles=_f(start, "start_miles" in uncertain),
        end_miles=_f(end, "end_miles" in uncertain),
        trailer_no=_f(trailer, "trailer_no" in uncertain),
        bol_ticket=_f(bol, "bol_ticket" in uncertain),
        code=_f(code, "code" in uncertain),
    )


_FIXTURES: dict[int, Sheet] = {
    0: Sheet(
        page_index=0,
        status="done",
        driver=_f("Joe Vail"),
        truck_no=_f("295-581"),
        beg_odometer=_f("255443"),
        end_odometer=_f("255864"),
        total_miles=_f("421"),
        rows=[
            _row("6-26", "CH", "255443", "255477", "02188", None, "S"),
            _row(None, "Emerson Inno", "255477", "255539", "0216", "512717", None, ["place_raw"]),
            _row(None, "cherry", "255539", "255540", "12345", None, "MT"),
            _row(None, "CH", "255540", "255601", "02203", None, "S"),
            _row(None, "Premium 34", "255601", "255745", "15531", "213408", None),
            _row(None, "Nidwoodfiber Decatur", "255745", "255749", "5531", None, "MT", ["place_raw"]),
            _row(None, "home", "255749", "255864", "16689", "590026", "S"),
        ],
    ),
    1: Sheet(
        page_index=1,
        status="done",
        driver=_f("Jeremy"),
        truck_no=_f("23029"),
        beg_odometer=_f("241228"),
        end_odometer=_f("241534"),
        total_miles=_f("306"),
        rows=[
            _row("6-1", "Avanti", "241226", "241349", "02133", "5121894", None),
            _row(None, "Rem", "241349", "241422", "02184", "A96226", "S"),
            _row(None, "Interplast", "241422", "241425", "17524", "213352", None),
            _row(None, "Rem", "241425", "241428", None, None, "MT", ["trailer_no"]),
            _row(None, "CH", "241428", "241477", "02184", "A96226", "S"),
            _row(None, "Lakeside", "241477", "241507", "17614", "5121554", None),
            _row(None, "Random Hse", "241507", "241512", "07227", None, "MT", ["place_raw"]),
            _row(None, "CH", "241512", "241534", "17509", "504690", "S"),
        ],
    ),
    2: Sheet(
        page_index=2,
        status="done",
        driver=_f("Mark"),
        truck_no=_f("455510"),
        beg_odometer=_f("373623"),
        end_odometer=_f("373983"),
        total_miles=_f("360"),
        rows=[
            _row("6-2-26", "CSI", "373623", "373681", "17585", "2131351", None),
            _row(None, "Random Hse", "373681", "373687", "17585", None, "MT"),
            _row(None, "CH", "373687", "373709", "17632", None, "S"),
            _row(None, "FFT Lok K", "373709", "373722", "06271", None, "MT", ["place_raw"]),
            _row(None, "CSL", "373722", "373849", "12354", "5121825", None),
            _row(None, "CH", "373849", "373983", "06266", None, "MT"),
        ],
    ),
    3: Sheet(
        page_index=3,
        status="done",
        driver=_f("Rob S"),
        truck_no=_f("380285"),
        beg_odometer=_f("509525"),
        end_odometer=_f("509920"),
        total_miles=_f("395"),
        rows=[
            _row("6-2-26", "CH", "509525", "509551", "17506", "504611", "S"),
            _row(None, "Blue Buffalo", "509551", "509664", "08333", "5121495", None),
            _row(None, "Honda", "509664", "509745", "17598", None, "MT"),
            _row(None, "CH", "509745", "509843", "07238", "504612", "S"),
            _row(None, "T+LN", "509843", "509859", "02656", "5121887", None, ["place_raw"]),
            _row(None, "Cat Veterans", "509859", "509871", "01625", None, "MT"),
            _row(None, "CH", "509871", "509884", "17550", "504613", "S"),
            _row(None, "IPC Delphi", "509884", "509919", "08339", "5121756", None),
            _row(None, "Delphi", "509919", "509920", "17490", "504614", "S"),
        ],
    ),
}


class StubExtractor:
    def extract(self, png_bytes: bytes, page_index: int) -> Sheet:
        fixture = _FIXTURES.get(page_index)
        if fixture is not None:
            return fixture.model_copy(deep=True)
        return Sheet(page_index=page_index, status="done")

    def verify(self) -> tuple[bool, str]:
        return True, "stub provider (offline fixtures, no API call)"
