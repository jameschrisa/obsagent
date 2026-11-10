import re
from html.parser import HTMLParser
from pathlib import Path

from obsagent_lib import guests

GUIDE = Path(__file__).resolve().parent.parent / "docs" / "guest-guide.html"


class _Fill(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.stack = []
        self.found = set()

    def handle_starttag(self, tag, attrs):
        fill = tag == "mark" or ("class", "fill") in attrs
        self.stack.append((tag, fill))
        if fill:
            self.depth += 1

    def handle_endtag(self, tag):
        while self.stack:
            t, fill = self.stack.pop()
            if fill:
                self.depth -= 1
            if t == tag:
                break

    def handle_data(self, data):
        if self.depth:
            self.found.update(m.group(0) for m in re.finditer(r"\[[A-Z][^\]]*\]", data))


def test_guide_placeholders_match_code():
    p = _Fill()
    p.feed(GUIDE.read_text())
    assert p.found == set(guests.PLACEHOLDERS)


def test_full_plan_leaves_no_placeholders():
    plan = {"title": "Big Show", "show_time": "Sat 7 Nov, 10am PT", "tech_check": "Fri 6 Nov, 4pm PT",
            "host_contact": "text 555-0100", "youtube": {"watch_url": "https://youtube.com/watch?v=abc"}}
    html_out, remaining = guests.render_guide(GUIDE.read_text(), plan, "Ann", "https://vdo.ninja/?room=x")
    assert remaining == []
    assert not any(ph in html_out for ph in guests.PLACEHOLDERS)
