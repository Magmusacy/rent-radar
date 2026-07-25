"""The alert rule and the Telegram wording — the two things a user actually sees."""
import bot
import refresh


def offer(**kw):
    base = {"total_price": "1900", "street": "al. Pokoju, Kraków", "district": "Czyżyny",
            "seller": "prywatny", "area_m2": "25", "url": "https://example.com/x"}
    base.update(kw)
    return base


# ---------------------------------------------------------------- is_hit

def test_cheap_and_close_in_minutes_is_a_hit():
    assert refresh.is_hit(offer(commute_min="20"))


def test_cheap_and_close_in_km_is_a_hit():
    """The server has no Google key, so it judges by straight-line distance."""
    assert refresh.is_hit(offer(distance_km="2.4"))


def test_too_expensive_is_never_a_hit():
    assert not refresh.is_hit(offer(total_price="4000", commute_min="10"))


def test_too_far_is_not_a_hit():
    assert not refresh.is_hit(offer(distance_km="12"))


def test_minutes_win_when_both_are_present():
    """A short straight line across the Vistula can still be a long ride."""
    assert not refresh.is_hit(offer(commute_min="55", distance_km="1.0"))


def test_missing_distance_is_not_a_hit():
    assert not refresh.is_hit(offer())


def test_unparseable_price_is_not_a_hit():
    assert not refresh.is_hit(offer(total_price="", commute_min="10"))


# ---------------------------------------------------------------- messages

def test_scan_message_lists_hits_with_links():
    result = refresh.ScanResult(added=34, total=211, hits=[offer(distance_km="2.4")])
    text = bot.scan_message(result, "16:00")
    assert "34 nowych ogłoszeń" in text
    assert "2.4 km" in text
    assert 'href="https://example.com/x"' in text
    assert "👤 właściciel" in text


def test_scan_message_without_hits_stays_short():
    text = bot.scan_message(refresh.ScanResult(added=12, total=200), "12:00")
    assert "Nic pod Twój profil" in text
    assert "1." not in text


def test_scan_message_reports_a_failed_sweep():
    text = bot.scan_message(refresh.ScanResult(error="TimeoutError: olx"), "13:00")
    assert "nie doszła do skutku" in text
    assert "TimeoutError" in text


def test_offer_line_escapes_html_in_addresses():
    text = bot.offer_line(1, offer(district="<script>alert(1)</script>"))
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


def test_reach_prefers_minutes_over_km():
    assert bot.reach({"commute_min": "17", "distance_km": "2.0"}) == "17 min MPK"
    assert bot.reach({"distance_km": "2.0"}) == "2.0 km"
    assert bot.reach({}) == "? km"
