"""Guards against the model mis-summing a listing's costs.

Both cases below came from real Facebook posts: one where the components add up
to ~2586 zł and the model returned 586, and one where it returned no price at
all. Neither listing writes a total, so the earlier "compare against the stated
total" check could not see them.
"""
import score_offers as so


KAZIMIERZOWSKA = (
    "Cena 2000 zł odstępne. Czynsz 493 zł (obecnie). "
    "Prąd ok. 78 zł (obecnie). Gaz 15 zł (obecnie)."
)


def test_largest_amount_ignores_small_utility_figures():
    assert so.largest_amount_in_text(KAZIMIERZOWSKA) == 2000


def test_largest_amount_ignores_deposits_and_sale_prices():
    assert so.largest_amount_in_text("Cena 1 050 000 zł, czynsz 900 zł") == 900


def test_largest_amount_handles_spaced_thousands():
    assert so.largest_amount_in_text("Czynsz najmu: 3 200 zł / miesiąc") == 3200


def test_no_amounts_means_nothing_to_check():
    assert so.largest_amount_in_text("Zapraszam do kontaktu") is None


def test_stated_total_is_recognised():
    assert 1590 in so.totals_in_text("Cena: 1200 zł + 390 zł opłaty = 1590 zł")


def test_a_total_below_the_rent_is_impossible():
    """586 < 2000: the model dropped the rent itself."""
    assert 586 < 0.95 * so.largest_amount_in_text(KAZIMIERZOWSKA)


def test_a_correct_sum_passes():
    assert not 2586 < 0.95 * so.largest_amount_in_text(KAZIMIERZOWSKA)
