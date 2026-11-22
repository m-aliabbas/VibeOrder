import uuid

import pytest
from rest_framework.test import APIClient

from apps.core.fixtures_demo import seed


@pytest.fixture
def demo(db, tmp_path, settings):
    settings.PRINT_SPOOL_DIR = tmp_path / "spool"
    return seed()


class Staff:
    """API client acting as one operator signed in on their own device."""

    def __init__(self, branch, username, password, kind="tablet"):
        self.branch = branch
        self.client = APIClient()
        r = self.client.post("/api/v1/login", {"username": username, "password": password, "branch": branch.code,
                                               "device_name": f"{username}-device", "device_kind": kind},
                             format="json")
        assert r.status_code == 201, r.content
        self.device_key = r.json()["device_key"]
        self.session = r.json()["session_token"]

    def url(self, path):
        return f"/api/v1/branches/{self.branch.pk}/{path}"

    def headers(self, key=None):
        h = {"HTTP_AUTHORIZATION": f"Bearer {self.session}"}
        if key is not False:
            h["HTTP_IDEMPOTENCY_KEY"] = str(key or uuid.uuid4())
        return h

    def get(self, path, **params):
        return self.client.get(self.url(path), params, **self.headers(False))

    def post(self, path, data=None, key=None):
        return self.client.post(self.url(path), data or {}, format="json", **self.headers(key))


@pytest.fixture
def staff_factory(demo):
    def make(username, password, kind="tablet"):
        return Staff(demo["branch"], username, password, kind)
    return make


@pytest.fixture
def waiter(staff_factory):
    return staff_factory("bilal", "bilal123")


@pytest.fixture
def waiter2(staff_factory):
    return staff_factory("sana", "sana123")


@pytest.fixture
def cashier(staff_factory):
    return staff_factory("hamza", "hamza123", "cashier")


@pytest.fixture
def cook(staff_factory):
    return staff_factory("usman", "usman123", "kds")


@pytest.fixture
def manager(staff_factory):
    return staff_factory("zara", "zara123")


MIXED_PIZZA = {"client_line_id": "L-phone-1", "product": "mixed-pizza", "variant": "large", "quantity": 1,
               "crust": "stuffed", "halves": {"left": "tikka", "right": "fajita"},
               "options": [{"id": "extra-cheese", "side": "right"}], "fire": "now"}


def table_id(n):
    from apps.core.fixtures_demo import did
    return str(did(f"table-{n}"))


def open_table(staff, n=6, guests=2):
    r = staff.post("checks", {"service_type": "dine_in", "table_id": table_id(n), "guest_count": guests})
    assert r.status_code == 201, r.content
    return r.json()


def add_round(staff, check_id, items, key=None):
    q = staff.post("quotes", {"items": items}, key=False)
    assert q.status_code == 201, q.content
    q = q.json()
    r = staff.post(f"checks/{check_id}/rounds", {"quote_id": q["quote_id"], "menu_release": q["menu_release"], "items": items}, key=key)
    return q, r
