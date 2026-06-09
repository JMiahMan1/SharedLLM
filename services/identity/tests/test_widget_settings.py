import os
os.environ["INTERNAL_SECRET"] = "test-secret"
os.environ["FERNET_KEY"] = "bW9ja2VkLWtleS1mb3ItdGVzdGluZy1wdXJwb3NlcyE="

import json
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, StaticPool, select

from services.identity.main import app, require_api_key, require_internal
from services.identity.models import User, UserWidget

import services.identity.main as main


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture(name="client")
def client_fixture(session: Session):
    main.engine = session.bind
    assert main.engine is not None
    SQLModel.metadata.create_all(main.engine)

    from services.identity.seed import seed_from_env
    seed_from_env(session, force=True)

    admin_user = session.exec(select(User).where(User.username == "default")).first()
    assert admin_user is not None
    admin_user.is_admin = True
    session.add(admin_user)
    session.commit()

    app.dependency_overrides[require_api_key] = lambda: admin_user
    app.dependency_overrides[require_internal] = lambda: True

    client = TestClient(app)
    yield client
    app.dependency_overrides = {}


def test_get_widget_settings_returns_all_known_widgets(client: TestClient, session: Session):
    resp = client.get("/api/widgets/settings")
    assert resp.status_code == 200
    settings = resp.json()
    assert len(settings["widgets"]) == 8  # 8 known widget keys

    widget_keys = [w["widget_key"] for w in settings["widgets"]]
    assert "energy_insights" in widget_keys
    assert "ambient_timer" in widget_keys
    assert "quick_notes" in widget_keys
    assert "active_media" in widget_keys
    assert "chores_progress" in widget_keys
    assert "upcoming_events" in widget_keys
    assert "quick_assistant" in widget_keys
    assert "device_control" in widget_keys


def test_quick_assistant_hidden_by_default(client: TestClient):
    resp = client.get("/api/widgets/settings")
    assert resp.status_code == 200
    settings = resp.json()
    quick_assistant = next(w for w in settings["widgets"] if w["widget_key"] == "quick_assistant")
    assert quick_assistant["visibility"] == "hidden"


def test_widget_settings_returns_order_index(client: TestClient):
    resp = client.get("/api/widgets/settings")
    settings = resp.json()
    energy = next(w for w in settings["widgets"] if w["widget_key"] == "energy_insights")
    assert energy["order_index"] == 0
    chores = next(w for w in settings["widgets"] if w["widget_key"] == "chores_progress")
    assert chores["order_index"] == 4


def test_widget_settings_default_pinned_devices_empty(client: TestClient):
    resp = client.get("/api/widgets/settings")
    settings = resp.json()
    device_control = next(w for w in settings["widgets"] if w["widget_key"] == "device_control")
    assert device_control["pinned_devices"] == []


def test_widget_settings_default_config_empty(client: TestClient):
    resp = client.get("/api/widgets/settings")
    settings = resp.json()
    energy = next(w for w in settings["widgets"] if w["widget_key"] == "energy_insights")
    assert energy["config"] == {}


def test_update_widget_settings_pinning(client: TestClient, session: Session):
    resp = client.put(
        "/api/widgets/settings/energy_insights",
        json={"is_pinned": True}
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "SUCCESS"}

    widget = session.exec(
        select(UserWidget).where(
            UserWidget.username == "default",
            UserWidget.widget_key == "energy_insights"
        )
    ).first()
    assert widget is not None
    assert widget.is_pinned is True


def test_update_widget_settings_hiding(client: TestClient, session: Session):
    resp = client.put(
        "/api/widgets/settings/ambient_timer",
        json={"visibility": "hidden"}
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "SUCCESS"}

    widget = session.exec(
        select(UserWidget).where(
            UserWidget.username == "default",
            UserWidget.widget_key == "ambient_timer"
        )
    ).first()
    assert widget is not None
    assert widget.visibility == "hidden"


def test_update_widget_settings_removing(client: TestClient, session: Session):
    resp = client.put(
        "/api/widgets/settings/active_media",
        json={"visibility": "removed"}
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "SUCCESS"}

    widget = session.exec(
        select(UserWidget).where(
            UserWidget.username == "default",
            UserWidget.widget_key == "active_media"
        )
    ).first()
    assert widget is not None
    assert widget.visibility == "removed"


def test_update_widget_settings_size(client: TestClient, session: Session):
    resp = client.put(
        "/api/widgets/settings/quick_notes",
        json={"size": "wide"}
    )
    assert resp.status_code == 200

    widget = session.exec(
        select(UserWidget).where(
            UserWidget.username == "default",
            UserWidget.widget_key == "quick_notes"
        )
    ).first()
    assert widget is not None
    assert widget.size == "wide"


def test_update_widget_settings_sort_mode(client: TestClient, session: Session):
    resp = client.put(
        "/api/widgets/settings/device_control",
        json={"sort_mode": "favorites"}
    )
    assert resp.status_code == 200

    widget = session.exec(
        select(UserWidget).where(
            UserWidget.username == "default",
            UserWidget.widget_key == "device_control"
        )
    ).first()
    assert widget is not None
    assert widget.sort_mode == "favorites"


def test_update_widget_settings_pinned_devices(client: TestClient, session: Session):
    resp = client.put(
        "/api/widgets/settings/device_control",
        json={"pinned_devices": ["light.living_room", "media_player.tv"]}
    )
    assert resp.status_code == 200

    widget = session.exec(
        select(UserWidget).where(
            UserWidget.username == "default",
            UserWidget.widget_key == "device_control"
        )
    ).first()
    assert widget is not None
    parsed = json.loads(widget.pinned_devices)
    assert parsed == ["light.living_room", "media_player.tv"]


def test_update_widget_settings_config(client: TestClient, session: Session):
    resp = client.put(
        "/api/widgets/settings/energy_insights",
        json={"config": {"chart_type": "bar", "range": "7d"}}
    )
    assert resp.status_code == 200

    widget = session.exec(
        select(UserWidget).where(
            UserWidget.username == "default",
            UserWidget.widget_key == "energy_insights"
        )
    ).first()
    assert widget is not None
    parsed = json.loads(widget.config)
    assert parsed == {"chart_type": "bar", "range": "7d"}


def test_update_widget_settings_empty_body(client: TestClient):
    resp = client.put(
        "/api/widgets/settings/energy_insights",
        json={}
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "SUCCESS"}


def test_quick_assistant_toggle_enabled(client: TestClient, session: Session):
    resp = client.put(
        "/api/widgets/settings/quick_assistant",
        json={"quick_assistant_enabled": True}
    )
    assert resp.status_code == 200

    widget = session.exec(
        select(UserWidget).where(
            UserWidget.username == "default",
            UserWidget.widget_key == "quick_assistant"
        )
    ).first()
    assert widget is not None
    assert widget.visibility == "visible"


def test_quick_assistant_toggle_disabled(client: TestClient, session: Session):
    # First enable
    resp = client.put(
        "/api/widgets/settings/quick_assistant",
        json={"quick_assistant_enabled": True}
    )
    assert resp.status_code == 200

    # Then disable
    resp = client.put(
        "/api/widgets/settings/quick_assistant",
        json={"quick_assistant_enabled": False}
    )
    assert resp.status_code == 200

    widget = session.exec(
        select(UserWidget).where(
            UserWidget.username == "default",
            UserWidget.widget_key == "quick_assistant"
        )
    ).first()
    assert widget is not None
    assert widget.visibility == "hidden"


def test_get_widget_settings_after_updates(client: TestClient, session: Session):
    # Update some settings
    client.put("/api/widgets/settings/energy_insights", json={"is_pinned": True, "size": "wide"})
    client.put("/api/widgets/settings/ambient_timer", json={"visibility": "hidden"})

    # Get settings again
    resp = client.get("/api/widgets/settings")
    assert resp.status_code == 200
    settings = resp.json()

    energy = next(w for w in settings["widgets"] if w["widget_key"] == "energy_insights")
    assert energy["is_pinned"] is True
    assert energy["size"] == "wide"

    timer = next(w for w in settings["widgets"] if w["widget_key"] == "ambient_timer")
    assert timer["visibility"] == "hidden"


def test_create_user_widget_on_first_update(client: TestClient, session: Session):
    # Update a widget that doesn't exist yet for this user
    resp = client.put(
        "/api/widgets/settings/chores_progress",
        json={"visibility": "visible"}
    )
    assert resp.status_code == 200

    widget = session.exec(
        select(UserWidget).where(
            UserWidget.username == "default",
            UserWidget.widget_key == "chores_progress"
        )
    ).first()
    assert widget is not None


def test_widget_settings_with_custom_order(client: TestClient, session: Session):
    client.put("/api/widgets/settings/device_control", json={"order_index": 0})
    client.put("/api/widgets/settings/energy_insights", json={"order_index": 1})

    resp = client.get("/api/widgets/settings")
    settings = resp.json()

    device_control = next(w for w in settings["widgets"] if w["widget_key"] == "device_control")
    energy = next(w for w in settings["widgets"] if w["widget_key"] == "energy_insights")
    assert device_control["order_index"] == 0
    assert energy["order_index"] == 1
