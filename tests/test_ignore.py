"""Tests for .ignore system doc — global store-level ignore patterns."""

import pytest

from keep.ignore import (
    parse_ignore_patterns,
    merge_excludes,
    match_file_uri,
    match_ignore,
    uri_pattern_prefixes,
    _is_uri_pattern,
)


class TestParseIgnorePatterns:
    def test_empty_string(self):
        assert parse_ignore_patterns("") == []

    def test_comments_and_blanks(self):
        text = """
# A comment
*.pyc

# Another comment

__pycache__/*
"""
        result = parse_ignore_patterns(text)
        assert result == ["*.pyc", "__pycache__/*"]

    def test_whitespace_stripped(self):
        text = "  *.min.js  \n  dist/*  "
        result = parse_ignore_patterns(text)
        assert result == ["*.min.js", "dist/*"]

    def test_hash_in_middle_not_comment(self):
        # Only lines starting with # are comments
        text = "file#1.txt"
        result = parse_ignore_patterns(text)
        assert result == ["file#1.txt"]


class TestMergeExcludes:
    def test_global_only(self):
        assert merge_excludes(["*.pyc"], None) == ["*.pyc"]

    def test_local_only(self):
        assert merge_excludes([], ["*.log"]) == ["*.log"]

    def test_combined(self):
        result = merge_excludes(["*.pyc"], ["*.log"])
        assert result == ["*.pyc", "*.log"]

    def test_dedup(self):
        result = merge_excludes(["*.pyc", "*.log"], ["*.log", "*.tmp"])
        assert result == ["*.pyc", "*.log", "*.tmp"]

    def test_both_empty(self):
        assert merge_excludes([], None) == []

    def test_both_none(self):
        assert merge_excludes([], None) == []


class TestMatchFileUri:
    def test_basename_match(self):
        assert match_file_uri("file:///a/b/c.pyc", ["*.pyc"]) is True

    def test_path_match(self):
        assert match_file_uri("file:///a/b/dist/bundle.js", ["dist/*"]) is True

    def test_deep_path_match(self):
        assert match_file_uri("file:///a/b/__pycache__/foo.pyc", ["__pycache__/*"]) is True

    def test_no_match(self):
        assert match_file_uri("file:///a/b/src/main.py", ["*.pyc"]) is False

    def test_non_file_uri(self):
        assert match_file_uri("https://example.com/dist/bundle.js", ["dist/*"]) is False

    def test_empty_patterns(self):
        assert match_file_uri("file:///a/b/c.pyc", []) is False

    def test_exact_filename(self):
        assert match_file_uri("file:///a/b/package-lock.json", ["package-lock.json"]) is True

    def test_exact_filename_no_match(self):
        assert match_file_uri("file:///a/b/package.json", ["package-lock.json"]) is False

    def test_multiple_patterns(self):
        patterns = ["*.min.js", "*.map", "dist/*"]
        assert match_file_uri("file:///a/b/app.min.js", patterns) is True
        assert match_file_uri("file:///a/b/app.js.map", patterns) is True
        assert match_file_uri("file:///a/b/dist/index.js", patterns) is True
        assert match_file_uri("file:///a/b/src/index.js", patterns) is False


class TestIgnorePurge:
    """Integration test: put files, set .ignore, verify purge."""

    @pytest.fixture
    def kp(self, mock_providers, tmp_path):
        from keep.api import Keeper
        kp = Keeper(store_path=tmp_path)
        kp._get_embedding_provider()

        # Put some file:// items
        kp.put("build artifact", id="file:///project/dist/bundle.js")
        kp.put("source code", id="file:///project/src/main.py")
        kp.put("bytecode", id="file:///project/__pycache__/main.cpython-312.pyc")
        kp.put("lock file", id="file:///project/package-lock.json")
        kp.put("normal file", id="file:///project/README.md")

        return kp

    def test_purge_on_ignore_update(self, kp):
        # Verify all 5 items exist
        assert kp.exists("file:///project/dist/bundle.js")
        assert kp.exists("file:///project/src/main.py")
        assert kp.exists("file:///project/__pycache__/main.cpython-312.pyc")
        assert kp.exists("file:///project/package-lock.json")
        assert kp.exists("file:///project/README.md")

        # Set .ignore with patterns
        kp.put("dist/*\npackage-lock.json\n__pycache__/*\n*.pyc", id=".ignore")

        # Purged items should be gone
        assert not kp.exists("file:///project/dist/bundle.js")
        assert not kp.exists("file:///project/__pycache__/main.cpython-312.pyc")
        assert not kp.exists("file:///project/package-lock.json")

        # Non-matching items should remain
        assert kp.exists("file:///project/src/main.py")
        assert kp.exists("file:///project/README.md")

    def test_ignore_patterns_loaded(self, kp):
        kp.put("*.pyc\ndist/*", id=".ignore")
        patterns = kp._load_ignore_patterns()
        assert "*.pyc" in patterns
        assert "dist/*" in patterns

    def test_ignore_cache_invalidated(self, kp):
        kp.put("*.pyc", id=".ignore")
        p1 = kp._load_ignore_patterns()
        assert p1 == ["*.pyc"]

        kp.put("*.pyc\n*.map", id=".ignore")
        p2 = kp._load_ignore_patterns()
        assert "*.map" in p2

    def test_no_purge_for_non_file_uris(self, kp):
        kp.put("inline note about dist", id="dist-notes")
        kp.put("dist/*", id=".ignore")
        # Inline note should not be purged (not a file:// URI)
        assert kp.exists("dist-notes")

    def test_purge_git_uri_pattern(self, kp):
        """URI-scheme patterns like git://x-access-token/* should purge matching items."""
        kp.put("commit from token", id="git://x-access-token/repo#abc1234")
        kp.put("commit from token 2", id="git://x-access-token/other#def5678")
        kp.put("normal commit", id="git://myrepo#111aaaa")
        assert kp.exists("git://x-access-token/repo#abc1234")
        assert kp.exists("git://x-access-token/other#def5678")
        assert kp.exists("git://myrepo#111aaaa")

        kp.put("git://x-access-token/*", id=".ignore")

        # Token-based items should be purged
        assert not kp.exists("git://x-access-token/repo#abc1234")
        assert not kp.exists("git://x-access-token/other#def5678")
        # Normal git item should remain
        assert kp.exists("git://myrepo#111aaaa")

    def test_purge_mixed_uri_and_file_patterns(self, kp):
        """Mix of file-path and URI-scheme patterns should both work."""
        kp.put("token commit", id="git://x-access-token/repo#aaa")
        assert kp.exists("file:///project/dist/bundle.js")
        assert kp.exists("git://x-access-token/repo#aaa")

        kp.put("dist/*\ngit://x-access-token/*", id=".ignore")

        assert not kp.exists("file:///project/dist/bundle.js")
        assert not kp.exists("git://x-access-token/repo#aaa")
        # Non-matching items survive
        assert kp.exists("file:///project/src/main.py")


class TestMatchIgnore:
    """Tests for the unified match_ignore() function."""

    def test_uri_pattern_match(self):
        assert match_ignore("git://x-access-token/repo#abc", ["git://x-access-token/*"]) is True

    def test_uri_pattern_no_match(self):
        assert match_ignore("git://myrepo#abc", ["git://x-access-token/*"]) is False

    def test_file_path_pattern_still_works(self):
        assert match_ignore("file:///a/b/dist/bundle.js", ["dist/*"]) is True

    def test_file_path_pattern_no_match_on_non_file(self):
        assert match_ignore("git://repo#abc", ["dist/*"]) is False

    def test_mixed_patterns(self):
        pats = ["dist/*", "git://x-access-token/*"]
        assert match_ignore("file:///a/dist/x.js", pats) is True
        assert match_ignore("git://x-access-token/r#1", pats) is True
        assert match_ignore("file:///a/src/x.js", pats) is False
        assert match_ignore("git://normal/r#1", pats) is False

    def test_empty_patterns(self):
        assert match_ignore("git://anything", []) is False

    def test_https_uri_pattern(self):
        assert match_ignore("https://example.com/foo", ["https://example.com/*"]) is True
        assert match_ignore("https://other.com/foo", ["https://example.com/*"]) is False


class TestUriPatternPrefixes:
    def test_basic(self):
        assert uri_pattern_prefixes(["git://x-access-token/*"]) == ["git://x-access-token/"]

    def test_file_patterns_ignored(self):
        assert uri_pattern_prefixes(["*.pyc", "dist/*"]) == []

    def test_mixed(self):
        result = uri_pattern_prefixes(["*.pyc", "git://x/*", "https://example.com/*"])
        assert "git://x/" in result
        assert "https://example.com/" in result
        assert len(result) == 2

    def test_dedup(self):
        result = uri_pattern_prefixes(["git://x/*", "git://x/*"])
        assert result == ["git://x/"]


class TestIsUriPattern:
    def test_git_uri(self):
        assert _is_uri_pattern("git://x-access-token/*") is True

    def test_https_uri(self):
        assert _is_uri_pattern("https://example.com/*") is True

    def test_file_glob(self):
        assert _is_uri_pattern("*.pyc") is False
        assert _is_uri_pattern("dist/*") is False

    def test_no_scheme(self):
        assert _is_uri_pattern("://no-scheme") is False

    def test_glob_in_scheme(self):
        # Pathological: glob char in the scheme part → not a URI pattern
        assert _is_uri_pattern("g*t://foo/*") is False
