"""Safe updates for GN2O-managed sections inside Obsidian hub pages."""
from pathlib import Path

BEGIN = "<!-- GN2O:BEGIN {name} -->"
END = "<!-- GN2O:END {name} -->"

def update_section(text: str, name: str, content: str) -> str:
    begin, end = BEGIN.format(name=name), END.format(name=name)
    block = f"{begin}\n{content.rstrip()}\n{end}"
    if begin in text and end in text:
        before, rest = text.split(begin, 1)
        _, after = rest.split(end, 1)
        return before + block + after
    return text.rstrip() + "\n\n" + block + "\n"

def update_hub(path: Path, sections: dict[str, str], header: str = "") -> None:
    text = path.read_text(encoding="utf-8") if path.exists() else header
    for name, content in sections.items():
        text = update_section(text, name, content)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
