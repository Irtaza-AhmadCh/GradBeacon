from __future__ import annotations

import re
import unicodedata

_TRANS = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", "\u00a0": " ",
                        "\u2009": " ", "\u202f": " "})


def normalize_for_match(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").translate(_TRANS).lower()
    s = re.sub(r"[*_`#|]", "", s)
    s = re.sub(r"\\([\-\.\$])", r"\1", s)  # markdown escapes
    return re.sub(r"\s+", " ", s).strip()
