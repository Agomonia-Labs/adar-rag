import json

from docintel_mcp.tools import _practice_quiz_json


def test_practice_quiz_generation_assigns_missing_question_and_option_ids():
    generated = json.dumps({
        "schema_version": 1,
        "instructions": "Select every correct answer.",
        "questions": [{
            "question": "Which retrieval method uses semantic similarity?",
            "options": [
                {"text": "Keyword search", "correct": False},
                {"text": "Vector search", "correct": True},
                {"text": "Exact filtering", "correct": False},
                {"text": "Sorting", "correct": False},
            ],
            "explanation": "Vector search compares embedding similarity.",
        }],
    })

    quiz = json.loads(_practice_quiz_json(f"```json\n{generated}\n```"))

    assert quiz["questions"][0]["id"] == "q1"
    assert [option["id"] for option in quiz["questions"][0]["options"]] == ["A", "B", "C", "D"]
    assert quiz["questions"][0]["options"][1]["correct"] is True


def test_practice_quiz_generation_normalizes_unstable_option_ids():
    generated = json.dumps({
        "questions": [{
            "id": "retrieval-question",
            "question": "Choose the supported statement.",
            "options": [
                {"id": "1", "text": "One", "correct": False},
                {"id": "2", "text": "Two", "correct": True},
                {"id": "3", "text": "Three", "correct": False},
                {"id": "4", "text": "Four", "correct": False},
            ],
            "explanation": "The evidence supports option two.",
        }],
    })

    quiz = json.loads(_practice_quiz_json(generated))

    assert quiz["questions"][0]["id"] == "retrieval-question"
    assert [option["id"] for option in quiz["questions"][0]["options"]] == ["A", "B", "C", "D"]
