"""Parse checked Basic-card proposals from an Insights Hub."""
import re
import uuid
import re
from pathlib import Path

CARD = re.compile(r"### Card: (?P<title>.+?)\n(?P<body>.*?)(?=\n### Card:|\Z)", re.S)

def parse(path: Path, default_deck: str = "") -> list[dict]:
    text = path.read_text(encoding="utf-8")
    cards = []
    for match in CARD.finditer(text):
        body = match.group("body")
        if "- [x] Approve" not in body:
            continue
        deck = re.search(r"- Deck: `(.+?)`", body)
        text = re.search(r"\*\*Text\*\*\s*\n+(.+?)(?=\n\*\*Extra\*\*)", body, re.S)
        extra = re.search(r"\*\*Extra\*\*\s*\n+(.+?)(?=\n(?:### |<!--|\Z))", body, re.S)
        if not (text and extra) or "{{c" not in text.group(1):
            continue
        ident = re.search(r"gn2o_card_id:\s*([\w-]+)", body)
        value = {"id": ident.group(1) if ident else str(uuid.uuid4()), "title": match.group("title").strip(), "deck": deck.group(1) if deck else default_deck, "text": text.group(1).strip(), "extra": extra.group(1).strip()}
        if value["deck"] and re.search(r"\{\{c\d+::.+?\}\}", value["text"]):
            cards.append(value)
    return cards

def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", value)).strip().lower()
