import tempfile
import unittest
from datetime import date
from pathlib import Path

from letterboxd_streaming import analyze, letterboxd, report
from letterboxd_streaming.cache import Cache
from letterboxd_streaming.cli import check_film
from letterboxd_streaming.config import Prefs, canonical_provider
from letterboxd_streaming.http import HTTPError
from letterboxd_streaming.models import Film, Status
from letterboxd_streaming.tmdb import TMDB, pick_match

FIX = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 13)


def prov(name, prio=1):
    return {"provider_name": name, "display_priority": prio}


def details(providers=None, release=None, status="Released", release_date=""):
    return {
        "status": status,
        "release_date": release_date,
        "release_dates": {"results": release or []},
        "watch/providers": {"results": {"US": providers} if providers is not None else {}},
    }


def us(*dates):
    return [{"iso_3166_1": "US", "release_dates": [{"release_date": f"{d}T00:00:00.000Z", "type": t} for d, t in dates]}]


class LetterboxdParsing(unittest.TestCase):
    def test_old_markup(self):
        films = letterboxd.parse_watchlist_page((FIX / "watchlist_old.html").read_text())
        self.assertEqual([(f.slug, f.title) for f in films],
                         [("dune-part-two", "Dune: Part Two"), ("amelie", "Amélie")])

    def test_new_markup_has_year(self):
        films = letterboxd.parse_watchlist_page((FIX / "watchlist_new.html").read_text())
        self.assertEqual([(f.slug, f.title, f.year) for f in films],
                         [("the-odyssey-2026", "The Odyssey", 2026), ("dune-part-two", "Dune: Part Two", 2024)])

    def test_pagination_stops_on_empty_page(self):
        pages = {
            "https://letterboxd.com/me/watchlist/": (FIX / "watchlist_old.html").read_text(),
            "https://letterboxd.com/me/watchlist/page/2/": (FIX / "watchlist_new.html").read_text(),
            "https://letterboxd.com/me/watchlist/page/3/": "<html>no films</html>",
        }
        films = letterboxd.fetch_watchlist("me", fetch=lambda url: pages[url])
        self.assertEqual([f.slug for f in films], ["dune-part-two", "amelie", "the-odyssey-2026"])

    def test_blocked_scrape_points_to_csv(self):
        def fetch(url):
            raise HTTPError(403, url)
        with self.assertRaises(SystemExit) as cm:
            letterboxd.fetch_watchlist("me", fetch=fetch)
        self.assertIn("--csv", str(cm.exception))

    def test_tmdb_id_from_film_page(self):
        self.assertEqual(letterboxd.parse_tmdb_link((FIX / "film_page.html").read_text()), (693134, "movie"))
        self.assertEqual(letterboxd.parse_tmdb_link('<a href="https://www.themoviedb.org/tv/1399/">'), (1399, "tv"))

    def test_csv_export(self):
        films = letterboxd.read_watchlist_csv(str(FIX / "watchlist.csv"))
        self.assertEqual([(f.title, f.year) for f in films], [
            ("Dune: Part Two", 2024), ("Crouching Tiger, Hidden Dragon", 2000), ("Untitled Project", None)])


class ProviderNames(unittest.TestCase):
    def test_variants_collapse(self):
        for name in ["Max", "HBO Max", "Max Amazon Channel", "HBO Max Amazon Channel"]:
            self.assertEqual(canonical_provider(name), "hbo max", name)
        self.assertEqual(canonical_provider("Netflix Standard with Ads"), "netflix")
        self.assertEqual(canonical_provider("Paramount+ Amazon Channel"), "paramount plus")
        self.assertEqual(canonical_provider("Paramount Plus Premium"), "paramount plus")
        self.assertEqual(canonical_provider("The Roku Channel"), "the roku channel")
        self.assertEqual(canonical_provider("The Criterion Channel"), "criterion channel")
        self.assertEqual(canonical_provider("Apple TV+"), "apple tv plus")
        self.assertEqual(canonical_provider("Criterion Collection"), "criterion channel")
        self.assertEqual(canonical_provider("HBOMax"), "hbo max")
        self.assertEqual(canonical_provider("Pluto"), "pluto tv")


class Ranking(unittest.TestCase):
    def setUp(self):
        self.prefs = Prefs(my_services={"hbo max"})

    def rank(self, providers, prefs=None):
        return [(o.provider, o.kind) for o in analyze.build_offers(providers, prefs or self.prefs)]

    def test_default_free_services(self):
        prefs = Prefs()
        for name in ["Tubi TV", "Pluto TV", "Plex", "The Roku Channel"]:
            offers = analyze.build_offers({"ads": [prov(name)]}, prefs)
            self.assertEqual(offers[0].tier, analyze.FREE, name)

    def test_tiers_free_then_subscription_then_rent_then_buy(self):
        got = self.rank({"buy": [prov("Apple TV")], "rent": [prov("Google Play Movies")],
                         "flatrate": [prov("MUBI")], "ads": [prov("Tubi TV", 20)]})
        self.assertEqual(got, [("Tubi TV", "ads"), ("MUBI", "flatrate"), ("Google Play Movies", "rent"), ("Apple TV", "buy")])

    def test_subscription_beats_rental_even_when_not_subscribed(self):
        got = self.rank({"flatrate": [prov("MUBI")], "rent": [prov("Amazon Video")]})
        self.assertEqual(got[0], ("MUBI", "flatrate"))

    def test_owned_subscription_beats_unowned_and_cheaper(self):
        got = self.rank({"flatrate": [prov("Paramount Plus", 1), prov("Max Amazon Channel", 5), prov("Max", 9)]})
        self.assertEqual([p for p, _ in got], ["Max Amazon Channel", "Paramount Plus"])  # Max variants deduped

    def test_unowned_subscriptions_cheapest_first(self):
        got = self.rank({"flatrate": [prov("MUBI", 1), prov("Netflix", 2)]})
        self.assertEqual([p for p, _ in got], ["Netflix", "MUBI"])

    def test_zero_display_priority_is_respected(self):
        got = self.rank({"flatrate": [prov("Max Amazon Channel", 3), prov("Max", 0)]})
        self.assertEqual(got, [("Max", "flatrate")])

    def test_other_free_services_ignored_by_default(self):
        providers = {"free": [prov("Kanopy"), prov("YouTube Free")], "flatrate": [prov("Netflix")]}
        self.assertEqual(self.rank(providers), [("Netflix", "flatrate")])
        self.assertEqual(self.rank(providers, Prefs(count_other_free=True))[0][1], "free")

    def test_ad_tier_of_paid_service_is_a_subscription(self):
        offers = analyze.build_offers({"ads": [prov("Amazon Prime Video with Ads")]}, Prefs())
        self.assertEqual((offers[0].tier, offers[0].key), (analyze.SUBSCRIPTION, "amazon prime video"))

    def test_free_service_listed_as_flatrate_still_free(self):
        offers = analyze.build_offers({"flatrate": [prov("Netflix"), prov("Pluto TV")]}, Prefs())
        self.assertEqual((offers[0].provider, offers[0].tier), ("Pluto TV", analyze.FREE))

    def test_apple_tv_flatrate_matches_apple_tv_plus_subscription(self):
        prefs = Prefs(my_services={canonical_provider("Apple TV+")})
        got = analyze.build_offers({"flatrate": [prov("Apple TV")], "rent": [prov("Apple TV")]}, prefs)
        self.assertTrue(got[0].subscribed)
        self.assertEqual(got[1].kind, "rent")


class ReleaseStatus(unittest.TestCase):
    def classify(self, d, film=None):
        return analyze.classify(film or Film("X", 2026, tmdb_id=1), d, Prefs(), today=TODAY)

    def test_available(self):
        r = self.classify(details({"flatrate": [prov("Netflix")]}))
        self.assertEqual(r.status, Status.AVAILABLE)

    def test_pvod_while_in_theaters_is_available(self):
        r = self.classify(details({"rent": [prov("Apple TV")]}, us(("2026-09-20", 3), ("2026-10-07", 4))))
        self.assertEqual(r.status, Status.AVAILABLE)
        self.assertIn("Rent/buy only", r.note)

    def test_future_theatrical(self):
        r = self.classify(details({}, us(("2026-12-18", 3)), status="Post Production"))
        self.assertEqual((r.status, r.expected_date), (Status.UNRELEASED, "2026-12-18"))

    def test_no_date_announced(self):
        r = self.classify(details(None, [], status="In Production"))
        self.assertEqual(r.status, Status.UNRELEASED)
        self.assertIn("In Production", r.note)

    def test_in_theaters_with_known_digital_date(self):
        r = self.classify(details({}, us(("2026-09-01", 3), ("2026-11-04", 4))))
        self.assertEqual((r.status, r.expected_date), (Status.IN_THEATERS, "2026-11-04"))

    def test_in_theaters_unknown_digital(self):
        r = self.classify(details({}, us(("2026-09-01", 3))))
        self.assertEqual(r.status, Status.IN_THEATERS)

    def test_old_film_with_no_providers(self):
        r = self.classify(details({}, us(("1999-05-01", 3))))
        self.assertEqual(r.status, Status.NOT_AVAILABLE)

    def test_released_abroad_only(self):
        fr = [{"iso_3166_1": "FR", "release_dates": [{"release_date": "2026-12-02T00:00:00.000Z", "type": 3}]}]
        r = self.classify(details({}, fr))
        self.assertEqual(r.status, Status.UNRELEASED)
        self.assertIn("worldwide", r.note)

    def test_festival_only(self):
        r = self.classify(details({}, us(("2026-05-20", 1))))
        self.assertEqual(r.status, Status.UNRELEASED)
        self.assertIn("Festival", r.note)


class History(unittest.TestCase):
    def test_service_switch_detected(self):
        prev = {"status": "available", "streaming": {"netflix": "Netflix"}}
        cur = {"status": "available", "streaming": {"hbo max": "HBO Max"}, "best": "HBO Max"}
        self.assertEqual(analyze.diff_snapshots(prev, cur), ["Now streaming on HBO Max", "Left Netflix"])

    def test_variant_rename_is_not_a_move(self):
        prev = {"status": "available", "streaming": {"hbo max": "Max Amazon Channel"}}
        cur = {"status": "available", "streaming": {"hbo max": "Max"}, "best": "Max"}
        self.assertEqual(analyze.diff_snapshots(prev, cur), [])

    def test_release_detected(self):
        changes = analyze.diff_snapshots({"status": "in_theaters", "streaming": {}},
                                         {"status": "available", "streaming": {"peacock": "Peacock"}, "best": "Peacock"})
        self.assertEqual(changes, ["Status changed: in theaters -> available", "Now streaming on Peacock"])


class TitleMatching(unittest.TestCase):
    def test_prefers_title_and_year(self):
        results = [
            {"id": 1, "title": "Dune", "release_date": "1984-12-14"},
            {"id": 2, "title": "Dune", "release_date": "2021-09-15"},
        ]
        self.assertEqual(pick_match(Film("Dune", 2021), results)["id"], 2)

    def test_rejects_wrong_film(self):
        self.assertIsNone(pick_match(Film("Heat", 1995), [{"id": 9, "title": "Hot", "release_date": "2013-01-01"}]))


class EndToEnd(unittest.TestCase):
    def test_check_film_records_history_and_reports_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Cache(Path(tmp) / "c.sqlite3")
            prefs = Prefs(my_services={"netflix"})
            payload = {"d": details({"flatrate": [prov("Netflix")]}, us(("2024-03-01", 3)))}
            tmdb = TMDB(api_key="k", cache=cache, fetch_json=lambda url, headers: payload["d"])
            film = Film("Dune: Part Two", 2024, tmdb_id=693134)

            first = check_film(film, tmdb, cache, prefs, refresh=True, use_letterboxd_pages=False)
            self.assertEqual(first.best.provider, "Netflix")
            self.assertEqual(first.changes, [])

            payload["d"] = details({"flatrate": [prov("Max")], "rent": [prov("Apple TV")]}, us(("2024-03-01", 3)))
            second = check_film(film, tmdb, cache, prefs, refresh=True, use_letterboxd_pages=False)
            self.assertEqual(second.best.provider, "Max")  # Netflix gone; any subscription beats renting
            self.assertEqual(second.changes, ["Now streaming on Max", "Left Netflix"])

            text = report.render_text([second], prefs)
            self.assertIn("CHANGES SINCE LAST RUN", text)
            html = report.render_html([second], prefs)
            self.assertIn("Left Netflix", html)
            cache.close()


if __name__ == "__main__":
    unittest.main()
