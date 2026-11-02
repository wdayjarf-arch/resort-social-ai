from uuid import uuid4

from app.db.models.identity import (
    Membership, MembershipRole, Organization, Property, SocialAccount, Provider, User
)
from app.security.passwords import hash_password


def make_user(db, email):
    user = User(email=email, display_name=email.split("@")[0], password_hash=hash_password("very-secure-test-password"))
    db.add(user)
    db.flush()
    return user


def make_org(db, name, slug, user, role=MembershipRole.owner):
    org = Organization(name=name, slug=slug)
    db.add(org)
    db.flush()
    db.add(Membership(organization_id=org.id, user_id=user.id, role=role, is_active=True))
    db.flush()
    return org


def login(client, email):
    response = client.post("/api/v1/auth/login", data={
        "username": email, "password": "very-secure-test-password"
    })
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_user_cannot_list_or_read_other_org_properties(client, db_session):
    user_a = make_user(db_session, "user-a@example.test")
    user_b = make_user(db_session, "user-b@example.test")
    org_a = make_org(db_session, "Org A", "org-a", user_a)
    org_b = make_org(db_session, "Org B", "org-b", user_b)
    property_b = Property(
        organization_id=org_b.id, name="Private Resort", slug="private-resort", timezone="Asia/Aden"
    )
    db_session.add(property_b)
    db_session.commit()

    headers_a = login(client, user_a.email)

    list_response = client.get(f"/api/v1/organizations/{org_b.id}/properties", headers=headers_a)
    assert list_response.status_code == 404

    read_response = client.get(
        f"/api/v1/organizations/{org_b.id}/properties/{property_b.id}", headers=headers_a
    )
    assert read_response.status_code == 404

    own_response = client.get(f"/api/v1/organizations/{org_a.id}/properties", headers=headers_a)
    assert own_response.status_code == 200
    assert own_response.json() == []


def test_user_cannot_mutate_other_org_property(client, db_session):
    user_a = make_user(db_session, "writer-a@example.test")
    user_b = make_user(db_session, "writer-b@example.test")
    org_a = make_org(db_session, "Org A", "org-a-write", user_a)
    org_b = make_org(db_session, "Org B", "org-b-write", user_b)
    property_b = Property(
        organization_id=org_b.id, name="Do Not Rename", slug="do-not-rename", timezone="Asia/Aden"
    )
    db_session.add(property_b)
    db_session.commit()
    headers_a = login(client, user_a.email)

    response = client.patch(
        f"/api/v1/organizations/{org_b.id}/properties/{property_b.id}",
        headers=headers_a,
        json={"name": "Tampered"},
    )
    assert response.status_code == 404
    db_session.refresh(property_b)
    assert property_b.name == "Do Not Rename"


def test_user_cannot_read_other_org_social_accounts(client, db_session):
    user_a = make_user(db_session, "social-a@example.test")
    user_b = make_user(db_session, "social-b@example.test")
    make_org(db_session, "Org A", "org-a-social", user_a)
    org_b = make_org(db_session, "Org B", "org-b-social", user_b)
    account = SocialAccount(
        organization_id=org_b.id, provider=Provider.meta,
        provider_account_id="page-123", display_name="Private Facebook Page",
        granted_scopes=[], metadata_json={},
    )
    db_session.add(account)
    db_session.commit()
    headers_a = login(client, user_a.email)

    list_response = client.get(f"/api/v1/organizations/{org_b.id}/social-accounts", headers=headers_a)
    assert list_response.status_code == 404
    read_response = client.get(
        f"/api/v1/organizations/{org_b.id}/social-accounts/{account.id}", headers=headers_a
    )
    assert read_response.status_code == 404


def test_logout_revokes_access_token(client, db_session):
    user = make_user(db_session, "logout@example.test")
    org = make_org(db_session, "Logout Org", "logout-org", user)
    db_session.commit()
    headers = login(client, user.email)
    assert client.get(f"/api/v1/organizations/{org.id}", headers=headers).status_code == 200
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
    assert client.get(f"/api/v1/organizations/{org.id}", headers=headers).status_code == 401


def test_agent_cannot_manage_properties(client, db_session):
    owner = make_user(db_session, "owner@example.test")
    agent = make_user(db_session, "agent@example.test")
    org = make_org(db_session, "Agent Org", "agent-org", owner)
    db_session.add(Membership(
        organization_id=org.id, user_id=agent.id, role=MembershipRole.agent, is_active=True
    ))
    db_session.commit()
    headers = login(client, agent.email)
    response = client.post(
        f"/api/v1/organizations/{org.id}/properties",
        headers=headers,
        json={"name": "New Property", "slug": "new-property", "timezone": "Asia/Aden"},
    )
    assert response.status_code == 403
