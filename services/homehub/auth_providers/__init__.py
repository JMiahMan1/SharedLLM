"""Login providers: where the hub's users, their logins and their permissions come from.

``local`` (the hub's own users) is always there; ``jarvis_identity`` adds Jarvis's accounts. See
:mod:`.base` for the interface, :mod:`.manager` for the settings and the login routing, and :mod:`.sync` for
how a provider's users become hub users and persons.
"""
