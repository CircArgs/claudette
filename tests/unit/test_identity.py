"""Tests for identity signature parsing."""

from claudette.core.identity import Author, parse_author, stamp_manager, stamp_worker


class TestParseAuthor:
    def test_human_comment(self):
        author, issue = parse_author("Just a normal comment from a person.")
        assert author == Author.HUMAN
        assert issue is None

    def test_manager_comment(self):
        body = "I've dispatched a worker for this.\n\n<!--agent:manager-->"
        author, issue = parse_author(body)
        assert author == Author.MANAGER
        assert issue is None

    def test_worker_comment(self):
        body = "PR opened.\n\n<!--agent:worker:42-->"
        author, issue = parse_author(body)
        assert author == Author.WORKER
        assert issue == 42

    def test_empty_comment(self):
        author, issue = parse_author("")
        assert author == Author.HUMAN
        assert issue is None


class TestStamp:
    def test_stamp_manager(self):
        result = stamp_manager("Hello")
        assert "<!--agent:manager-->" in result

    def test_stamp_worker(self):
        result = stamp_worker("Done", 42)
        assert "<!--agent:worker:42-->" in result
