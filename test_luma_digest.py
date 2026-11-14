"""Run: python3 -m unittest test_luma_digest.py"""
import importlib.util
import json
import os
import smtplib
import unittest
from datetime import UTC, datetime
from unittest import mock

HERE = os.path.dirname(__file__)
spec = importlib.util.spec_from_file_location("luma", os.path.join(HERE, "luma-digest.py"))
luma = importlib.util.module_from_spec(spec)
spec.loader.exec_module(luma)

NOW = datetime(2026, 10, 18, 15, 0, tzinfo=UTC)


def vevent(start, summary, desc, location="https://luma.com/event/evt-x", uid="evt-x"):
    return (
        f"BEGIN:VEVENT\nDTSTART:{start}\nUID:{uid}@events.lu.ma\nSUMMARY:{summary}\n"
        f"DESCRIPTION:{desc}\nLOCATION:{location}\nEND:VEVENT\n"
    )


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


class Fetch(unittest.TestCase):
    @mock.patch.object(luma, "download")
    def test_supplements_truncated_city_feed_through_next_week(self, download):
        city_feed = feed(vevent(
            "20261019T160000Z", "Founders breakfast", "for startups", uid="evt-first"
        ))
        calendar_feed = feed(vevent(
            "20261026T160000Z", "Durable AI Meetup", "for founders", uid="evt-next"
        ))
        pages = [
            {
                "entries": [{"event": {
                    "api_id": "evt-first", "calendar_api_id": "cal-first",
                    "start_at": "2026-10-19T16:00:00.000Z",
                }}],
                "has_more": True,
                "next_cursor": "cursor-1",
            },
            {
                "entries": [
                    {"event": {
                        "api_id": "evt-next", "calendar_api_id": "cal-next",
                        "start_at": "2026-10-26T16:00:00.000Z",
                    }},
                    {"event": {
                        "api_id": "evt-late", "calendar_api_id": "cal-late",
                        "start_at": "2026-11-02T16:00:00.000Z",
                    }},
                ],
                "has_more": True,
                "next_cursor": "cursor-2",
            },
        ]
        calls = []

        def respond(url, params):
            calls.append((url, params.copy()))
            if url == luma.FEED and params["entity"] == "discover":
                return city_feed
            if url == luma.DISCOVER:
                cursor = params.get("pagination_cursor")
                return json.dumps(pages[0 if cursor is None else 1]).encode()
            if url == luma.FEED and params == {"entity": "calendar", "id": "cal-next"}:
                return calendar_feed
            raise AssertionError(f"unexpected request: {url} {params}")

        download.side_effect = respond
        events = luma.fetch_events(NOW)
        this_week, next_week = luma.render(luma.pick(events, NOW), NOW).split("\n\n\n")

        self.assertEqual(len(events), 2)
        self.assertIn("Founders breakfast", this_week)
        self.assertIn("Durable AI Meetup", next_week)
        self.assertIn("matched: founder", next_week)
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[2][1]["pagination_cursor"], "cursor-1")
        self.assertEqual(calls[3], (luma.FEED, {"entity": "calendar", "id": "cal-next"}))


class Pick(unittest.TestCase):
    def test_keeps_next_two_weeks_matching_keywords_sorted(self):
        events = luma.parse(feed(
            vevent("20261021T010000Z", "GPU night", "inference talks"),
            vevent("20261019T160000Z", "Founders breakfast", "for startups"),
            vevent("20261020T160000Z", "Yoga in the park", "stretching"),
            vevent("20261026T160000Z", "Kubernetes meetup", "next week"),
            vevent("20261102T160000Z", "Grafana con", "three weeks out, too late"),
            vevent("20261018T140000Z", "SRE brunch", "already started"),
        ))
        picked = luma.pick(events, NOW)
        self.assertEqual(
            [(ev["SUMMARY"], hits) for ev, hits in picked],
            [
                ("Founders breakfast", ["founder", "startup"]),
                ("GPU night", ["gpu", "inference"]),
                ("Kubernetes meetup", ["kubernetes"]),
            ],
        )

    def test_matches_whole_words_only(self):
        events = luma.parse(feed(vevent("20261019T160000Z", "Presreading club", "Ascension")))
        self.assertEqual(luma.pick(events, NOW), [])


class Retry(unittest.TestCase):
    @mock.patch.object(luma.time, "sleep")
    def test_retries_transient_errors_with_backoff(self, sleep):
        operation = mock.Mock(side_effect=[TimeoutError("one"), OSError("two"), "ok"])
        self.assertEqual(luma.retry(operation), "ok")
        self.assertEqual(operation.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])

    @mock.patch.object(luma.time, "sleep")
    def test_stops_after_three_transient_errors(self, sleep):
        operation = mock.Mock(side_effect=TimeoutError("offline"))
        with self.assertRaises(TimeoutError):
            luma.retry(operation)
        self.assertEqual(operation.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @mock.patch.object(luma.time, "sleep")
    def test_does_not_retry_authentication_errors(self, sleep):
        operation = mock.Mock(
            side_effect=smtplib.SMTPAuthenticationError(535, b"bad credentials")
        )
        with self.assertRaises(smtplib.SMTPAuthenticationError):
            luma.retry(operation)
        operation.assert_called_once_with()
        sleep.assert_not_called()


class Render(unittest.TestCase):
    def test_uses_event_link_even_when_location_is_an_address(self):
        events = luma.parse(feed(vevent(
            "20261021T003000Z", "SGLang Meetup",
            "Get up-to-date information at: https://luma.com/zbp6a1e1\\n\\nAbout",
            location="950 W Maude Ave\\, Sunnyvale",
        )))
        self.assertEqual(
            luma.render(luma.pick(events, NOW), NOW),
            "== This week ==\n\nTue Oct 20 5:30PM  SGLang Meetup\nhttps://luma.com/zbp6a1e1\nmatched: sglang"
            "\n\n\n== Next week ==\n\nNo matching events.",
        )

    def test_labels_only_non_sf_locations(self):
        events = luma.parse(feed(
            vevent(
                "20261019T160000Z", "GPU Palo Alto",
                "Get up-to-date information at: https://luma.com/palo-alto\\n\\nGPU",
                location="NODE, 180 University Ave, Palo Alto, CA 94301, USA",
            ),
            vevent(
                "20261020T160000Z", "GPU SF",
                "Get up-to-date information at: https://luma.com/sf\\n\\nGPU",
                location="450 Powell St, San Francisco, CA 94108, USA",
            ),
            vevent(
                "20261021T160000Z", "GPU hidden",
                "Get up-to-date information at: https://luma.com/hidden\\n\\nGPU",
            ),
        ))
        body = luma.render(luma.pick(events, NOW), NOW)

        self.assertIn(
            "GPU Palo Alto\nhttps://luma.com/palo-alto\nlocation: Palo Alto, CA\nmatched: gpu",
            body,
        )
        self.assertIn("GPU SF\nhttps://luma.com/sf\nmatched: gpu", body)
        self.assertIn("GPU hidden\nhttps://luma.com/hidden\nmatched: gpu", body)
        self.assertEqual(body.count("location:"), 1)

    def test_splits_at_seven_days(self):
        events = luma.parse(feed(
            vevent("20261025T145900Z", "LLM night", "last minute of week one"),
            vevent("20261025T150000Z", "SRE night", "first minute of week two"),
        ))
        this_week, next_week = luma.render(luma.pick(events, NOW), NOW).split("\n\n\n")
        self.assertIn("LLM night", this_week)
        self.assertIn("SRE night", next_week)

    def test_renders_all_day_events_without_a_time(self):
        events = luma.parse(feed(
            "BEGIN:VEVENT\n"
            "DTSTART;VALUE=DATE:20261019\n"
            "DTEND;VALUE=DATE:20261020\n"
            "SUMMARY:GPU exhibition\n"
            "DESCRIPTION:https://luma.com/event/gpu-exhibition\n"
            "LOCATION:https://luma.com/event/gpu-exhibition\n"
            "END:VEVENT\n"
        ))
        self.assertIn(
            "Mon Oct 19 all day  GPU exhibition",
            luma.render(luma.pick(events, NOW), NOW),
        )


if __name__ == "__main__":
    unittest.main()
