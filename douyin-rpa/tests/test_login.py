from __future__ import annotations

import time

import pytest

from conftest import login
from douyin_rpa.browser import open_browser
from douyin_rpa.errors import LoginTimeout
from douyin_rpa.login import capture_login_qr, is_logged_in, login_flow, status_flow, wait_for_login


def test_qr_capture_and_login_state(browser_context, mock_server, tmp_path):
    page = browser_context.new_page()
    page.goto(mock_server.url, wait_until="domcontentloaded")

    assert not is_logged_in(page)
    captured = capture_login_qr(page, tmp_path / "qr.png")
    assert captured is not None
    qr_path, digest = captured
    assert qr_path.exists()
    assert qr_path.stat().st_size > 0
    assert len(digest) == 64

    login(browser_context, mock_server)
    page.goto(mock_server.url, wait_until="domcontentloaded")
    assert is_logged_in(page)


def test_wait_for_login_times_out_without_scan(browser_context, mock_server, tmp_path):
    page = browser_context.new_page()
    page.goto(mock_server.url, wait_until="domcontentloaded")

    started = time.monotonic()
    with pytest.raises(LoginTimeout):
        wait_for_login(page, qr_path=tmp_path / "qr.png", timeout_s=0.2, poll_ms=100)
    assert time.monotonic() - started < 10


def test_status_flow_persists_session(tmp_path, mock_server):
    profile = tmp_path / "profile"
    with open_browser(profile_dir=profile, headless=True, browser="chrome") as context:
        login(context, mock_server)
        context.storage_state(path=str(profile / "storage-state.json"))

    assert status_flow(profile_dir=profile, base_url=mock_server.url, timeout_s=8) is True
    assert status_flow(profile_dir=tmp_path / "other", base_url=mock_server.url, timeout_s=5) is False


def test_login_flow_simulated_scan(tmp_path, mock_server):
    mock_server.auto_login_after_ms = 7000
    qr = tmp_path / "qr.png"
    result = login_flow(
        profile_dir=tmp_path / "profile",
        headless=True,
        base_url=mock_server.url,
        qr_path=qr,
        timeout_s=25,
    )
    assert not result.already_logged_in
    assert qr.exists() and qr.stat().st_size > 0
    assert (tmp_path / "profile" / "storage-state.json").exists()
    assert "creator-micro" in result.page_url
