"""Name normalisation and album-title cleaning shared by the importer and the weekly sync."""
import re
import unicodedata

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE = re.compile(r"\s+")


def norm(name: str) -> str:
    """Matching key for a name: casefolded, accents stripped, & and 'and' unified, punctuation and
    extra whitespace removed. Only used for matching; stored names keep their original form."""
    s = unicodedata.normalize("NFKD", str(name or "")).casefold()
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("&", " and ").replace("+", " and ")
    s = _PUNCT.sub(" ", s.replace("'", "").replace("’", ""))
    return _SPACE.sub(" ", s).strip()


def compact(name: str) -> str:
    """norm() without spaces, so 'Gold Band' and 'Goldband' match."""
    return norm(name).replace(" ", "")


def loose(name: str) -> str:
    """compact() without a leading 'DJ' or 'The', for matching catalogue entries ('DJ Shadow')."""
    return re.sub(r"^(dj|the) (?=\S)", "", norm(name)).replace(" ", "")


def split_aliases(text: str) -> list[str]:
    return [a.strip() for a in str(text or "").split(";") if a.strip()]


# Suffixes that don't make a different album. Live recordings are kept apart on purpose.
_EDITION_WORDS = (
    r"remaster(ed)?|deluxe|standard|dolby|expanded|anniversary|bonus tracks?|special|edition|"
    r"version|reissue|super deluxe|collector'?s|mono|stereo|atmos|spatial audio|explicit|clean|"
    r"\d{4} mix|\d{4} remix|legacy|ep"
)
_EP = re.compile(r"\s+(?:[-–—]\s+)?EP$")
_BRACKET = re.compile(r"\s*[\(\[]([^\)\]]*)[\)\]]\s*$")
_DASH = re.compile(r"\s+[-–—]\s+([^-–—]+)$")
_EDITION = re.compile(rf"\b({_EDITION_WORDS})\b", re.IGNORECASE)
_LIVE = re.compile(r"\blive\b", re.IGNORECASE)


def _is_edition(suffix: str) -> bool:
    return bool(_EDITION.search(suffix)) and not _LIVE.search(suffix)


def clean_title(title: str) -> str:
    """'Album (Deluxe Edition)' -> 'Album'; 'Album - Remastered 2003' -> 'Album'; '(Live at X)' stays."""
    t = _EP.sub("", str(title or "").strip())
    for _ in range(4):  # titles can carry several suffixes: "X (Remastered) [Deluxe]"
        m = _BRACKET.search(t) or _DASH.search(t)
        if not m or not _is_edition(m.group(1)):
            break
        t = t[: m.start()].rstrip()
    return t or str(title or "").strip()


# "with" is left out on purpose: too many names contain it (Kill Them With Colour, Royal with Cheese).
_FEAT = re.compile(r"\s+(feat\.?|ft\.?|featuring)\s+", re.IGNORECASE)
_MULTI = re.compile(r"\s*(?:&|,|\s+x\s+|\s+and\s+|\s+vs\.?\s+|\s+met\s+|\s+en\s+)\s*", re.IGNORECASE)


def credit_parts(credit: str) -> list[str]:
    """Split a multi-artist credit into its listed artists ('A feat. B' -> ['A', 'B'])."""
    head, *_ = _FEAT.split(str(credit or "").strip(), maxsplit=1)
    parts = [p.strip() for p in _MULTI.split(head) if p and p.strip()]
    return parts or [str(credit or "").strip()]


def is_multi_credit(credit: str) -> bool:
    return len(credit_parts(credit)) > 1 or bool(_FEAT.search(str(credit or "")))
