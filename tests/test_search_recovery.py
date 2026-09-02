from utils import search_engine
from utils.search_engine import PostDocument, PostSearchEngine


def test_failed_index_rebuild_restores_backup(tmp_path, monkeypatch):
    index_dir = tmp_path / "index"
    engine = PostSearchEngine(str(index_dir))
    engine.add_post(PostDocument(message_id=1, title="kept"))

    def fail_create(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(search_engine.index, "create_in", fail_create)
    engine._rebuild_incompatible_index()

    assert engine.search("kept").total_results == 1
    assert not (tmp_path / "index.backup").exists()
