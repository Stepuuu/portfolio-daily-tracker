import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def images(tmp_path, monkeypatch):
    # chat.py creates its data directories relative to the working directory on import.
    monkeypatch.chdir(tmp_path)
    from backend.api import chat

    image_dir = tmp_path / "conversation_images"
    image_dir.mkdir()
    monkeypatch.setattr(chat, "IMAGES_DIR", image_dir)
    (image_dir / "120000000000_0.jpg").write_bytes(b"\xff\xd8\xff\xe0image")
    (tmp_path / "config.json").write_text('{"api_key": "sentinel"}')
    (tmp_path / "outside.jpg").write_bytes(b"sentinel")
    app = FastAPI()
    app.include_router(chat.router, prefix="/api/chat")
    return TestClient(app), tmp_path, image_dir


def get(client, path):
    return client.get("/api/chat/conversation-images/" + path)


def test_saved_image_is_served_by_name_and_by_stored_relative_path(images):
    client, _, _ = images
    for stored in ("120000000000_0.jpg", "data/conversation_images/120000000000_0.jpg"):
        response = get(client, stored)
        assert response.status_code == 200
        assert response.content == b"\xff\xd8\xff\xe0image"
        assert response.headers["content-type"] == "image/jpeg"


def test_absolute_paths_cannot_read_files_outside_the_image_directory(images):
    client, root, _ = images
    for target in (root / "config.json", root / "outside.jpg"):
        response = get(client, str(target))
        assert response.status_code == 404
        assert "sentinel" not in response.text


@pytest.mark.parametrize("path", [
    "config.json",
    "outside.jpg",
    "../config.json",
    "%2e%2e/config.json",
    "%2e%2e/outside.jpg",
    "conversation_images/../outside.jpg",
    "conversation_images/%2e%2e/config.json",
    # An existing image name is only accepted bare or under the stored prefix.
    "other/120000000000_0.jpg",
    "data/other/120000000000_0.jpg",
    "data/conversation_images/nested/120000000000_0.jpg",
    "%2e%2e/120000000000_0.jpg",
    "data/conversation_images/%2e%2e/120000000000_0.jpg",
    "data/conversation_images/%2e%2e/outside.jpg",
    "bad%00.jpg",
    "120000000000_0.jpg%00.txt",
])
def test_relative_and_parent_paths_cannot_escape(images, path):
    client, _, _ = images
    response = get(client, path)
    assert response.status_code == 404
    assert "sentinel" not in response.text


@pytest.mark.parametrize("path", [
    # The HTTP client normalises literal dot segments, so call the handler with them directly.
    "../config.json",
    "../outside.jpg",
    "conversation_images/../outside.jpg",
    "data/conversation_images/../outside.jpg",
    "data/conversation_images/../../outside.jpg",
    "..\\outside.jpg",
    "data\\conversation_images\\..\\outside.jpg",
    "/etc/passwd",
    "",
    ".",
    "..",
    "..jpg/../outside.jpg",
    "bad\x00.jpg",
])
def test_handler_rejects_literal_traversal(images, path):
    import asyncio
    from fastapi import HTTPException
    from backend.api import chat

    with pytest.raises(HTTPException) as error:
        asyncio.run(chat.serve_conversation_image(path))
    assert error.value.status_code == 404


def test_non_image_files_inside_the_directory_are_not_served(images):
    client, _, image_dir = images
    (image_dir / "notes.json").write_text('{"api_key": "sentinel"}')
    response = get(client, "notes.json")
    assert response.status_code == 404
    assert "sentinel" not in response.text


def test_directories_are_not_served(images):
    client, _, image_dir = images
    (image_dir / "folder.jpg").mkdir()
    assert get(client, "folder.jpg").status_code == 404


def test_png_and_webp_keep_their_media_type(images):
    client, _, image_dir = images
    (image_dir / "a.png").write_bytes(b"png")
    (image_dir / "b.webp").write_bytes(b"webp")
    assert get(client, "a.png").headers["content-type"] == "image/png"
    assert get(client, "data/conversation_images/b.webp").headers["content-type"] == "image/webp"


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_symlink_inside_the_directory_cannot_point_outside(images):
    client, root, image_dir = images
    try:
        (image_dir / "link.jpg").symlink_to(root / "outside.jpg")
    except OSError:
        pytest.skip("symlinks unavailable")
    response = get(client, "link.jpg")
    assert response.status_code == 404
    assert "sentinel" not in response.text
