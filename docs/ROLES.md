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
| `family.manage` | set family roles, mother/child links and household sharing | `/api/users/family`, `/api/users/{username}/family` |

Admins hold every permission without a role. A user can hold several roles
and gets everything they add up to.

## Family roles

Every account also carries one family role: `father`, `mother`, `child`,
`teen`, `adult_child` or `other`. Father and mother are relationships, not
permission grants by themselves; an admin father and a Home manager mother
get their powers from `is_admin` and `family.manage` respectively.

| Family role | What it adds |
|---|---|
| Father | nothing by itself; the account is normally also an admin |
| Mother | every device permission plus `family.manage` |
| Child / Teen | circle activity sharing stays on and cannot be turned off |
| Adult child | `devices.use_all`; may turn activity sharing off |
| Other | nothing; a guest or an account without a family role |

- A child or teen may be linked to one `mother` account. That link is what a
  parent uses to reach their health and activity sharing under the minor
  rules; it is set on the member, not by the child.
- Activity sharing for a minor is **circle-on and locked**: `sharing_locked`
  is returned with the sharing settings, and the API refuses to disable it.
  An adult child can opt out like any other adult.
- Cycle tracking stays private even inside the circle. A minor can share it
  only with the mother linked to their account.
- Manage relationships in **Admin > Users > Family** (admins) or in
  **Settings** for a Home manager / mother.

| Endpoint | Who | What |
|---|---|---|
| `GET /api/users/family` | admin or `family.manage` | family roster: username, display name, family role, linked mother, minor/sharing locks |
| `PATCH /api/users/{username}/family` | admin or `family.manage` | `{family_role, mother_username, clear_mother}`; a mother cannot edit parents/admins or promote to father/mother |

## Default roles

Seeded on start when missing. They can be renamed and their permissions
changed, but not deleted.

| Role | Permissions |
|---|---|
| Home manager | every device permission plus `family.manage`: who uses which device, locks, and family roles/links. No accounts or server settings. |
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

- Only device and family-management permissions exist so far. Other admin
  pages (users, network, services) stay admin-only. A Home manager manages
  family links from Settings, not from the Admin page, which only admins open.
- Entity Protection is managed from the Admin page, which only admins open, so
  someone with `devices.lock` can lock through the API but has no screen for
  it yet.
