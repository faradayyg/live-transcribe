"""
Bible book name dictionary and spoken-form normalisation.

Books are keyed by their canonical name.  Each entry contains:
  - aliases   : common abbreviations (case-insensitive matched)
  - spoken    : spoken/dictated forms (e.g. "first corinthians")
  - chapters  : total chapter count (for validation)
  - prefix    : numeric prefix as int, or None
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Ordinal word → digit mapping
# ---------------------------------------------------------------------------

ORDINAL_WORDS: dict[str, int] = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "1st": 1,
    "2nd": 2,
    "3rd": 3,
    "4th": 4,
}

# ---------------------------------------------------------------------------
# Book dictionary
# ---------------------------------------------------------------------------

BOOKS: list[dict] = [
    # Old Testament
    {"canonical": "Genesis",       "prefix": None, "chapters": 50,  "aliases": ["Gen","Ge","Gn"],             "spoken": ["genesis"]},
    {"canonical": "Exodus",        "prefix": None, "chapters": 40,  "aliases": ["Exo","Ex","Exod"],           "spoken": ["exodus"]},
    {"canonical": "Leviticus",     "prefix": None, "chapters": 27,  "aliases": ["Lev","Le","Lv"],             "spoken": ["leviticus"]},
    {"canonical": "Numbers",       "prefix": None, "chapters": 36,  "aliases": ["Num","Nu","Nm","Nb"],        "spoken": ["numbers"]},
    {"canonical": "Deuteronomy",   "prefix": None, "chapters": 34,  "aliases": ["Deu","De","Dt","Deut"],      "spoken": ["deuteronomy"]},
    {"canonical": "Joshua",        "prefix": None, "chapters": 24,  "aliases": ["Jos","Josh"],                "spoken": ["joshua"]},
    {"canonical": "Judges",        "prefix": None, "chapters": 21,  "aliases": ["Jdg","Judg","Jg"],           "spoken": ["judges"]},
    {"canonical": "Ruth",          "prefix": None, "chapters":  4,  "aliases": ["Rut","Ru"],                  "spoken": ["ruth"]},
    {"canonical": "1 Samuel",      "prefix": 1,    "chapters": 31,  "aliases": ["1Sa","1Sam","1 Sam","1S"],   "spoken": ["first samuel","1 samuel"]},
    {"canonical": "2 Samuel",      "prefix": 2,    "chapters": 24,  "aliases": ["2Sa","2Sam","2 Sam","2S"],   "spoken": ["second samuel","2 samuel"]},
    {"canonical": "1 Kings",       "prefix": 1,    "chapters": 22,  "aliases": ["1Ki","1Kgs","1 Kgs","1K"],   "spoken": ["first kings","1 kings"]},
    {"canonical": "2 Kings",       "prefix": 2,    "chapters": 25,  "aliases": ["2Ki","2Kgs","2 Kgs","2K"],   "spoken": ["second kings","2 kings"]},
    {"canonical": "1 Chronicles",  "prefix": 1,    "chapters": 29,  "aliases": ["1Ch","1Chr","1 Chr","1Chron"],"spoken": ["first chronicles","1 chronicles"]},
    {"canonical": "2 Chronicles",  "prefix": 2,    "chapters": 36,  "aliases": ["2Ch","2Chr","2 Chr","2Chron"],"spoken": ["second chronicles","2 chronicles"]},
    {"canonical": "Ezra",          "prefix": None, "chapters": 10,  "aliases": ["Ezr"],                       "spoken": ["ezra"]},
    {"canonical": "Nehemiah",      "prefix": None, "chapters": 13,  "aliases": ["Neh","Ne"],                  "spoken": ["nehemiah"]},
    {"canonical": "Esther",        "prefix": None, "chapters": 10,  "aliases": ["Est","Es"],                  "spoken": ["esther"]},
    {"canonical": "Job",           "prefix": None, "chapters": 42,  "aliases": ["Job","Jb"],                  "spoken": ["job"]},
    {"canonical": "Psalms",        "prefix": None, "chapters": 150, "aliases": ["Ps","Psa","Pss","Psalm"],    "spoken": ["psalms","psalm","the psalms"]},
    {"canonical": "Proverbs",      "prefix": None, "chapters": 31,  "aliases": ["Pro","Pr","Prv","Prov"],     "spoken": ["proverbs"]},
    {"canonical": "Ecclesiastes",  "prefix": None, "chapters": 12,  "aliases": ["Ecc","Ec","Qoh","Eccl"],     "spoken": ["ecclesiastes"]},
    {"canonical": "Song of Solomon","prefix": None,"chapters":  8,  "aliases": ["SOS","Song","SS","Sng"],     "spoken": ["song of solomon","song of songs","the song"]},
    {"canonical": "Isaiah",        "prefix": None, "chapters": 66,  "aliases": ["Isa","Is"],                  "spoken": ["isaiah"]},
    {"canonical": "Jeremiah",      "prefix": None, "chapters": 52,  "aliases": ["Jer","Je","Jr"],             "spoken": ["jeremiah"]},
    {"canonical": "Lamentations",  "prefix": None, "chapters":  5,  "aliases": ["Lam","La"],                  "spoken": ["lamentations"]},
    {"canonical": "Ezekiel",       "prefix": None, "chapters": 48,  "aliases": ["Eze","Ezk","Ezek"],          "spoken": ["ezekiel"]},
    {"canonical": "Daniel",        "prefix": None, "chapters": 12,  "aliases": ["Dan","Da","Dn"],             "spoken": ["daniel"]},
    {"canonical": "Hosea",         "prefix": None, "chapters": 14,  "aliases": ["Hos","Ho"],                  "spoken": ["hosea"]},
    {"canonical": "Joel",          "prefix": None, "chapters":  3,  "aliases": ["Joe","Jl"],                  "spoken": ["joel"]},
    {"canonical": "Amos",          "prefix": None, "chapters":  9,  "aliases": ["Amo","Am"],                  "spoken": ["amos"]},
    {"canonical": "Obadiah",       "prefix": None, "chapters":  1,  "aliases": ["Oba","Ob"],                  "spoken": ["obadiah"]},
    {"canonical": "Jonah",         "prefix": None, "chapters":  4,  "aliases": ["Jon","Jnh"],                 "spoken": ["jonah"]},
    {"canonical": "Micah",         "prefix": None, "chapters":  7,  "aliases": ["Mic","Mi"],                  "spoken": ["micah"]},
    {"canonical": "Nahum",         "prefix": None, "chapters":  3,  "aliases": ["Nah","Na"],                  "spoken": ["nahum"]},
    {"canonical": "Habakkuk",      "prefix": None, "chapters":  3,  "aliases": ["Hab","Hb"],                  "spoken": ["habakkuk"]},
    {"canonical": "Zephaniah",     "prefix": None, "chapters":  3,  "aliases": ["Zep","Zph","Zeph"],          "spoken": ["zephaniah"]},
    {"canonical": "Haggai",        "prefix": None, "chapters":  2,  "aliases": ["Hag","Hg"],                  "spoken": ["haggai"]},
    {"canonical": "Zechariah",     "prefix": None, "chapters": 14,  "aliases": ["Zec","Zch","Zech"],          "spoken": ["zechariah"]},
    {"canonical": "Malachi",       "prefix": None, "chapters":  4,  "aliases": ["Mal","Ml"],                  "spoken": ["malachi"]},
    # New Testament
    {"canonical": "Matthew",       "prefix": None, "chapters": 28,  "aliases": ["Mat","Mt","Matt"],           "spoken": ["matthew"]},
    {"canonical": "Mark",          "prefix": None, "chapters": 16,  "aliases": ["Mar","Mk","Mrk"],            "spoken": ["mark"]},
    {"canonical": "Luke",          "prefix": None, "chapters": 24,  "aliases": ["Luk","Lk"],                  "spoken": ["luke"]},
    {"canonical": "John",          "prefix": None, "chapters": 21,  "aliases": ["Joh","Jn","Jhn"],            "spoken": ["john"]},
    {"canonical": "Acts",          "prefix": None, "chapters": 28,  "aliases": ["Act","Ac"],                  "spoken": ["acts","acts of the apostles"]},
    {"canonical": "Romans",        "prefix": None, "chapters": 16,  "aliases": ["Rom","Ro","Rm"],             "spoken": ["romans"]},
    {"canonical": "1 Corinthians", "prefix": 1,    "chapters": 16,  "aliases": ["1Co","1Cor","1 Cor"],        "spoken": ["first corinthians","1 corinthians"]},
    {"canonical": "2 Corinthians", "prefix": 2,    "chapters": 13,  "aliases": ["2Co","2Cor","2 Cor"],        "spoken": ["second corinthians","2 corinthians"]},
    {"canonical": "Galatians",     "prefix": None, "chapters":  6,  "aliases": ["Gal","Ga"],                  "spoken": ["galatians"]},
    {"canonical": "Ephesians",     "prefix": None, "chapters":  6,  "aliases": ["Eph","Ephes"],               "spoken": ["ephesians"]},
    {"canonical": "Philippians",   "prefix": None, "chapters":  4,  "aliases": ["Php","Phil","Phi"],          "spoken": ["philippians"]},
    {"canonical": "Colossians",    "prefix": None, "chapters":  4,  "aliases": ["Col"],                       "spoken": ["colossians"]},
    {"canonical": "1 Thessalonians","prefix": 1,   "chapters":  5,  "aliases": ["1Th","1Thes","1 Thess"],     "spoken": ["first thessalonians","1 thessalonians"]},
    {"canonical": "2 Thessalonians","prefix": 2,   "chapters":  3,  "aliases": ["2Th","2Thes","2 Thess"],     "spoken": ["second thessalonians","2 thessalonians"]},
    {"canonical": "1 Timothy",     "prefix": 1,    "chapters":  6,  "aliases": ["1Ti","1Tim","1 Tim"],        "spoken": ["first timothy","1 timothy"]},
    {"canonical": "2 Timothy",     "prefix": 2,    "chapters":  4,  "aliases": ["2Ti","2Tim","2 Tim"],        "spoken": ["second timothy","2 timothy"]},
    {"canonical": "Titus",         "prefix": None, "chapters":  3,  "aliases": ["Tit","Ti"],                  "spoken": ["titus"]},
    {"canonical": "Philemon",      "prefix": None, "chapters":  1,  "aliases": ["Phm","Phlm"],                "spoken": ["philemon"]},
    {"canonical": "Hebrews",       "prefix": None, "chapters": 13,  "aliases": ["Heb","He"],                  "spoken": ["hebrews"]},
    {"canonical": "James",         "prefix": None, "chapters":  5,  "aliases": ["Jam","Jas","Jm"],            "spoken": ["james"]},
    {"canonical": "1 Peter",       "prefix": 1,    "chapters":  5,  "aliases": ["1Pe","1Pet","1 Pet"],        "spoken": ["first peter","1 peter"]},
    {"canonical": "2 Peter",       "prefix": 2,    "chapters":  3,  "aliases": ["2Pe","2Pet","2 Pet"],        "spoken": ["second peter","2 peter"]},
    {"canonical": "1 John",        "prefix": 1,    "chapters":  5,  "aliases": ["1Jo","1Jn","1 Jn"],          "spoken": ["first john","1 john"]},
    {"canonical": "2 John",        "prefix": 2,    "chapters":  1,  "aliases": ["2Jo","2Jn","2 Jn"],          "spoken": ["second john","2 john"]},
    {"canonical": "3 John",        "prefix": 3,    "chapters":  1,  "aliases": ["3Jo","3Jn","3 Jn"],          "spoken": ["third john","3 john"]},
    {"canonical": "Jude",          "prefix": None, "chapters":  1,  "aliases": ["Jud","Jde"],                 "spoken": ["jude"]},
    {"canonical": "Revelation",    "prefix": None, "chapters": 22,  "aliases": ["Rev","Re","Rv"],             "spoken": ["revelation","the revelation","the book of revelation"]},
]

# ---------------------------------------------------------------------------
# Look-up structures (built once at import time)
# ---------------------------------------------------------------------------

# canonical_name -> book entry
_BY_CANONICAL: dict[str, dict] = {b["canonical"]: b for b in BOOKS}

# lowercase key -> canonical name  (covers aliases and spoken forms)
_LOOKUP: dict[str, str] = {}

for _book in BOOKS:
    _canonical = _book["canonical"]
    _LOOKUP[_canonical.lower()] = _canonical
    for _alias in _book["aliases"]:
        _LOOKUP[_alias.lower()] = _canonical
    for _spoken in _book["spoken"]:
        _LOOKUP[_spoken.lower()] = _canonical


def resolve_book(raw: str) -> str | None:
    """
    Return the canonical book name for *raw*, or None if unrecognised.

    Handles:
    - Direct name / abbreviation  ("john", "Rom", "1Co")
    - Ordinal prefix normalisation ("first corinthians" → "1 Corinthians")
    """
    normalised = raw.strip().lower()

    # Direct hit
    if normalised in _LOOKUP:
        return _LOOKUP[normalised]

    # Try replacing ordinal word prefix with digit
    for word, digit in ORDINAL_WORDS.items():
        if normalised.startswith(word + " "):
            candidate = str(digit) + " " + normalised[len(word) + 1:]
            if candidate in _LOOKUP:
                return _LOOKUP[candidate]

    return None


def get_book_info(canonical: str) -> dict | None:
    return _BY_CANONICAL.get(canonical)
