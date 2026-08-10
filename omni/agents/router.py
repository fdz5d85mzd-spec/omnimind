"""Routes an incoming prompt to the skill it needs, so the orchestrator can
assign it to a real specialist from the seeded fleet (see fleet_seed.py)
instead of every request landing on an interchangeable generic worker.
"""

from __future__ import annotations

import re

# skill tag -> patterns matched against the lowercased prompt. Skill tags
# are the ones actually carried by fleet_seed.SEED_ROSTER; \b word
# boundaries keep short keywords (e.g. "sql") from matching mid-word.
_SKILL_PATTERNS: dict[str, tuple[str, ...]] = {
    "coding": (r"\bcode\b", r"\bfunction\b", r"\bdebug", r"\bpython\b", r"\bjavascript\b", r"\btypescript\b",
               r"\bsql\b", r"\bapi\b", r"\bregex\b", r"\bstack trace\b", r"\bexception\b", r"\brefactor",
               r"\bcompile", r"\bunit test", r"\bwrite a script\b"),
    "code-review": (r"\bcode review\b", r"\breview (this|my) code\b", r"\bpull request\b"),
    "research": (r"\bresearch\b", r"\blook up\b", r"\bfind out\b", r"\bsources?\b", r"\bcitations?\b",
                 r"\bwho (is|was)\b", r"\bwhat (is|are|was)\b", r"\bhistory of\b", r"\bfacts? about\b"),
    "writing": (r"\bwrite\b", r"\bdraft\b", r"\bessay\b", r"\bblog post\b", r"\barticle\b", r"\bstory\b",
                r"\bpoem\b", r"\bcopy for\b", r"\btagline\b", r"\bslogan\b"),
    "translation": (r"\btranslate\b", r"\bin (spanish|french|greek|german|chinese|japanese|italian)\b",
                     r"\binto english\b"),
    "planning": (r"\bplan\b", r"\broadmap\b", r"\bschedule\b", r"\bitinerary\b", r"\btimeline\b",
                 r"\bsteps to\b", r"\bhow do i start\b"),
    "data-analysis": (r"\banalyze (this|the) data\b", r"\bdataset\b", r"\bcsv\b", r"\bspreadsheet\b",
                       r"\bcorrelation\b", r"\btrend\b", r"\baverage of\b"),
    "summarization": (r"\bsummarize\b", r"\bsummary of\b", r"\btl;?dr\b", r"\bshorten this\b"),
}

_COMPILED: list[tuple[str, re.Pattern[str]]] = [
    (skill, re.compile("|".join(patterns), re.IGNORECASE)) for skill, patterns in _SKILL_PATTERNS.items()
]


def classify_skills(prompt: str) -> list[str]:
    """Return every skill tag this prompt plausibly needs, most-specific
    signal first (code-review before the broader coding). Empty when
    nothing matches -- callers fall back to unskilled (any-agent) routing.
    Real specialists in the fleet each carry one primary skill, so callers
    should route on a single tag (e.g. the first) rather than requiring
    every match at once."""
    text = prompt.lower()
    return [skill for skill, pattern in _COMPILED if pattern.search(text)]
