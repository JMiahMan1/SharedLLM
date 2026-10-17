# Roles and shared devices

Admin is all or nothing: an admin manages accounts, credentials, server
settings and every device. Roles give someone part of that, such as running
the house's devices, without the rest.

## Devices can be shared

A Home Assistant device can be assigned to several people. Assigning it to
one more person never takes it from anyone else, and removing someone leaves
the others. `DeviceAssignment` holds one row per (device, user).

- **Device control widget:** open a device; anyone who can assign devices
  sees "Who can use it", one checkbox per person.
- **Admin > Users & Devices > Device Assignments:** add a device for a person;
  each row is one person's use of one device, removed on its own.
- **Voice:** a device used by exactly one person (besides the household
  account) still tells Jarvis who is speaking. A shared device does not, so
  the request falls back as if no device were named.
- **Rediscovery:** Gateway's entity discovery grants the discovering user any
  entity they lack, and never undoes an admin's revoke.

## Permissions

A role is a set of these, each checked in code
(`services/identity/roles.py`):

| Permission | Lets someone | Enforced in |
|---|---|---|
| `devices.use_all` | control every device that is not locked | Identity's `all_unprotected`, read by `services/execution/entity_access.py` |
| `devices.use_locked` | control locked devices too | Identity lists every locked entity as permitted for them |
| `devices.assign` | choose who can use each device | `POST /api/users/devices`, `DELETE /api/devices/{id}`, revoke; they also see every assignment |
| `devices.lock` | lock and unlock devices and set who may use a locked one | `/api/entity-protection` |

Admins hold every permission without a role. A user can hold several roles
and gets everything they add up to.

## Default roles

Seeded on start when missing. They can be renamed and their permissions
changed, but not deleted.

| Role | Permissions |
|---|---|
| Home manager | all four: every device, who uses which, and locks. No accounts or server settings. |
| Family member | `devices.use_all` |
| Guest | none: only the devices assigned to them |

## Managing roles

Admin > Users > Roles lists every role with what it allows and who holds it.
New role, or tap one to change its name, description, permissions and members.
Only admins manage roles; changes are in the account audit log (`role.create`,
`role.update`, `role.delete`, `user.roles`).

| Endpoint | Who | What |
|---|---|---|
| `GET /api/users/roles` | admin | `{permissions: [{key, description}], roles: [...]}` |
| `POST /api/users/roles` | admin | `{name, description, permissions}` |
| `PATCH /api/users/roles/{id}` | admin | same body |
| `DELETE /api/users/roles/{id}` | admin | 409 for a built-in role |
| `PUT /api/users/{username}/roles` | admin | `{role_ids: [...]}` replaces the user's roles |
| `GET /api/users/me` | anyone | now includes `role_ids` and `permissions` |

## Limits

- Only device permissions exist so far. Other admin pages (users, network,
  services) stay admin-only.
- Entity Protection is managed from the Admin page, which only admins open, so
  someone with `devices.lock` can lock through the API but has no screen for
  it yet.
