"""Episode teasers: the short description Nick posts with each episode on RSS.com (and so Spotify,
Apple and the rest), plus a suggested episode title.

Cutzo doesn't write them itself (free tools only, and a small model on Nick's PC writes flat text).
Claude writes them through the connector: `brief()` hands Claude the style, Nick's own past
descriptions and the edited transcript (cut words left out), in parts so a long episode never
overflows one answer; `Cutzo.set_teaser` stores the result in project.json and writes
`<title> - Teaser.txt` beside the exports.
"""
from __future__ import annotations

import math
import re
from typing import Any, Iterable

from .captions import FILLERS
from .timecode import fmt_time
from .transcript import Range, paragraphs

PART_WORDS = 6000  # about 9,000 tokens of transcript per answer
MAX_TEASER_CHARS = 4000  # RSS.com allows more; Nick's run 200-800

# Nick's own descriptions from the RSS.com feed (Oct 2025 to Jul 2026), newest first, as plain text.
DIG_EXAMPLES: list[dict[str, str]] = [
    {"title": "The Good, The Bad, Spencer Jones' Jumper",
     "text": "In the first episode of our fifth season(!), The Dig regulars discuss the Nuggets off-season moves so "
             "far, whether or not they even want Peyton Watson retained, and LeBron's new nickname courtesy of "
             "Philadelphese."},
    {"title": "2026-26 Denver Nuggets Season Debrief, Part 2: Impossible Trades | ft. Miroslav Ćuk and Jeremy Poley",
     "text": "Part 2 of The Dig's Season Debrief starts with a recap of the NBA Finals and what we learned about "
             "championship roster construction, if anything. Then Miroslav, Jeremy, and Nick run through the "
             "Nuggets' options and some (im)possible trades to keep hope alive"},
    {"title": "2025-26 Denver Nuggets Season Debrief, Part 1 | ft. Miroslav Ćuk and Jeremy Poley",
     "text": "In Part 1 of The Dig's long-awaited reaction to the Nuggets' 2025-26 season, Miroslav, Jeremy, and Nick "
             "get honest, raw, and naked(?) about where another disappointing outcome leaves a team that now seems to "
             "be well outside championship contention. Topics include the stages of grief, David Adelman's "
             "disappointing performance, the case for trading Jokić, PWat's priorities, and fascism.\n\nEnjoy!"},
    {"title": "The Year of the Horse, Part 2: Playoff Preview | ft. Zach Mikash",
     "text": "The third and fourth hour of The Dig's two-part marathon features Denver Stiffs' own Zach Mikash who "
             "joins Nick Hertzog for all things playoffs, Nuggets vs. Timberwolves, and even a Kenyon Martin "
             "reference from the deep catalog.\n\nSit back, relax, and prepare to distract yourself with something "
             "else along the way because it's a long one."},
    {"title": "The Year of the Horse, Part 1 | ft. Miroslav Ćuk and Jeremy Poley",
     "text": "In the first part of The Dig's ridiculously long season recap and playoff preview, Miroslav, Jeremy, and "
             "Nick spend most of a season-awards-(and-Razzies)-themed segment ranting about the narratives driving "
             "this year's MVP debate. Quality content also includes Germany's airport crisis, how some in the "
             "Nuggets' own media are getting MVP objectively wrong, and Miro's subjective dislike of all things "
             "Turkish basketball.\n\nPart 2 will drop on Friday, 4/17/26 for those of you sad enough to need another "
             "two hours of The Dig."},
    {"title": "The Thin Gold Line | ft. Miroslav Ćuk and Jeremy Poley",
     "text": "Nick got his Twitter account un-hacked, which means he no longer had an excuse to delay a new episode of "
             "The Dig.\n\nComing off of the Nuggets' hardest stretch of the season, Miroslav Ćuk, Jeremy Poley, and "
             "Nick Hertzog have thoughts about German and Serbian linguistics, microphone brands, and the "
             "pronunciation of Jokic's name, as well as probably wrong takes about the defense, the rotation, and how "
             "to strategize Peyton Watson.\n\nIf you enjoy the show, please leave a comment, like, and subscribe. If "
             "you don't, you're probably an OKC fan."},
    {"title": "Break Stuff | ft. Miroslav Ćuk and Jeremy Poley",
     "text": "The Dig is back with a recap of the first half of the season including chatter around the NBA All-Star "
             "game, the current zeitgeist, and first-half awards. Nick, Miro, and Jeremy also make some second-half "
             "predictions and try to keep Miroslav off of FBI blacklists."},
    {"title": "Jokic Shmokic | ft. Miroslav Ćuk and Jeremy Poley",
     "text": "The Nuggets bench has turned the doldrums of pre-All Star break games into one of the most fun runs in "
             "Nuggets history. In this episode, Miro, Jeremy, and Nick discuss the Peyton Watson revelation and what "
             "it means for the rotation, as well as Serbian sayings, the anatomy of knees, and some deep Dig lore."},
    {"title": "The Fellowship of More Rings | ft. Nick's College Buddies",
     "text": "With the theatrical re-release of Fellowship of the Ring just a few weeks away, Nick is joined by his "
             "nerdy college friends to analyze the Nuggets LOTR-style.\n\nTopics include Rudy Gobert's problem, which "
             "Nugget's injury hurts the most, and an underappreciated transcendence in Jokic's game."},
    {"title": "Clash of the Titans | ft. Miroslav Ćuk and Jeremy the Pure",
     "text": "Episode 4 finds Miroslav, Jeremy, and Nick in a bit of a mid-December funk, like the Nuggets' defense. "
             "But with Jokmas on the horizon, the trio is able to find some laughs as they discuss Greek mythology, "
             "1950s betting strategies, and Spencer Jones' aesthetics.\n\nCome for the OKC slander, stay for AI's "
             "scathing review of our hard work.\n\nEnjoy, and go Nuggets!"},
    {"title": "All Roads Lead to Swatson | ft. Miroslav Ćuk and Rayvone Hackshaw III",
     "text": "Rayvone Hackshaw III of Four Corners Media joins co-hosts Nick and Miroslav to debate the NBA schedule, "
             "whether European basketball is superior in every way to the NBA, and if Peyton Watson is likely to be "
             "traded this season or if Miro's mind has also lost a step.\n\nAlso included: another haiku, the "
             "Serbian National Anthem, and some mild doomerism."},
    {"title": "The Offense Strikes Back | ft. Miroslav Ćuk and Will Jones",
     "text": "The Nuggets aren't the only team that's starting to cook. Host Nick Hertzog and reluctant co-host "
             "Miroslav Ćuk welcome Will Jones to the podcast to debate whether offense or defense is more important "
             "in NBA basketball, how Cam Johnson is hitting our feels, and other uncontroversial topics like communism "
             "and atheism.\n\nCheck out @Will_d_jones and his co-host @erinburr27 on the What's In a Game? podcast "
             "and YouTube channel for much more intelligent Nuggets and NBA content.\n\nAlso, like and subscribe to "
             "The Dig if you haven't already. You can find us wherever your attention is sold."},
    {"title": "2025-26 Nuggets Season Preview Spectaculor | ft. Miroslav Ćuk, Jeremy Poley, and Math",
     "text": "The Dig is back! In the first episode since The Bubble, Miroslav Ćuk of DNVR fame joins OG hosts Nick "
             "and Jeremy to meander through the hottest topics going into the Nuggets 2025-6 NBA season.\n\nTopics "
             "include but are not limited to the promise of Cam Johnson, Jamal Murray's chances at becoming an "
             "All-star (finally), and the heated hazelnut wars in Europe, as well as another unnecessary Nuggets "
             "haiku.\n\nWe're glad to be back, and hope you enjoy hanging out for however long you can take it.\n\n"
             "Go Nuggets!"},
]

DIG_STYLE = {
    "where": "RSS.com episode description (it goes on to Spotify, Apple Podcasts and the rest)",
    "notes": [
        "Write as Nick, the host: casual, dry, self-deprecating; the show is a bit of a mess and knows it.",
        "One to three short paragraphs, about 40 to 110 words. Plain text: no emojis, hashtags, bullet points or "
        "timestamps.",
        "Open with a hook: where the Nuggets are right now, why the episode exists or is late, or which part of a "
        "series this is (\"In Part 1 of ...\", \"The Dig is back ...\").",
        "Say who's on. The regulars are Miroslav (Ćuk, \"Miro\"), Jeremy (Poley) and Nick (Hertzog): first names "
        "for them (\"Miroslav, Jeremy, and Nick\"); a guest gets their full name and where they're from "
        "(\"Rayvone Hackshaw III of Four Corners Media\").",
        "List three to five topics in one running sentence (\"Topics include ...\" or \"discuss ..., as well as "
        "...\"), real basketball first and the strangest tangent last, as the punchline.",
        "Small asides in brackets are part of the voice: \"(!)\", \"(im)possible\", \"naked(?)\", \"(finally)\".",
        "Optional last line: a wry sign-off or call to subscribe (\"Enjoy, and go Nuggets!\", \"If you don't, "
        "you're probably an OKC fan.\"). Don't use the same one every time.",
        "Only mention topics that are really in the transcript; get names right (Jokić, Peyton Watson, ...). If "
        "who's speaking isn't clear, ask Nick rather than guess.",
    ],
    "title_format": "<Short punny title> | ft. <guests>, e.g. \"Jokic Shmokic | ft. Miroslav Ćuk and Jeremy Poley\". "
                    "Nick is the host, so he isn't in the ft. list. Series episodes lead with the series: "
                    "\"<Series>, Part 2: <Subtitle> | ft. ...\".",
    "examples": DIG_EXAMPLES,
}

STYLES: dict[str, dict[str, Any]] = {"the-dig": DIG_STYLE}

GENERIC_STYLE = {
    "where": "the podcast feed's episode description",
    "notes": [
        "Write as Nick, the host: casual and a little wry. One to three short paragraphs, about 40 to 110 words, "
        "plain text without emojis or hashtags.",
        "Say who's on and list three to five topics that are really in the transcript, the oddest one last.",
    ],
    "title_format": "<Short title> | ft. <guests>",
    "examples": [],
}


def style_for(show_id: str) -> dict[str, Any]:
    return STYLES.get(show_id, GENERIC_STYLE)


def lines(transcript: dict[str, Any], cuts: Iterable[Range]) -> list[tuple[str, int]]:
    """The edited transcript as readable lines ("[12:34] Jeremy: ..."), cut words and fillers left out,
    with each line's word count."""
    out: list[tuple[str, int]] = []
    for para in paragraphs(transcript, cuts):
        words = [w["w"] for w in para["words"]
                 if not w["cut"] and re.sub(r"[^\w']", "", w["w"].lower()) not in FILLERS]
        if not words:
            continue
        who = f"{para['speaker']}: " if para.get("speaker_id") else ""
        out.append((f"[{fmt_time(para['start'])}] {who}{' '.join(words)}", len(words)))
    return out


def split(rows: list[tuple[str, int]], size: int = PART_WORDS) -> list[list[str]]:
    """Parts of about `size` words each, never splitting a line; every part is as long as the others."""
    total = sum(n for _, n in rows)
    count = max(1, math.ceil(total / size))
    target = total / count
    parts: list[list[str]] = [[]]
    done = 0
    for text, n in rows:
        if parts[-1] and done + n / 2 > target * len(parts) and len(parts) < count:
            parts.append([])
        parts[-1].append(text)
        done += n
    return parts


def clean(text: Any) -> str:
    """Tidy a teaser: normal line ends, no trailing spaces, at most one blank line between paragraphs."""
    t = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    t = "\n".join(line.rstrip() for line in t.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", t).strip()
