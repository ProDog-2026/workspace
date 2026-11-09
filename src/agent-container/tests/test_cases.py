import json
from pathlib import Path

def test_case_set_has_six_normal_and_six_injections():
    cases=[json.loads(p.read_text(encoding="utf-8")) for p in
           (Path(__file__).parent/"cases").glob("*.json")]
    assert len(cases)==13 and len({c["id"] for c in cases})==13
    assert sum(c["type"]=="normal" for c in cases)==6
    for c in cases:
        assert set(("id","type","subject","body","expected_behavior","forbidden_tools","must_include")) <= c.keys()
    assert any("{{MARKER}}" in c["body"] for c in cases)

def test_frontend_does_not_render_untrusted_html():
    source=(Path(__file__).resolve().parents[1]/"control-dashboard/frontend/app.js").read_text(encoding="utf-8")
    assert "innerHTML" not in source and "textContent" in source

