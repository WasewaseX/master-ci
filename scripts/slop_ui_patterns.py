"""UI-pattern constants: generic-AI design tells for frontend files.

The regexes translate the noqode 'design IA generique' signs and the
tenex '8 signs of AI slop' into deterministic source-level signals:
gradients, glass, eyebrows, fake stats, testimonials, placeholder
imagery, identical card grids, decorative numbering, em-dash piles.
"""

from __future__ import annotations

import re

UI_EXTS = {".css", ".scss", ".less", ".html", ".htm"}

UI_GRADIENT = re.compile(
    r"from-(?:purple|pink|blue|indigo|violet|fuchsia)-\d00\s+to-(?:pink|purple|rose|fuchsia|violet)-\d00"
    r"|linear-gradient\([^)]*(?:667eea|764ba2|a855f7|ec4899|8b5cf6)",
    re.IGNORECASE,
)
UI_GRADIENT_TEXT = re.compile(r"bg-clip-text|text-transparent\s+bg-gradient")
UI_MARKETING = re.compile(
    r"revolutioniz\w*|game-?chang\w*|next-gen(?:eration)?\b|seamless(?:ly)?\b|"
    r"cutting-edge|10,000\+|99\.9% uptime|happy users|trusted by thousands",
    re.IGNORECASE,
)
UI_PLACEHOLDER = re.compile(
    r"lorem ipsum|your (?:company|name|logo) here|yourusername|yourbrand|example\.com",
    re.IGNORECASE,
)
# tenex sign 5 / noqode sign 7: no real images. Placeholder-image services
# stand in for photos the author never chose.
UI_PLACEHOLDER_IMAGE = re.compile(
    r"(?:picsum\.photos|placehold\.co|via\.placeholder\.com|placekitten\.com|"
    r"pravatar\.cc|unsplash\.it|source\.unsplash\.com|placeimg\.com|"
    r"dummyimage\.com|fakeimg\.pl|plchldr\.co|place-hold\.it)",
    re.IGNORECASE,
)
# tenex sign 7 / noqode sign 3: identical cards. Six copies of the same
# class string mean one template multiplied, not six designed blocks.
UI_CARD_CLASS = re.compile(r"""class(?:Name)?\s*=\s*["']([^"']{30,})["']""")
# tenex sign 1: 'the em dash, everywhere' - the audited page carried
# thirty-one. A handful per file is typography; a pile of them is slop.
UI_EM_DASH_MIN = 8
UI_EYEBROW = re.compile(
    r"""(?:class(?:Name)?\s*=\s*["'{][^"'}]*\b(?:eyebrow|overline|kicker)\b"""
    r"""|\.(?:eyebrow|overline|kicker)\s*[{,])""",
    re.IGNORECASE,
)
UI_ITALIC_HEADLINE = re.compile(r"<h[1-3][^>]*>[^<]*<(?:em|i)\b", re.IGNORECASE)
UI_FAKE_STATS = re.compile(
    r"\b\d[\d,.]*\s*\+?\s*(?:happy\s+)?"
    r"(?:customers|users|teams|businesses|developers|companies|downloads|projects|integrations)\b"
    r"|\btrusted by\b[^.\n]{0,40}\d",
    re.IGNORECASE,
)
UI_GENERIC_TESTIMONIAL = re.compile(
    r"[A-Z][a-z]+,\s+(?:CEO|CTO|COO|CMO|CFO|founder|co-founder"
    r"|marketing director|head of [a-z]{3,25})\b"
)
UI_NOT_JUST = re.compile(r"\bnot (?:just|only)\b[^.!?\n]{1,40},\s*it'?s\b", re.IGNORECASE)
UI_TRIPLE_SLOGAN = re.compile(r"\b[A-Z][a-z]+\. [A-Z][a-z]+\. [A-Z][a-z]+\.")
UI_GLASS = re.compile(r"backdrop-filter:\s*blur|backdrop-blur\b")
UI_DECORATIVE_NUMBERS = re.compile(r"\b0[1-9]\b")
