from app.services.analytics import best_slots

def test_best_slots_are_ranked_by_observed_activity():
    counts = [[0 for _ in range(24)] for _ in range(7)]
    counts[4][17] = 10
    counts[5][12] = 5
    matrix = {"counts": counts, "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]}
    slots = best_slots(matrix, 2)
    assert slots[0]["day"] == "Friday"
    assert slots[0]["hour"] == 17
    assert slots[0]["score"] == 100
    assert slots[1]["score"] == 50
