"""Static live-dashboard tests: served at /dashboard, consumes /twin/stream.

The dashboard's HTML/JS is a static asset (omni/api/static/index.html)
mounted via StaticFiles — its content isn't generated per-request, but this
still guards two real regressions: the mount actually resolving to a
readable file (a wrong STATIC_DIR path 404s silently), and the packaging
config that ships api/static/* inside the installed wheel (setuptools
drops non-.py files from a package unless package-data lists them; see
pyproject.toml's [tool.setuptools.package-data])."""

from pathlib import Path

from fastapi.testclient import TestClient

from omni.api.main import app

client = TestClient(app)


def test_dashboard_serves_html():
    r = client.get("/dashboard/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "OMNIMIND" in r.text


def test_dashboard_connects_to_the_live_twin_stream():
    r = client.get("/dashboard/")
    assert "/twin/stream" in r.text


def test_dashboard_root_redirects_or_serves_index():
    # StaticFiles(html=True) serves index.html for the mount root
    r = client.get("/dashboard")
    assert r.status_code in (200, 307)  # 307 if it redirects to the trailing slash first


def test_static_dir_is_declared_as_package_data():
    """Guards the packaging bug directly: pip install . must ship
    omni/api/static/* in the installed package, not just in the source
    checkout. See the regression this caught before deploy."""
    pyproject = (Path(__file__).parent.parent / "pyproject.toml").read_text()
    assert "[tool.setuptools.package-data]" in pyproject
    assert "api/static" in pyproject
