"""Tests for git changelog ingest."""

import os
import subprocess
import pytest
from pathlib import Path

from keep.git_ingest import (
    is_git_repo,
    get_repo_root,
    get_commits_since,
    get_tags,
    ingest_git_history,
    _repo_name,
)


@pytest.fixture
def git_repo(tmp_path):
    """Create a minimal git repo with some commits."""
    repo = tmp_path / "myproject"
    repo.mkdir()

    def _run(*args):
        subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            check=True,
            env={**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t.com",
                 "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@t.com"},
        )

    _run("init", "-b", "main")
    _run("config", "user.name", "Test")
    _run("config", "user.email", "t@t.com")

    # Commit 1: initial
    (repo / "README.md").write_text("# My Project\n")
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("print('hello')\n")
    _run("add", ".")
    _run("commit", "-m", "Initial commit\n\nSets up project structure")

    # Commit 2: add auth
    (repo / "src" / "auth.py").write_text("def login(): pass\n")
    _run("add", ".")
    _run("commit", "-m", "Add auth module")

    # Commit 3: update readme
    (repo / "README.md").write_text("# My Project\n\nWith auth!\n")
    _run("add", ".")
    _run("commit", "-m", "Update readme with auth docs")

    # Tag
    _run("tag", "-a", "v1.0", "-m", "First release")

    return repo


class TestGitDetection:
    """Tests for git repository detection."""
    def test_is_git_repo(self, git_repo):
        assert is_git_repo(git_repo) is True

    def test_not_git_repo(self, tmp_path):
        assert is_git_repo(tmp_path) is False

    def test_get_repo_root(self, git_repo):
        root = get_repo_root(git_repo / "src")
        assert root == git_repo

    def test_repo_name_no_remote(self, git_repo):
        # No remote → falls back to absolute path
        name = _repo_name(git_repo)
        assert name == str(git_repo.resolve())

    def test_repo_name_with_remote(self, git_repo):
        # Add a remote and verify URL extraction
        subprocess.run(
            ["git", "remote", "add", "origin", "https://github.com/acme/myproject.git"],
            cwd=str(git_repo), check=True, capture_output=True,
        )
        assert _repo_name(git_repo) == "github.com/acme/myproject"


class TestGetCommits:
    """Tests for commit retrieval."""
    def test_all_commits(self, git_repo):
        commits = get_commits_since(git_repo)
        assert len(commits) == 3
        # Newest first
        assert "readme" in commits[0]["subject"].lower()
        assert "auth" in commits[1]["subject"].lower()
        assert "initial" in commits[2]["subject"].lower()

    def test_commit_structure(self, git_repo):
        commits = get_commits_since(git_repo)
        c = commits[0]
        assert c["sha"]  # full SHA
        assert c["sha_short"]  # short SHA
        assert c["author_name"] == "Test"
        assert c["author_email"] == "t@t.com"
        assert c["date"]  # ISO date
        assert c["message"]
        assert c["id"].startswith("git://")
        assert f"#{c['sha_short']}" in c["id"]
        assert isinstance(c["files"], list)

    def test_commit_has_files(self, git_repo):
        commits = get_commits_since(git_repo)
        # "Update readme" commit should touch README.md
        readme_commit = commits[0]
        assert "README.md" in readme_commit["files"]

    def test_incremental(self, git_repo):
        all_commits = get_commits_since(git_repo)
        # Use second commit as watermark
        watermark = all_commits[1]["sha"]
        new_commits = get_commits_since(git_repo, watermark=watermark)
        assert len(new_commits) == 1
        assert "readme" in new_commits[0]["subject"].lower()

    def test_message_is_subject(self, git_repo):
        commits = get_commits_since(git_repo)
        initial = commits[-1]  # oldest
        # message = subject only (body excluded to avoid --name-only parse corruption)
        assert initial["message"] == "Initial commit"

    def test_multiline_body_not_in_files(self, git_repo):
        """Regression: multi-line commit bodies must not leak into file lists."""
        (git_repo / "src" / "main.py").write_text("print('updated')\n")
        subprocess.run(
            ["git", "add", "."], cwd=str(git_repo), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Refactor main\n\nShould not modify the host's config files.\nAlso fixes edge case."],
            cwd=str(git_repo), check=True, capture_output=True,
            env={**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t.com",
                 "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@t.com"},
        )
        commits = get_commits_since(git_repo)
        newest = commits[0]
        assert newest["subject"] == "Refactor main"
        # Body lines must NOT appear in the file list
        for f in newest["files"]:
            assert "config" not in f.lower(), f"Body text leaked into files: {f}"
        assert "src/main.py" in newest["files"]


class TestGetTags:
    """Tests for git tag retrieval."""
    def test_tags(self, git_repo):
        tags = get_tags(git_repo)
        assert len(tags) >= 1
        v1 = [t for t in tags if t["name"] == "v1.0"]
        assert len(v1) == 1
        assert v1[0]["id"].endswith("@v1.0")
        assert v1[0]["id"].startswith("git://")
        assert "release" in v1[0]["subject"].lower() or "first" in v1[0]["subject"].lower()


class TestIngest:
    """Tests for git ingest pipeline."""
    @pytest.fixture
    def kp(self, mock_providers, git_repo):
        from keep.api import Keeper
        kp = Keeper(store_path=git_repo.parent / ".keep")
        kp._get_embedding_provider()

        # Index the repo files first
        kp.put("# My Project\n\nWith auth!\n", id=f"file://{git_repo}/README.md")
        kp.put("def login(): pass\n", id=f"file://{git_repo}/src/auth.py")
        kp.put("print('hello')\n", id=f"file://{git_repo}/src/main.py")

        return kp

    def test_ingest_creates_commit_items(self, kp, git_repo):
        result = ingest_git_history(kp, git_repo)
        assert result["commits"] == 3
        assert result["tags"] >= 1

        # Commit items should exist
        commits = get_commits_since(git_repo)
        for c in commits:
            item = kp.get(c["id"])
            assert item is not None, f"Commit {c['id']} not found"
            assert c["subject"] in item.summary

    def test_ingest_tags_files(self, kp, git_repo):
        ingest_git_history(kp, git_repo)

        # Files should have git_commit tags
        readme = kp.get(f"file://{git_repo}/README.md")
        assert readme is not None
        assert "git_commit" in readme.tags

    def test_ingest_creates_tag_items(self, kp, git_repo):
        ingest_git_history(kp, git_repo)

        tags = get_tags(git_repo)
        v1 = [t for t in tags if t["name"] == "v1.0"][0]
        tag_item = kp.get(v1["id"])
        assert tag_item is not None

    def test_incremental_ingest(self, kp, git_repo):
        # First ingest
        result1 = ingest_git_history(kp, git_repo)
        assert result1["commits"] == 3

        # Second ingest (no new commits)
        result2 = ingest_git_history(kp, git_repo)
        assert result2["commits"] == 0

    def test_watermark_stored(self, kp, git_repo):
        ingest_git_history(kp, git_repo)

        # Ingest creates the directory item and sets git_watermark
        dir_item = kp.get(f"file://{git_repo}")
        assert dir_item is not None
        watermark = dir_item.tags.get("git_watermark")
        assert watermark is not None
        assert len(watermark) == 40  # full SHA

    def test_incremental_watermark_stays_scalar(self, kp, git_repo):
        """git_watermark is singular — re-ingest must replace, not append.

        Regression: pre-singular stores accumulated a list of SHAs,
        which broke the next ingest run because re.match got a list.
        """
        # Two ingest runs back-to-back produce at most one watermark value.
        ingest_git_history(kp, git_repo)
        ingest_git_history(kp, git_repo)

        dir_item = kp.get(f"file://{git_repo}")
        assert dir_item is not None
        watermark = dir_item.tags.get("git_watermark")
        assert isinstance(watermark, str), \
            f"git_watermark must be a scalar string, got {type(watermark).__name__}: {watermark!r}"
        assert len(watermark) == 40

    def test_ingest_tolerates_legacy_list_watermark(self, kp, git_repo):
        """Defensive: old stores may hold a list-valued git_watermark.

        Rather than blowing up with ``TypeError: expected string or
        bytes-like object, got 'list'`` when re.match runs on the list,
        the reader picks the last value and proceeds.
        """
        # First ingest establishes the watermark in singular form.
        ingest_git_history(kp, git_repo)
        dir_uri = f"file://{git_repo}"
        first_watermark = kp.get(dir_uri).tags.get("git_watermark")
        assert isinstance(first_watermark, str)

        # Simulate a corrupted store by rewriting the tag as a list
        # directly via the document store (bypassing the singular
        # enforcement in the tag path).
        doc_coll = kp._resolve_doc_collection()
        doc = kp._document_store.get(doc_coll, dir_uri)
        fake_list = ["0" * 40, first_watermark]  # last value is real
        updated_tags = dict(doc.tags)
        updated_tags["git_watermark"] = fake_list
        kp._document_store.update_tags(doc_coll, dir_uri, updated_tags)

        # Re-ingest: must not raise. Incremental: no new commits.
        result = ingest_git_history(kp, git_repo)
        assert result["commits"] == 0

    def test_not_git_repo(self, kp, tmp_path):
        result = ingest_git_history(kp, tmp_path)
        assert result["commits"] == 0

    def test_commit_references_files(self, kp, git_repo):
        ingest_git_history(kp, git_repo)

        # The auth commit should reference src/auth.py
        commits = get_commits_since(git_repo)
        auth_commit = [c for c in commits if "auth" in c["subject"].lower() and "readme" not in c["subject"].lower()][0]
        item = kp.get(auth_commit["id"])
        assert item is not None
        refs = item.tags.get("references", [])
        if isinstance(refs, str):
            refs = [refs]
        auth_refs = [r for r in refs if "auth.py" in r]
        assert len(auth_refs) > 0

    def test_commit_has_author_email(self, kp, git_repo):
        ingest_git_history(kp, git_repo)

        commits = get_commits_since(git_repo)
        item = kp.get(commits[0]["id"])
        assert item is not None
        assert item.tags.get("author") == "t@t.com"
        assert item.tags.get("git_author") == "Test"

    def test_commit_has_created_at(self, kp, git_repo):
        ingest_git_history(kp, git_repo)

        commits = get_commits_since(git_repo)
        item = kp.get(commits[0]["id"])
        assert item is not None
        # created should reflect commit date, not ingest time
        assert item.created is not None


class TestRepoNameParsing:
    """Test remote URL parsing edge cases."""

    def test_ssh_url(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:acme/project.git"],
            cwd=str(repo), capture_output=True, check=True,
        )
        assert _repo_name(repo) == "github.com/acme/project"

    def test_https_url(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "https://gitlab.com/team/sub/repo.git"],
            cwd=str(repo), capture_output=True, check=True,
        )
        assert _repo_name(repo) == "gitlab.com/team/sub/repo"

    def test_https_with_token(self, tmp_path):
        """Credentials in URL must be stripped."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin",
             "https://x-access-token:github_pat_SECRET@github.com/acme/private.git"],
            cwd=str(repo), capture_output=True, check=True,
        )
        assert _repo_name(repo) == "github.com/acme/private"

    def test_ssh_with_user(self, tmp_path):
        """ssh:// with username must strip userinfo."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin",
             "ssh://deploy@github.com/acme/project.git"],
            cwd=str(repo), capture_output=True, check=True,
        )
        assert _repo_name(repo) == "github.com/acme/project"

    def test_no_remote(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=str(repo), capture_output=True, check=True)
        assert _repo_name(repo) == str(repo.resolve())

    def test_empty_repo(self, tmp_path):
        """Repo with no commits should return 0 commits."""
        repo = tmp_path / "empty"
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=str(repo), capture_output=True, check=True)
        commits = get_commits_since(repo)
        assert commits == []
