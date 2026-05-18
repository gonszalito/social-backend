import time

import jwt


def _make_token(private_pem: str, sub: str) -> str:
    now = int(time.time())
    payload = {"sub": sub, "jti": f"jti_{sub}_{now}", "exp": now + 3600}
    return jwt.encode(payload, private_pem, algorithm="RS256")


def test_social_profile_sets_cache_control(client, rsa_keys):
    token = _make_token(rsa_keys["private_pem"], sub="u1")
    r = client.get("/social/profile/u2", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.headers.get("Cache-Control") == "public, max-age=300"


def test_social_feed_pagination_50_events_non_overlapping(client, rsa_keys):
    follower = "u_follower"
    target = "u_target"
    follower_token = _make_token(rsa_keys["private_pem"], sub=follower)
    target_token = _make_token(rsa_keys["private_pem"], sub=target)

    r = client.post(
        f"/social/follow/{target}",
        headers={"Authorization": f"Bearer {follower_token}"},
    )
    assert r.status_code == 200

    # create 50 events from the followed user
    for i in range(50):
        rr = client.post(
            "/social/recipe-share",
            json={"recipe_id": f"r{i}"},
            headers={"Authorization": f"Bearer {target_token}"},
        )
        assert rr.status_code == 200

    page1 = client.get("/social/feed", headers={"Authorization": f"Bearer {follower_token}"})
    assert page1.status_code == 200
    j1 = page1.json()
    assert len(j1["items"]) == 20
    assert j1["next_cursor"]

    page2 = client.get(
        "/social/feed",
        params={"cursor": j1["next_cursor"]},
        headers={"Authorization": f"Bearer {follower_token}"},
    )
    assert page2.status_code == 200
    j2 = page2.json()
    assert len(j2["items"]) == 20

    ids1 = {it["event_id"] for it in j1["items"]}
    ids2 = {it["event_id"] for it in j2["items"]}
    assert ids1.isdisjoint(ids2)


def test_social_follow_unfollow_affects_profile_counts(client, rsa_keys):
    a = "u_a"
    b = "u_b"
    a_token = _make_token(rsa_keys["private_pem"], sub=a)
    b_token = _make_token(rsa_keys["private_pem"], sub=b)

    r0 = client.get(f"/social/profile/{b}", headers={"Authorization": f"Bearer {a_token}"})
    assert r0.status_code == 200
    assert r0.json()["followers"] == 0

    rf = client.post(f"/social/follow/{b}", headers={"Authorization": f"Bearer {a_token}"})
    assert rf.status_code == 200

    r1 = client.get(f"/social/profile/{b}", headers={"Authorization": f"Bearer {b_token}"})
    assert r1.status_code == 200
    assert r1.json()["followers"] == 1

    ru = client.delete(f"/social/unfollow/{b}", headers={"Authorization": f"Bearer {a_token}"})
    assert ru.status_code == 200

    r2 = client.get(f"/social/profile/{b}", headers={"Authorization": f"Bearer {a_token}"})
    assert r2.status_code == 200
    assert r2.json()["followers"] == 0


def test_social_recipe_share_adds_event_to_owner_feed(client, rsa_keys):
    target = "u_owner_feed_target"
    target_token = _make_token(rsa_keys["private_pem"], sub=target)
    rr = client.post(
        "/social/recipe-share",
        json={"recipe_id": "r1"},
        headers={"Authorization": f"Bearer {target_token}"},
    )
    assert rr.status_code == 200
    event_id = rr.json()["event_id"]

    page = client.get("/social/feed", headers={"Authorization": f"Bearer {target_token}"})
    assert page.status_code == 200
    items = page.json()["items"]
    assert any(item["event_id"] == event_id for item in items)


def test_social_feed_is_trimmed_to_50_newest_events(client, rsa_keys):
    actor = "u_feed_trim_actor"
    actor_token = _make_token(rsa_keys["private_pem"], sub=actor)

    for i in range(55):
        rr = client.post(
            "/social/recipe-share",
            json={"recipe_id": f"r{i}"},
            headers={"Authorization": f"Bearer {actor_token}"},
        )
        assert rr.status_code == 200
        time.sleep(0.002)

    collected_recipe_ids: list[str] = []
    cursor = None
    for _ in range(3):
        params = {"cursor": cursor} if cursor else None
        page = client.get("/social/feed", params=params, headers={"Authorization": f"Bearer {actor_token}"})
        assert page.status_code == 200
        payload = page.json()
        collected_recipe_ids.extend([item["data"].get("recipe_id") for item in payload["items"]])
        cursor = payload["next_cursor"]
        if not cursor:
            break

    assert len(collected_recipe_ids) == 50
    assert "r54" in collected_recipe_ids
    assert "r0" not in collected_recipe_ids
    assert "r1" not in collected_recipe_ids
    assert "r2" not in collected_recipe_ids
    assert "r3" not in collected_recipe_ids
    assert "r4" not in collected_recipe_ids


def test_social_potluck_endpoints_publish_pubsub_events(client, rsa_keys, fake_redis):
    token = _make_token(rsa_keys["private_pem"], sub="u_potluck")

    rc = client.post(
        "/social/potluck/create",
        json={"title": "Dinner"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert rc.status_code == 200
    potluck_id = rc.json()["potluck_id"]

    ri = client.post(
        "/social/potluck/invite",
        json={"potluck_id": potluck_id, "invitee_user_id": "u_friend"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert ri.status_code == 200

    assert any(ch == "social:potluck" for ch, _msg in fake_redis.published)

