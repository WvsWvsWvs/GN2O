"""Read-only AnkiConnect client."""
import json
from collections import Counter
from urllib.request import Request, urlopen
from processors.bayesian import analyze

try:
    from config import ANKI_CONNECT_URL
except ImportError:
    ANKI_CONNECT_URL = "http://localhost:8765"

class AnkiConnectionError(RuntimeError):
    pass

def invoke(action: str, **params):
    body = json.dumps({"action": action, "version": 6, "params": params}).encode()
    req = Request(ANKI_CONNECT_URL, data=body, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=5) as response:
            result = json.loads(response.read().decode())
    except Exception as exc:
        raise AnkiConnectionError(f"Could not connect to AnkiConnect: {exc}") from exc
    if result.get("error"):
        raise AnkiConnectionError(str(result["error"]))
    return result.get("result")

def read_deck(deck_name: str) -> dict:
    ids = invoke("findNotes", query=f'deck:"{deck_name}"')
    notes = invoke("notesInfo", notes=ids) if ids else []
    card_ids = [cid for note in notes for cid in note.get("cards", [])]
    cards = invoke("cardsInfo", cards=card_ids) if card_ids else []
    reviews = invoke("getReviewsOfCards", cards=card_ids) if card_ids else {}
    return {"notes": notes, "cards": cards, "reviews": reviews}

def deck_names_and_ids() -> dict:
    return invoke("deckNamesAndIds")

def summarize(data: dict) -> dict:
    tags = Counter(tag for note in data["notes"] for tag in note.get("tags", []))
    note_tags = {cid: note.get("tags", []) for note in data["notes"] for cid in note.get("cards", [])}
    tag_reviews = {}
    for cid, history in data.get("reviews", {}).items() if isinstance(data.get("reviews"), dict) else []:
        for review in history:
            ease = review[3] if isinstance(review, list) and len(review) > 3 else 0
            for tag in note_tags.get(int(cid), []):
                success, failure = tag_reviews.setdefault(tag, [0, 0])
                tag_reviews[tag][0 if ease >= 3 else 1] += 1
    return {"notes": len(data["notes"]), "cards": len(data["cards"]), "tags": tags, "estimates": analyze({k: tuple(v) for k, v in tag_reviews.items()})}

def add_cloze_note(deck: str, text: str, extra: str, tags: list[str]) -> int:
    return invoke("addNote", note={"deckName": deck, "modelName": "Cloze", "fields": {"Text": text, "Extra": extra}, "tags": tags, "options": {"allowDuplicate": False}})
