"""Run: python3 -m unittest test_luma_digest.py"""
import importlib.util
import os
import unittest
from datetime import UTC, datetime

HERE = os.path.dirname(__file__)
spec = importlib.util.spec_from_file_location("luma", os.path.join(HERE, "luma-digest.py"))
luma = importlib.util.module_from_spec(spec)
spec.loader.exec_module(luma)

NOW = datetime(2026, 10, 18, 15, 0, tzinfo=UTC)


def vevent(start, summary, desc, location="https://luma.com/event/evt-x"):
    return f"BEGIN:VEVENT\nDTSTART:{start}\nSUMMARY:{summary}\nDESCRIPTION:{desc}\nLOCATION:{location}\nEND:VEVENT\n"


def feed(*events):
    return ("BEGIN:VCALENDAR\n" + "".join(events) + "END:VCALENDAR\n").encode()


class Parse(unittest.TestCase):
    def test_unfolds_split_multibyte_chars_and_unescapes(self):
        # Shaped like the live feed: a fold landing inside the 3-byte em dash.
        raw = (
            b"BEGIN:VEVENT\nDTSTART:20261019T160000Z\nSUMMARY:PyTorch\\, Enterprise\n"
            b"DESCRIPTION:Get up-to-date information at: https://luma.com/wjmtvwvw\\n\\nAgenda \xe2\x80\n"
            b" \x94 vLLM\nLOCATION:https://luma.com/event/evt-w\nEND:VEVENT\n"
        )
        [ev] = luma.parse(raw)
        self.assertEqual(ev["SUMMARY"], "PyTorch, Enterprise")
        self.assertEqual(
            ev["DESCRIPTION"],
            "Get up-to-date information at: https://luma.com/wjmtvwvw\n\nAgenda — vLLM",
        )


class Pick(unittest.TestCase):
    def test_keeps_next_seven_days_matching_keywords_sorted(self):
        events = luma.parse(feed(
            vevent("20261021T010000Z", "GPU night", "inference talks"),
            vevent("20261019T160000Z", "Founders breakfast", "for startups"),
            vevent("20261020T160000Z", "Yoga in the park", "stretching"),
            vevent("20261026T160000Z", "Kubernetes meetup", "next week, too late"),
            vevent("20261018T140000Z", "SRE brunch", "already started"),
        ))
        picked = luma.pick(events, NOW)
        self.assertEqual(
            [(ev["SUMMARY"], hits) for ev, hits in picked],
            [("Founders breakfast", ["founder", "startup"]), ("GPU night", ["gpu", "inference"])],
        )

    def test_matches_whole_words_only(self):
        events = luma.parse(feed(vevent("20261019T160000Z", "Presreading club", "Ascension")))
        self.assertEqual(luma.pick(events, NOW), [])


class Render(unittest.TestCase):
    def test_uses_event_link_even_when_location_is_an_address(self):
        events = luma.parse(feed(vevent(
            "20261021T003000Z", "SGLang Meetup",
            "Get up-to-date information at: https://luma.com/zbp6a1e1\\n\\nAbout",
            location="950 W Maude Ave\\, Sunnyvale",
        )))
        self.assertEqual(
            luma.render(luma.pick(events, NOW)),
            "Tue Oct 20 5:30PM  SGLang Meetup\nhttps://luma.com/zbp6a1e1\nmatched: sglang",
        )

    def test_says_so_when_nothing_matches(self):
        self.assertEqual(luma.render([]), "No matching Luma SF events this week.")


if __name__ == "__main__":
    unittest.main()
