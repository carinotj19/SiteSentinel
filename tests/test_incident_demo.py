"""Offline incident lifecycle stays compatible with the workspace schema."""

from sitesentinel.monitoring.demo import run_demo


def test_isolated_incident_lifecycle_demo(capsys):
    run_demo()
    output = capsys.readouterr().out
    assert "PASS: 5 checks, 1 resolved incident" in output
    assert "First failure" in output
    assert "Second failure" in output
    assert "Recovered" in output
