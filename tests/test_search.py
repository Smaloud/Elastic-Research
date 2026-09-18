import uuid

from app.services.search import rrf_scores


def test_rrf_rewards_items_present_in_both_lists() -> None:
    first, second, third = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    scores = rrf_scores([[first, second], [third, first]])
    assert scores[first] > scores[second]
    assert scores[first] > scores[third]
