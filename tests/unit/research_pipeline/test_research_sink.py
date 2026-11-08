from vibe.research_pipeline.research_sink import _repository_root


def test_research_sink_resolves_the_linked_worktree_root():
    root = _repository_root()

    assert (root / ".git").exists()
    assert (root / "vibe" / "research_pipeline" / "research_sink.py").is_file()
