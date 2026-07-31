from fastapi.testclient import TestClient


def test_register_creates_user(client: TestClient) -> None:
    response = client.post(
        "/auth/register", json={"email": "ada@cindra.dev", "password": "supersecret1"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "ada@cindra.dev"
    assert body["role"] == "solo"
    assert "id" in body
    assert "hashed_password" not in body


def test_register_accepts_agency_role(client: TestClient) -> None:
    response = client.post(
        "/auth/register",
        json={"email": "agency@cindra.dev", "password": "supersecret1", "role": "agency"},
    )
    assert response.status_code == 201
    assert response.json()["role"] == "agency"


def test_update_me_changes_role(client: TestClient) -> None:
    payload = {"email": "ada@cindra.dev", "password": "supersecret1"}
    client.post("/auth/register", json=payload)
    token = client.post("/auth/login", json=payload).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = client.patch("/auth/me", json={"role": "agency"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["role"] == "agency"

    me_response = client.get("/auth/me", headers=headers)
    assert me_response.json()["role"] == "agency"


def test_register_duplicate_email_conflicts(client: TestClient) -> None:
    payload = {"email": "ada@cindra.dev", "password": "supersecret1"}
    assert client.post("/auth/register", json=payload).status_code == 201
    response = client.post("/auth/register", json=payload)
    assert response.status_code == 409


def test_login_returns_token_and_me_resolves_it(client: TestClient) -> None:
    payload = {"email": "ada@cindra.dev", "password": "supersecret1"}
    client.post("/auth/register", json=payload)

    login_response = client.post("/auth/login", json=payload)
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    me_response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "ada@cindra.dev"


def test_login_wrong_password_rejected(client: TestClient) -> None:
    client.post(
        "/auth/register", json={"email": "ada@cindra.dev", "password": "supersecret1"}
    )
    response = client.post(
        "/auth/login", json={"email": "ada@cindra.dev", "password": "wrong-password"}
    )
    assert response.status_code == 401


def test_me_without_token_rejected(client: TestClient) -> None:
    response = client.get("/auth/me")
    assert response.status_code == 401
