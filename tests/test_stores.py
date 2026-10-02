API = "/api/v1/stores"


async def test_store_crud(client, admin_headers):
    created = await client.post(
        API,
        json={"name": "Amazon", "website_url": "https://amazon.com"},
        headers=admin_headers,
    )
    assert created.status_code == 201
    store = created.json()
    assert store["slug"] == "amazon"
    assert store["is_active"] is True

    assert (await client.get(f"{API}/by-slug/amazon")).json()["id"] == store["id"]

    updated = await client.patch(
        f"{API}/{store['id']}", json={"is_active": False}, headers=admin_headers
    )
    assert updated.json()["is_active"] is False

    assert (await client.delete(f"{API}/{store['id']}", headers=admin_headers)).status_code == 204
    assert (await client.get(f"{API}/{store['id']}")).status_code == 404


async def test_store_search(client, admin_headers):
    for name in ("Amazon", "AliExpress", "eBay"):
        await client.post(API, json={"name": name}, headers=admin_headers)

    hits = await client.get(API, params={"search": "ali"})
    assert hits.json()["total"] == 1
    assert hits.json()["items"][0]["name"] == "AliExpress"


async def test_store_rbac(client, user_headers, editor_headers):
    payload = {"name": "Nope"}
    assert (await client.post(API, json=payload)).status_code == 401
    assert (await client.post(API, json=payload, headers=user_headers)).status_code == 403
    assert (await client.post(API, json=payload, headers=editor_headers)).status_code == 201


async def test_commission_disclosure_round_trip(client, admin_headers):
    created = await client.post(
        API,
        json={"name": "Nike", "commission_disclosure": "We earn 5% from Nike on this link."},
        headers=admin_headers,
    )
    assert created.json()["commission_disclosure"] == "We earn 5% from Nike on this link."

    no_disclosure = (
        await client.post(API, json={"name": "Local Shop"}, headers=admin_headers)
    ).json()
    assert no_disclosure["commission_disclosure"] is None
