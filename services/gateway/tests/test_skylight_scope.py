"""Tests for services/gateway/skylight_scope.py -- who may see whose chores."""

import pytest

from services.gateway.skylight_scope import ChoreScopeError, resolve_chore_scope


class TestDefaults:
    def test_admin_without_a_scope_keeps_seeing_the_whole_frame(self):
        # Today's behaviour: an admin's widget shows every family member.
        assert resolve_chore_scope(scope=None, caller_user="jeremiah", is_admin=True) == ""

    def test_admin_omitting_the_scope_param_also_sees_everyone(self):
        assert resolve_chore_scope(caller_user="jeremiah", is_admin=True) == ""

    @pytest.mark.parametrize("alias", ["", "all", "everyone", "family", "*", "ALL"])
    def test_explicit_whole_frame_aliases(self, alias):
        assert resolve_chore_scope(scope=alias, caller_user="jeremiah", is_admin=True) == ""

    def test_non_admin_without_a_scope_gets_their_own_chores(self):
        assert resolve_chore_scope(scope=None, caller_user="michele", is_admin=False) == "michele"

    def test_non_admin_asking_for_everyone_still_only_gets_their_own(self):
        assert resolve_chore_scope(scope="all", caller_user="michele", is_admin=False) == "michele"

    def test_whitespace_and_casing_are_ignored(self):
        assert resolve_chore_scope(scope="  ALL ", caller_user="jeremiah", is_admin=True) == ""
        assert resolve_chore_scope(scope="Me", caller_user="Jeremiah", is_admin=True) == "Jeremiah"

    def test_non_admin_asking_for_everyone_in_another_case_still_gets_their_own(self):
        assert resolve_chore_scope(scope="EVERYONE", caller_user="michele", is_admin=False) == "michele"


class TestSelf:
    def test_admin_can_ask_for_their_own_chores(self):
        assert resolve_chore_scope(scope="me", caller_user="jeremiah", is_admin=True) == "jeremiah"

    def test_non_admin_self_is_the_same_as_the_default(self):
        assert resolve_chore_scope(scope="me", caller_user="michele", is_admin=False) == "michele"

    def test_admin_naming_themselves_outright_works_too(self):
        # Names are normalized to lower case; the Skylight assignee match is
        # case-insensitive either way, so this is equivalent and deterministic.
        assert resolve_chore_scope(scope="Jeremiah", caller_user="jeremiah", is_admin=True) == "jeremiah"


class TestNamingSomeoneElse:
    def test_admin_may_ask_for_any_member_by_login_name(self):
        assert resolve_chore_scope(scope="michele", caller_user="jeremiah", is_admin=True) == "michele"

    def test_non_admin_asking_for_someone_else_is_refused(self):
        with pytest.raises(ChoreScopeError) as err:
            resolve_chore_scope(scope="jeremiah", caller_user="michele", is_admin=False)
        # The message has to say what went wrong: a silent downgrade would show
        # Michele her own chores while the caller believed she asked for others.
        assert "your own chores" in str(err.value)

    def test_non_admin_naming_themselves_in_another_case_is_allowed(self):
        assert resolve_chore_scope(scope="MICHELE", caller_user="michele", is_admin=False) == "michele"

    def test_admin_name_is_normalized_to_lower_case(self):
        assert resolve_chore_scope(scope="Jeremiah", caller_user="jeremiah", is_admin=True) == "jeremiah"

    def test_non_scope_value_that_is_not_a_string_is_treated_as_unset(self):
        # Query params always arrive as strings; a stray type must not 500.
        assert resolve_chore_scope(scope=object(), caller_user="jeremiah", is_admin=True) == ""


class TestMissingCaller:
    def test_admin_with_no_login_name_can_still_ask_for_the_whole_frame(self):
        assert resolve_chore_scope(scope=None, caller_user=None, is_admin=True) == ""

    def test_self_with_no_login_name_yields_no_filter_rather_than_a_wildcard(self):
        # '' is the whole-frame filter upstream, so a nameless caller asking for
        # "me" would see everyone's chores. That is wrong; make it a refusal.
        with pytest.raises(ChoreScopeError):
            resolve_chore_scope(scope="me", caller_user="", is_admin=True)