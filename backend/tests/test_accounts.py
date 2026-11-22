"""Username + password sign in, self-registering devices (no pairing) and manager staff admin."""
from rest_framework.test import APIClient

from apps.core.models import Device, StaffSession


def login(c, username, password, **extra):
    return c.post("/api/v1/login", {"username": username, "password": password, **extra}, format="json")


def auth(c, token):
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return c


def test_login_registers_device_once_and_reuses_it(demo):
    c = APIClient()
    r = login(c, "Bilal", "bilal123", device_name="Front tablet")  # username is case-insensitive
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["branch_code"] == "B1" and body["roles"] == ["waiter"] and body["device_key"]
    r2 = login(APIClient(), "sana", "sana123", device_key=body["device_key"], device_name="Front tablet")
    assert r2.json()["device_id"] == body["device_id"], "same device, other person"
    assert Device.objects.filter(name="Front tablet").count() == 1
    me = auth(APIClient(), body["session_token"]).get(f"/api/v1/branches/{body['branch_id']}/bootstrap")
    assert me.status_code == 200 and me.json()["staff"]["username"] == "bilal"


def test_wrong_password_and_blocked_device(demo, manager):
    assert login(APIClient(), "bilal", "nope").status_code == 401
    r = login(APIClient(), "bilal", "bilal123").json()
    assert manager.post(f"devices/{r['device_id']}/revoke", key=False).status_code == 200
    again = login(APIClient(), "bilal", "bilal123", device_key=r["device_key"])
    assert again.status_code == 403 and again.json()["code"] == "device_blocked"
    old = auth(APIClient(), r["session_token"]).get(f"/api/v1/branches/{r['branch_id']}/bootstrap")
    assert old.status_code == 401


def test_owner_with_two_branches_chooses(demo, settings):
    r = login(APIClient(), "ayesha", "ayesha123")
    assert r.status_code == 409 and {b["code"] for b in r.json()["branches"]} == {"B1", "B2"}
    assert login(APIClient(), "ayesha", "ayesha123", branch="B2").json()["branch_code"] == "B2"
    settings.LOCAL_BRANCH = "B1"
    assert login(APIClient(), "ayesha", "ayesha123").json()["branch_code"] == "B1"


def test_manager_creates_staff_resets_password_and_switches_off(demo, manager, waiter):
    r = manager.post("staff", {"username": "ali", "display_name": "Ali", "password": "ali12345",
                               "roles": ["cashier"]})
    assert r.status_code == 201, r.content
    ali = r.json()
    assert ali["roles"] == ["cashier"]
    assert manager.post("staff", {"username": "ALI", "display_name": "x", "password": "123456",
                                  "roles": ["waiter"]}).status_code == 409
    assert manager.post("staff", {"username": "z", "display_name": "z", "password": "123",
                                  "roles": ["waiter"]}).json()["field_errors"] == {"password": "too_short"}
    assert waiter.post("staff", {"username": "q"}).status_code == 403
    assert manager.post("staff", {"username": "boss", "display_name": "B", "password": "123456",
                                  "roles": ["owner"]}).status_code == 403, "manager cannot mint owners"

    sess = login(APIClient(), "ali", "ali12345").json()
    assert manager.post(f"staff/{ali['id']}", {"password": "newpass1", "roles": ["cashier", "waiter"]}).status_code == 200
    assert login(APIClient(), "ali", "ali12345").status_code == 401
    assert login(APIClient(), "ali", "newpass1").json()["roles"] == ["cashier", "waiter"]

    assert manager.post(f"staff/{ali['id']}", {"is_active": False}).status_code == 200
    assert not StaffSession.objects.filter(staff_id=ali["id"], revoked_at__isnull=True).exists()
    assert auth(APIClient(), sess["session_token"]).get(f"/api/v1/branches/{sess['branch_id']}/bootstrap").status_code == 401
    assert login(APIClient(), "ali", "newpass1").status_code == 401
    names = [s["username"] for s in manager.get("staff").json()["staff"]]
    assert "ali" in names and "web-orders" not in names


def test_change_my_password_signs_out_other_devices(demo):
    a = login(APIClient(), "bilal", "bilal123").json()
    b = login(APIClient(), "bilal", "bilal123").json()
    c = auth(APIClient(), a["session_token"])
    url = f"/api/v1/branches/{a['branch_id']}/me/password"
    assert c.post(url, {"current_password": "bad", "new_password": "x"}, format="json").status_code == 400
    assert c.post(url, {"current_password": "bilal123", "new_password": "bilal999"}, format="json").status_code == 200
    assert c.get(f"/api/v1/branches/{a['branch_id']}/bootstrap").status_code == 200
    assert auth(APIClient(), b["session_token"]).get(f"/api/v1/branches/{b['branch_id']}/bootstrap").status_code == 401
    assert login(APIClient(), "bilal", "bilal999").status_code == 201
