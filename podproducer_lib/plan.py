"""Plan yaml helpers: the `producer:` brief block."""
from pathlib import Path

import yaml

SKELETON = """
# Producer brief for `podproducer run`. Fill this in before going live.
producer:
  topics:
    - "Main topic of the show"
  goals:
    - "What a listener should walk away with"
  tone: "playful, warm, curious"
  segments:
    - {name: Cold open, minutes: 3}
    - {name: Main conversation, minutes: 30}
    - {name: Rapid fire, minutes: 7}
    - {name: Wrap up, minutes: 5}
  must_ask:
    - "The one question you cannot leave without asking"
  avoid:
    - "Topics a guest asked not to discuss"
  question_bank:
    - "What is the worst advice you ever followed, and why did it almost work?"
    - "What is the smallest hill you will happily die on?"
    - "If your job had a theme song, what would it be?"
    - "What did you believe two years ago that now makes you laugh?"
    - "Quick game: explain your work to a five year old in one sentence."
    - "What is the most unexpected thing that turned out to matter?"
  guests:
    - {name: "Guest name", bio: "One or two lines of background"}
"""

KEYS = ["topics", "goals", "tone", "segments", "must_ask", "avoid", "question_bank", "guests"]


def load_plan(path: Path) -> dict:
    return yaml.safe_load(Path(path).read_text()) or {}


def load_brief(path: Path) -> dict:
    b = load_plan(path).get("producer")
    return b if isinstance(b, dict) else {}


def prep(path: Path):
    """Add a producer skeleton to the plan, or fill missing keys. Never overwrites existing values.
    Returns (action, missing_keys)."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"no plan at {path}")
    text = path.read_text()
    plan = yaml.safe_load(text) or {}
    if "producer" not in plan:
        path.write_text(text.rstrip("\n") + "\n" + SKELETON)
        return "added", list(KEYS)
    block = plan["producer"]
    if not isinstance(block, dict):
        raise ValueError("producer: exists but is not a mapping; fix it by hand")
    skel = yaml.safe_load(SKELETON)["producer"]
    missing = [k for k in KEYS if k not in block]
    if not missing:
        return "unchanged", []
    for k in missing:
        block[k] = skel[k]
    path.with_suffix(".yaml.bak").write_text(text)
    path.write_text(yaml.safe_dump(plan, sort_keys=False, allow_unicode=True))
    return "updated", missing


def roster(show: str) -> list:
    """Guests from the obsagent roster (read only). Empty on any problem."""
    try:
        from obsagent_lib import guests
        st = guests.load_state(show)
        return [g for g in (st or {}).get("guests", []) if isinstance(g, dict)]
    except Exception:
        return []


def merged_guests(brief: dict, show: str) -> list:
    out = {g.get("name"): dict(g) for g in roster(show) if g.get("name")}
    for g in brief.get("guests") or []:
        if isinstance(g, dict) and g.get("name") and not str(g["name"]).startswith("Guest name"):
            out.setdefault(g["name"], {}).update(g)
    return list(out.values())
