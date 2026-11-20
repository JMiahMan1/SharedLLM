# Network Device Tracking & Email Integration Design

## Overview

This document describes the implementation of:
1. **Per-account email integration** - Each user can configure their email for notifications
2. **Network device tracking** - Detect when devices join/leave the network
3. **Device naming/management** - Friendly names, user assignment, device types
4. **Automation triggers** - Actions when devices arrive/leave

---

## Current Architecture

### Network Monitoring (Existing)
- `services/control_plane/router_monitor.py` - Polls OpenWrt router every 60s
- Tracks: DHCP leases, Wi-Fi associations, MAC addresses, hostnames
- Redis keys: `network:status`, `network:state`, `network:usage:*`
- Alerts: Internet down, router restart, airtime hogs, DHCP pool full, Wi-Fi flaps

### Device Tracking (Existing)
- Home Assistant `device_tracker` entities (via HomeHub)
- Persons registry with `device_trackers` list
- Geo service for location history

### Notifications (Existing)
- Web Push (VAPID) via `services/telemetry/push.py`
- FCM for Android
- Push delivered via `telemetry.push.notify()`

---

## New Components Needed

### 1. Email Integration Service
**File:** `services/email/main.py`

Per-user email configuration with:
- SMTP credentials (encrypted in Identity)
- Test connection
- Templates for common notifications
- Per-user preferences (what to email vs push)

### 2. Device Registry Enhancement
**File:** `services/homehub/registries.py` (extend)

Add to person/device registry:
```python
{
  "mac": "aa:bb:cc:dd:ee:ff",
  "name": "John's iPhone 15",
  "user_id": "john",
  "device_type": "phone",  # phone, laptop, tablet, watch, iot, other
  "notify_on_arrival": true,
  "notify_on_departure": true,
  "email_on_arrival": false,
  "email_on_departure": true,
  "last_seen": "2026-11-18T10:30:00Z",
  "first_seen": "2026-10-01T12:00:00Z"
}
```

### 3. Network Device Tracker
**File:** `services/telemetry/device_tracker.py` (new)

Background service that:
- Subscribes to router monitor's device events (or polls router_monitor state)
- Detects: NEW_DEVICE, DEVICE_ARRIVED, DEVICE_LEFT, DEVICE_RENAMED
- Matches MAC to device registry
- Triggers automation webhooks
- Sends notifications (push/email)

### 4. Automation Trigger Extension
**File:** `services/automation/main.py` (extend)

New trigger types:
```json
{
  "type": "device_arrival",
  "device_mac": "aa:bb:cc:dd:ee:ff",
  "action": "turn_on_lights"
}
{
  "type": "device_departure",
  "device_user": "john",
  "action": "arm_security"
}
```

### 5. API Routes
**File:** `services/gateway/network_routes.py` (extend)

New endpoints:
- `GET /api/network/devices` - List all known devices with registry info
- `POST /api/network/devices/{mac}/name` - Rename device
- `POST /api/network/devices/{mac}/assign` - Assign to user
- `GET /api/network/devices/{mac}/history` - Arrival/departure log
- `POST /api/email/settings` - Configure email per user
- `POST /api/email/test` - Send test email

---

## Implementation Plan

### Phase 1: Device Registry (Week 1)
1. Extend `services/homehub/registries.py` with device tracking fields
2. Add API endpoints in `services/gateway/hub_registry_routes.py`
3. Update frontend (Device Management page)

### Phase 2: Network Device Tracker (Week 1-2)
1. Create `services/telemetry/device_tracker.py`
2. Subscribe to router_monitor events (Redis pub/sub or poll state)
2. Detect arrival/departure events
3. Match MAC to registry, trigger notifications

### Phase 3: Email Integration (Week 2)
1. Create `services/email/main.py`
2. Add SMTP config to Identity (encrypted)
3. Email templates (arrival, departure, security alerts)
4. Per-user notification preferences

### Phase 4: Automation Triggers (Week 2)
1. Extend automation service with device triggers
2. Add trigger evaluation in device tracker
3. Webhook dispatch for custom actions

### Phase 5: Testing & Polish (Week 2-3)
1. Unit tests for device tracker logic
2. Integration test with real router
3. Frontend device management UI

---

## Data Flow

```
Router (OpenWrt)
    │
    ▼
Router Monitor (control_plane/router_monitor.py) - polls every 60s
    │
    ▼
Redis: network:state (leases, hosts, radios.clients)
    │
    ▼
Device Tracker (telemetry/device_tracker.py) - monitors state changes
    │
    ├──▶ Device Registry (homehub/registries.py) - MAC → name, user, prefs
    │
    ├──▶ Notifications
    │     ├──▶ Push (telemetry/push.py)
    │     └──▶ Email (email/main.py)
    │
    └──▶ Automation (automation/main.py)
          ├──▶ device_arrival trigger
          └──▶ device_departure trigger
```

---

## Redis Keys (New)

| Key | Purpose | TTL |
|-----|---------|-----|
| `device:registry:{mac}` | Device info (name, user, type, prefs) | None |
| `device:history:{mac}` | Arrival/departure log (list) | 90 days |
| `device:presence:{mac}` | Current state (home/away) | None |
| `email:settings:{user_id}` | SMTP config, templates, prefs | None |
| `automation:device_triggers` | Registered device triggers | None |

---

## API Endpoints (New)

### Device Management
```
GET    /api/network/devices              # List all devices with registry info
GET    /api/network/devices/{mac}        # Device details + history
POST   /api/network/devices/{mac}/name   # Rename device
POST   /api/network/devices/{mac}/assign # Assign to user
POST   /api/network/devices/{mac}/prefs  # Notification preferences
GET    /api/network/devices/{mac}/history # Arrival/departure log
```

### Email Settings
```
GET    /api/email/settings               # Current email config
POST   /api/email/settings               # Save SMTP config (encrypted)
POST   /api/email/test                   # Send test email
```

### Automation (extend existing)
```
POST   /api/automation/triggers          # Add device_arrival/departure trigger
```

---

## Example Use Cases

1. **"John's iPhone arrived" → Push to family → Turn on entry lights**
2. **"Unknown device joined" → Email admin → Log for review**
3. **"Last person left" → Arm security → Turn off all lights**
4. **"Kid's tablet arrived after 10pm" → Email parents → Log screen time**

---

## Security Considerations

1. **MAC addresses** - Treat as PII, never expose to unauthorized users
2. **Email credentials** - Encrypt with Fernet in Identity (like HA tokens)
3. **Device naming** - Only admins can rename; users can only see their devices
4. **Automation** - Only admins can create triggers; scoped to user's devices

---

## Configuration

Environment variables:
```
# Email (optional, per-user override)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_TLS=true

# Device tracker
DEVICE_TRACKER_POLL_SECONDS=30
DEVICE_AWAY_THRESHOLD_SECONDS=300  # 5 min before "away"
```

---

## Future Extensions

1. **Bluetooth tracking** - ESPHome/iBeacon for room-level presence
2. **Bandwidth alerts** - Per-device quotas
3. **Parental controls** - Schedule internet access per device
4. **Guest network** - Auto-assign unknown devices to guest VLAN
5. **MDM integration** - Corporate device compliance
