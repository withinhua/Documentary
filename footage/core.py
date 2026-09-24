"""Data model shared by every stage: visual requests, normalised candidates, licence policy."""
from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass, field
from typing import Any

SOURCE_LABELS = {
    "commons": "Wikimedia Commons",
    "openverse": "Openverse",
    "archive": "Internet Archive",
    "loc": "Library of Congress",
    "nara": "US National Archives",
    "smithsonian": "Smithsonian Open Access",
    "pexels": "Pexels",
    "pixabay": "Pixabay",
    "youtube": "YouTube",
    "local": "Local folder",
}

# Licence classes, best first. "other" covers free-to-use stock licences (Pexels, Pixabay) and
# anything else that is not CC; restrictions live in `license_flags`.
LICENSE_CLASSES = ("public-domain", "cc0", "cc-by", "cc-by-sa", "other", "unknown")
LICENSE_SCORE = {"public-domain": 1.0, "cc0": 1.0, "cc-by": 0.85, "cc-by-sa": 0.75, "other": 0.7,
                 "unknown": 0.3}


@dataclass
class Request:
    id: str
    kind: str = "photo"                 # photo | video | any
    subject: str = ""
    must_show: str = ""
    era: str = ""                       # "1980-1990", "1985", "" (no constraint)
    queries: dict[str, list[str]] = field(default_factory=dict)
    min_width: int = 1000

    @classmethod
    def from_dict(cls, d: dict) -> "Request":
        q = d.get("queries") or {}
        if isinstance(q, list):         # one list for every source
            q = {"*": q}
        return cls(id=str(d["id"]), kind=d.get("kind", "photo"), subject=d.get("subject", ""),
                   must_show=d.get("must_show", ""), era=str(d.get("era") or ""),
                   queries={k: [v] if isinstance(v, str) else list(v) for k, v in q.items()},
                   min_width=int(d.get("min_width") or (1000 if d.get("kind") != "video" else 640)))

    def queries_for(self, source: str) -> list[str]:
        """Source-specific queries; falls back to the generic list, then to the subject."""
        if source in self.queries:
            return [q for q in self.queries[source] if q]
        if "*" in self.queries:
            return [q for q in self.queries["*"] if q]
        return [self.subject] if self.subject else []

    def era_range(self) -> tuple[int, int] | None:
        years = [int(y) for y in re.findall(r"\b(1[5-9]\d\d|20\d\d)s?\b", self.era)]
        if not years:
            return None
        lo, hi = min(years), max(years)
        if "s" in self.era and len(years) == 1 and years[0] % 10 == 0:   # "1980s"
            hi = lo + 9
        return lo, hi

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Candidate:
    source: str
    id: str
    kind: str                           # photo | video
    title: str = ""
    description: str = ""
    thumb_url: str | None = None        # ~300-800 px preview for filters and the contact sheet
    full_url: str | None = None         # what fetch downloads
    page_url: str | None = None         # human landing page (goes in the credits)
    width: int | None = None
    height: int | None = None
    duration: float | None = None       # seconds, videos only
    license: str = "unknown"            # one of LICENSE_CLASSES
    license_name: str = ""              # human form: "CC BY-SA 4.0", "Public domain", "Pexels License"
    license_url: str | None = None
    license_flags: list[str] = field(default_factory=list)   # nc, nd, fair-use, verify
    author: str = ""
    credit: str = ""                    # ready-to-print attribution line
    date: str | None = None
    query: str = ""                     # the query that found it
    preview_url: str | None = None      # low-res video file for keyframes (videos)
    preview_frames: list[str] = field(default_factory=list)  # source-provided still frames (videos)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.id}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Candidate":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


# ---------------------------------------------------------------- licences

_CC_VERSION = re.compile(r"(\d\.\d)")


def classify_license(name: str | None = None, url: str | None = None) -> tuple[str, str, list[str]]:
    """Normalise a licence string and/or URL → (class, human name, flags).

    Flags: "nc" (non-commercial), "nd" (no derivatives), "fair-use", "verify" (a rights statement
    rather than a licence, e.g. LOC "No known restrictions"; fine to use, but credit and check).
    """
    raw = " ".join(x for x in (name, url) if x)
    s = raw.lower().replace("_", "-").strip()
    flags: list[str] = []
    if not s:
        return "unknown", "Unknown", flags
    ver = _CC_VERSION.search(s)
    v = f" {ver.group(1)}" if ver else ""
    if "fair use" in s or "fair-use" in s or "standard youtube" in s:
        return "other", "Copyrighted (fair-use risk)", ["fair-use"]

    nc = bool(re.search(r"(?<![a-z])(by-nc|nc-sa|nc-nd|-nc\b|noncommercial|non-commercial)", s))
    nd = bool(re.search(r"(by-nd|nc-nd|-nd\b|noderiv|no-deriv|no derivative)", s))
    if nc:
        flags.append("nc")
    if nd:
        flags.append("nd")

    if re.search(r"(publicdomain/zero|\bcc0\b|cc-zero|cc zero)", s):
        return "cc0", "CC0" + (v if v else " 1.0"), flags
    if re.search(r"(publicdomain/mark|public[ -]domain|\bpdm\b|^pd\b|\bpd-|no known (copyright )?restrictions"
                 r"|no known copyright|unrestricted|copyright[- ]?free|\bpd$)", s):
        if "no known" in s or "unrestricted" in s:
            flags.append("verify")
        return "public-domain", "Public domain", flags
    if re.search(r"(^|[^a-z])(cc[- ]?)?by[- ]sa\b|licenses/by-sa|attribution[- ]sharealike|share ?alike", s):
        return ("other" if nc or nd else "cc-by-sa"), _cc_name("BY-SA", nc, nd, v), flags
    if re.search(r"(^|[^a-z])cc[- ]?by\b|^by\b|licenses/by(-nc|-nd|-nc-nd|-nc-sa)?/|^by-n|\battribution\b", s):
        return ("other" if nc or nd else "cc-by"), _cc_name("BY", nc, nd, v), flags
    if nc or nd:
        return "other", raw.strip(), flags
    if "pexels" in s:
        return "other", "Pexels License", flags
    if "pixabay" in s:
        return "other", "Pixabay Content License", flags
    if "gfdl" in s or "gnu free documentation" in s:
        return "other", "GFDL", flags
    return "unknown", raw.strip()[:80] or "Unknown", flags


def _cc_name(base: str, nc: bool, nd: bool, v: str) -> str:
    parts = ["BY"] + (["NC"] if nc else []) + (["SA"] if "SA" in base and not nd else []) + (["ND"] if nd else [])
    return "CC " + "-".join(parts) + v


def license_allowed(c: Candidate, allow_nc: bool = False, allow_unknown: bool = True,
                    allow_fair_use: bool = False) -> bool:
    """Monetised-channel policy: no NC/ND by default; fair-use only when opted in."""
    if not allow_nc and ({"nc", "nd"} & set(c.license_flags)):
        return False
    if "fair-use" in c.license_flags and not allow_fair_use:
        return False
    if c.license == "unknown" and not allow_unknown:
        return False
    return True


def make_credit(c: Candidate) -> str:
    who = c.author or SOURCE_LABELS.get(c.source, c.source)
    lic = c.license_name or c.license
    via = SOURCE_LABELS.get(c.source, c.source)
    return f"{who} / {via}, {lic}" if who != via else f"{via}, {lic}"


# ---------------------------------------------------------------- text helpers

_TAG = re.compile(r"<[^>]+>")


def strip_html(s: Any) -> str:
    if s is None:
        return ""
    if isinstance(s, list):
        s = " ".join(str(x) for x in s)
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", str(s)))).strip()


def first_year(s: Any) -> int | None:
    m = re.search(r"\b(1[5-9]\d\d|20\d\d)\b", strip_html(s))
    return int(m.group(1)) if m else None


_STOP = {"the", "a", "an", "of", "and", "in", "on", "at", "for", "to", "with", "by", "from", "his", "her",
         "their", "its", "is", "photo", "image", "picture", "file", "jpg", "jpeg", "png", "video", "film", "s"}


def tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", strip_html(s).lower()) if t not in _STOP and len(t) > 1}


def text_match(req: Request, c: Candidate) -> float:
    """Cheap lexical relevance 0..1: how much of the subject (esp. names) the metadata mentions."""
    hay = tokens(" ".join([c.title, c.description]))
    subj = tokens(req.subject.split(",")[0])            # "Roberto Goizueta, Coca-Cola CEO" → name part
    rest = tokens(req.subject) | tokens(req.must_show)
    if not hay or not (subj or rest):
        return 0.0
    s1 = len(subj & hay) / len(subj) if subj else 0.0
    s2 = len(rest & hay) / len(rest) if rest else 0.0
    return round(0.7 * s1 + 0.3 * s2, 3)
