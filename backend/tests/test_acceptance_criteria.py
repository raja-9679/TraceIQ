"""Acceptance-criteria parser, ADF flattening and ambiguity detection.

    ./run-tests.sh tests/test_acceptance_criteria.py -q
"""
import pytest

from app.services.acceptance_criteria import (
    adf_to_text, analyse, detect_ambiguity, parse_acceptance_criteria,
)


def texts(desc):
    return [c["text"] for c in parse_acceptance_criteria(desc)]


def kinds(desc):
    return [c["kind"] for c in parse_acceptance_criteria(desc)]


# --- Gherkin ---------------------------------------------------------------

def test_gherkin_scenarios_are_one_criterion_each():
    desc = """
As a shopper I want to pay with UPI.

Scenario: successful payment
  Given I have an item in my cart
  When I pay with a valid UPI id
  Then I see the order confirmation page
  And I receive a confirmation email

Scenario: invalid UPI id
  Given I have an item in my cart
  When I pay with "bad@upi"
  Then I see the error "Invalid UPI ID"
"""
    out = parse_acceptance_criteria(desc)
    assert [c["kind"] for c in out] == ["gherkin", "gherkin"]
    assert out[0]["text"].splitlines() == [
        "Scenario: successful payment",
        "Given I have an item in my cart",
        "When I pay with a valid UPI id",
        "Then I see the order confirmation page",
        "And I receive a confirmation email",
    ]
    assert out[1]["text"].startswith("Scenario: invalid UPI id")


def test_gherkin_without_scenario_lines_splits_on_given_after_then():
    desc = ("Given a logged-in user\nWhen they open settings\nThen the profile tab is shown\n"
            "Given a guest\nWhen they open settings\nThen they are redirected to login")
    out = texts(desc)
    assert len(out) == 2
    assert out[1].startswith("Given a guest")


def test_bulleted_and_bold_gherkin_keywords():
    desc = ("Acceptance Criteria\n- **Given** I am on the login page\n"
            "- **When** I submit wrong credentials\n- **Then** an error message is displayed")
    out = parse_acceptance_criteria(desc)
    assert len(out) == 1 and out[0]["kind"] == "gherkin"
    assert "Then an error message is displayed" in out[0]["text"]


def test_lowercase_keywords_and_example_tables_stay_with_scenario():
    desc = ("Scenario Outline: login\n given user <name>\n when they log in\n then they see <page>\n"
            "| name | page |\n| admin | Dashboard |\nSome closing prose.")
    out = parse_acceptance_criteria(desc)
    assert len(out) == 1
    assert out[0]["text"].endswith("| admin | Dashboard |")
    assert "Given user <name>" in out[0]["text"]


def test_stray_and_is_prose_not_gherkin():
    assert texts("And another thing about the design.") == []


# --- AC headings -------------------------------------------------------------

@pytest.mark.parametrize("heading", [
    "## Acceptance Criteria",
    "h3. Acceptance Criteria",
    "**Acceptance criteria:**",
    "Acceptance Criteria:",
    "*Acceptance Criteria*",
    "### AC",
    "Definition of Done",
    "ACCEPTANCE CRITERIA",
])
def test_heading_variants_open_the_section(heading):
    desc = f"Intro paragraph that is not a criterion.\n\n{heading}\n- User can reset password via email link\n- Reset link expires after 24 hours\n"
    assert texts(desc) == ["User can reset password via email link", "Reset link expires after 24 hours"]


def test_other_heading_closes_the_section():
    desc = ("## Acceptance Criteria\n- The export button downloads a CSV\n\n"
            "## Notes\n- remember to ping design\n")
    assert texts(desc) == ["The export button downloads a CSV"]


@pytest.mark.parametrize("marker", ["-", "*", "+", "1.", "2)", "(3)", "a.", "•"])
def test_list_markers(marker):
    desc = f"Acceptance Criteria:\n{marker} Search results are sorted by price\n"
    assert texts(desc) == ["Search results are sorted by price"]


def test_plain_lines_in_section_are_criteria():
    desc = "Acceptance criteria:\nThe cart shows the item count.\nThe total includes tax.\n"
    out = parse_acceptance_criteria(desc)
    assert [c["kind"] for c in out] == ["text", "text"]


def test_inline_criterion_after_colon():
    assert texts("Acceptance criteria: the user sees a welcome banner") == [
        "the user sees a welcome banner"]


def test_sub_bullets_fold_into_parent():
    desc = ("## Acceptance Criteria\n- Password rules are enforced\n"
            "  - minimum 12 characters\n  - at least one digit\n- Login is locked after 5 attempts\n")
    assert texts(desc) == [
        "Password rules are enforced; minimum 12 characters; at least one digit",
        "Login is locked after 5 attempts",
    ]


def test_wrapped_continuation_line_joins_item():
    desc = "Acceptance Criteria\n- The invoice PDF is emailed to\n  the billing contact\n"
    assert texts(desc) == ["The invoice PDF is emailed to the billing contact"]


# --- checklists / AC prefixes ----------------------------------------------

def test_checklists_anywhere():
    desc = "Some story text.\n- [ ] Order history lists the last 10 orders\n- [x] Each order links to its invoice\n[ ] Bare checkbox item works too\n"
    assert kinds(desc) == ["checklist", "checklist", "checklist"]
    assert texts(desc)[1] == "Each order links to its invoice"


def test_ac_prefixed_lines_anywhere():
    desc = "Story.\nAC1: The banner is dismissible\nAC-2 - Dismissal persists across reloads\nAC 3) Banner shows the promo code"
    assert texts(desc) == ["The banner is dismissible", "Dismissal persists across reloads",
                           "Banner shows the promo code"]


def test_jira_wiki_icons_and_monospace_are_stripped():
    desc = "h2. Acceptance Criteria\n* (/) The {{Save}} button is disabled until the form is valid\n"
    assert texts(desc) == ["The Save button is disabled until the form is valid"]


# --- edge cases -----------------------------------------------------------

def test_nothing_recognisable_returns_empty():
    assert parse_acceptance_criteria("Just a story with no criteria at all.") == []
    assert parse_acceptance_criteria("") == []
    assert parse_acceptance_criteria(None) == []


def test_duplicates_are_removed_and_crlf_handled():
    desc = "Acceptance Criteria\r\n- Shows the total\r\n- shows the total.\r\n"
    assert texts(desc) == ["Shows the total"]


def test_html_breaks():
    assert texts("Acceptance Criteria:<br>- The footer shows the version") == [
        "The footer shows the version"]


# --- ADF ----------------------------------------------------------------

def _p(text):
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def test_adf_headings_lists_and_tasks_round_trip_through_parser():
    doc = {"type": "doc", "version": 1, "content": [
        _p("As a user I want to export."),
        {"type": "heading", "attrs": {"level": 3}, "content": [{"type": "text", "text": "Acceptance Criteria"}]},
        {"type": "bulletList", "content": [
            {"type": "listItem", "content": [_p("Export downloads a CSV file"),
                                             {"type": "bulletList", "content": [
                                                 {"type": "listItem", "content": [_p("named after the report")]}]}]},
            {"type": "listItem", "content": [_p("Empty reports show a message")]},
        ]},
        {"type": "taskList", "content": [
            {"type": "taskItem", "attrs": {"state": "DONE"}, "content": [{"type": "text", "text": "Admins see an audit entry"}]},
        ]},
    ]}
    text = adf_to_text(doc)
    assert "### Acceptance Criteria" in text
    assert "- [x] Admins see an audit entry" in text
    assert texts(text) == [
        "Export downloads a CSV file; named after the report",
        "Empty reports show a message",
        "Admins see an audit entry",
    ]


def test_adf_ordered_list_hardbreak_and_mentions():
    doc = {"type": "doc", "content": [
        {"type": "paragraph", "content": [
            {"type": "text", "text": "Given a cart"}, {"type": "hardBreak"},
            {"type": "text", "text": "When "}, {"type": "mention", "attrs": {"text": "@raja"}},
            {"type": "text", "text": " checks out"}, {"type": "hardBreak"},
            {"type": "text", "text": "Then the order is created"}]},
        {"type": "orderedList", "content": [{"type": "listItem", "content": [_p("one")]}]},
    ]}
    text = adf_to_text(doc)
    assert "When @raja checks out" in text
    assert "1. one" in text
    assert kinds(text) == ["gherkin"]


def test_adf_passthrough_and_garbage():
    assert adf_to_text("plain v2 text") == "plain v2 text"
    assert adf_to_text(None) == ""
    assert adf_to_text(42) == ""
    assert adf_to_text({"type": "doc", "content": [{"type": "mystery", "content": [{"type": "text", "text": "x"}]}]}) == "x"


# --- ambiguity ------------------------------------------------------------

@pytest.mark.parametrize("text,word", [
    ("The page loads fast", "fast"),
    ("The checkout flow is user-friendly", "user-friendly"),
    ("Search should work", "should work"),
    ("The dashboard is intuitive for new admins", "intuitive"),
    ("Error handling works as expected on the form", "works as expected"),
    ("The UI looks good on mobile", "looks good"),
])
def test_vague_terms_are_flagged(text, word):
    res = detect_ambiguity(text)
    assert res["ambiguous"]
    assert any(word in r for r in res["reasons"]), res


def test_longest_vague_phrase_wins():
    reasons = detect_ambiguity("Login works as expected on the page")["reasons"]
    assert reasons == ['vague term "works as expected"']


def test_measurable_bound_forgives_performance_words():
    assert not detect_ambiguity("Search results are returned fast, within 2 seconds")["ambiguous"]
    assert not detect_ambiguity("The page loads in under 300 ms")["ambiguous"]
    # ...but not other vague words
    assert detect_ambiguity("The page is intuitive and loads in 2 seconds")["ambiguous"]


@pytest.mark.parametrize("text", [
    "Users can log in with SSO",
    "An error message is displayed for an expired card",
    'The header reads "Welcome back"',
    "The API returns 404 for an unknown order",
    "Given a cart\nWhen I check out\nThen the order is created",
])
def test_concrete_criteria_are_not_ambiguous(text):
    assert detect_ambiguity(text) == {"ambiguous": False, "reasons": []}


def test_no_observable_outcome():
    res = detect_ambiguity("Refactor the payment module internals for maintainability")
    assert res["ambiguous"] and "no observable outcome" in res["reasons"]


def test_gherkin_without_then():
    res = detect_ambiguity("Given a logged in user\nWhen they open the menu")
    assert res["ambiguous"]
    assert any("no Then step" in r for r in res["reasons"])


def test_too_short():
    assert detect_ambiguity("Works")["reasons"] == ["too short to test"]


def test_word_boundaries_avoid_false_positives():
    # "fastest"/"breakfast"/"easyjet" are not the vague words.
    assert not detect_ambiguity("The breakfast menu page shows prices")["ambiguous"]


def test_analyse_shapes_rows():
    rows = analyse("Acceptance Criteria\n- The page is fast\n- The total is displayed\n")
    assert [(r["index"], r["ambiguous"]) for r in rows] == [(0, True), (1, False)]
    assert set(rows[0]) == {"index", "text", "kind", "ambiguous", "reasons"}
