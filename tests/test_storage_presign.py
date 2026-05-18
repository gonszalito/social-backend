from pathlib import Path
from urllib.parse import parse_qs, urlparse


def test_presign_happy_path(client):
    r = client.post(
        "/api/v1/storage/presign",
        json={"upload_type": "avatar", "content_type": "image/png"},
    )
    assert r.status_code == 200
    body = r.json()

    assert body["alias"]
    assert body["max_size_mb"] == 2
    assert body["format"]
    assert body["presigned_url"]

    url = body["presigned_url"]
    lowered = url.lower()
    assert "backblaze" not in lowered
    assert "/b2" not in lowered
    assert "b2." not in lowered
    assert "/api/upload" in url
    assert "upload_type=avatar" in url
    assert "upload_id=" in url


def test_upload_happy_path_from_presign(client, monkeypatch, tmp_path):
    monkeypatch.setenv("GOBIG_UPLOAD_STAGING_DIR", str(tmp_path))

    presign = client.post(
        "/api/v1/storage/presign",
        json={"upload_type": "avatar", "content_type": "image/png"},
    )
    assert presign.status_code == 200
    presigned_url = presign.json()["presigned_url"]

    parsed = urlparse(presigned_url)
    query = parse_qs(parsed.query)
    upload_type = query["upload_type"][0]
    upload_id = query["upload_id"][0]

    upload = client.post(
        "/api/upload",
        params={"upload_type": upload_type, "upload_id": upload_id},
        files={"file": ("avatar.png", b"fake-image-bytes", "image/png")},
    )
    assert upload.status_code == 200
    body = upload.json()
    assert body["status"] == "uploaded"
    assert body["upload_type"] == "avatar"
    assert body["upload_id"] == upload_id
    assert body["filename"] == "avatar.png"
    assert body["size_bytes"] == len(b"fake-image-bytes")

    stored = Path(body["stored_path"])
    assert stored.exists()
    assert stored.read_bytes() == b"fake-image-bytes"


def test_upload_rejects_oversized_file(client, monkeypatch, tmp_path):
    monkeypatch.setenv("GOBIG_UPLOAD_STAGING_DIR", str(tmp_path))
    too_large = b"a" * ((2 * 1024 * 1024) + 1)

    r = client.post(
        "/api/upload",
        params={"upload_type": "avatar", "upload_id": "oversize-1"},
        files={"file": ("large.png", too_large, "image/png")},
    )
    assert r.status_code == 413
    assert "Max 2MB" in r.json()["detail"]


def test_presign_rejects_empty_content_type(client):
    r = client.post(
        "/api/v1/storage/presign",
        json={"upload_type": "avatar", "content_type": ""},
    )
    assert r.status_code == 422


def test_presign_rejects_invalid_upload_type(client):
    r = client.post(
        "/api/v1/storage/presign",
        json={"upload_type": "not_a_type", "content_type": "application/octet-stream"},
    )
    assert r.status_code == 422

