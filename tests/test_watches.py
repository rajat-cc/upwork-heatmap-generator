from __future__ import annotations

import core.watches as watches


def test_parse_pasted_search_url_with_labels_and_ids():
    text = """
    # comment line
    US Based | https://www.upwork.com/nx/search/jobs/?category2_uid=5317,5318&q=n8n%20automation&subcategory2_uid=99
    n8n automation
    Skills | ontology_skill_uid=1,2&occupation_uid=3
    """
    parsed = watches.parse_watches_text(text)
    assert [w.label for w in parsed] == ["US Based", "n8n automation", "Skills"]
    assert parsed[0].search_expression == "n8n automation"
    assert parsed[0].category_ids == ["5317", "5318"]
    assert parsed[0].subcategory_ids == ["99"]
    assert parsed[1].search_expression == "n8n automation"
    assert parsed[1].category_ids == []
    assert parsed[2].skill_ids == ["1", "2", "3"]
    assert "2 cat" in parsed[0].summary()


def test_load_watches_prefers_env_file_then_agent_then_repo_then_default(tmp_path, monkeypatch):
    env_file = tmp_path / "env.txt"
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    monkeypatch.setattr(watches, "WATCHES_FILE", str(env_file))
    monkeypatch.setattr(watches, "AGENT_DIR", str(agent_dir))
    monkeypatch.setattr(watches, "ROOT_DIR", str(repo_dir))

    ws, source = watches.load_watches()
    assert source == "default" and ws[0].search_expression == "n8n"

    (repo_dir / "watch_terms.txt").write_text("python etl\n")
    ws, source = watches.load_watches()
    assert source.endswith("watch_terms.txt") and ws[0].search_expression == "python etl"

    (agent_dir / "watches.txt").write_text(
        "Agent | https://www.upwork.com/nx/search/jobs/?q=make.com\n"
    )
    ws, source = watches.load_watches()
    assert source.endswith("agent/watches.txt") and ws[0].label == "Agent"

    env_file.write_text("Env | q=zapier\n")
    ws, source = watches.load_watches()
    assert source.endswith("env.txt") and ws[0].search_expression == "zapier"
