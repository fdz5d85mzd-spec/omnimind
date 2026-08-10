"""classify_skills(): keyword routing from a prompt to the fleet skill tags
it plausibly needs (see fleet_seed.SEED_ROSTER for the real vocabulary)."""

from omni.agents.router import classify_skills


def test_coding_prompt_matches_coding_skill():
    assert "coding" in classify_skills("write a python function to reverse a string")


def test_research_prompt_matches_research_skill():
    assert "research" in classify_skills("research the history of the Suez Canal")


def test_writing_prompt_matches_writing_skill():
    assert "writing" in classify_skills("write a short blog post about coffee")


def test_translation_prompt_matches_translation_skill():
    assert "translation" in classify_skills("translate this sentence into greek")


def test_planning_prompt_matches_planning_skill():
    assert "planning" in classify_skills("make a roadmap for launching my app")


def test_summarization_prompt_matches_summarization_skill():
    assert "summarization" in classify_skills("summarize this article for me")


def test_code_review_is_more_specific_than_plain_coding():
    skills = classify_skills("please do a code review of this pull request")
    assert "code-review" in skills


def test_unrelated_prompt_matches_nothing():
    assert classify_skills("hi there, how are you today?") == []


def test_short_keyword_does_not_match_inside_another_word():
    # "sql" must not fire on unrelated words that merely contain the letters
    assert "coding" not in classify_skills("the results were sequel to the announcement")
