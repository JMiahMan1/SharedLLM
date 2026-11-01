"""The collected-data visibility rule: a minor's raw data is the parents',
and the father and the mother share theirs with each other."""
import pytest

from services.identity.main import may_view_comms, may_view_raw_data, may_view_technical_data
from services.identity.models import User


def _u(name, role="other", admin=False, mother=None):
    return User(username=name, family_role=role, is_admin=admin, mother_username=mother)


DAD = _u("dad", "father")
MOM = _u("mom", "mother")
OTHER_MOM = _u("aunt", "mother")
ADMIN = _u("admin", admin=True)
KID = _u("kid", "child", mother="mom")
TEEN = _u("teen", "teen")  # no linked mother
SIB = _u("sib", "child", mother="mom")
GROWN = _u("grown", "adult_child")


@pytest.mark.parametrize(
    ("viewer", "subject", "allowed"),
    [
        (DAD, KID, True),
        (MOM, KID, True),
        (ADMIN, KID, True),
        (OTHER_MOM, KID, False),  # not the linked mother
        (OTHER_MOM, TEEN, True),  # no mother linked: a mother counts
        (KID, KID, False),  # a minor does not browse their own stream
        (SIB, KID, False),  # siblings never see each other's
        (GROWN, KID, False),
        (DAD, GROWN, False),  # an adult's data is their own
        (GROWN, GROWN, True),
        (MOM, DAD, True),  # the parents share with each other
        (DAD, MOM, True),
        (MOM, GROWN, False),
        (DAD, DAD, True),
        (None, KID, False),
        (DAD, None, False),
    ],
)
def test_who_may_see_collected_data(viewer, subject, allowed):
    assert may_view_raw_data(viewer, subject) is allowed


@pytest.mark.parametrize(
    ("viewer", "subject", "allowed"),
    [
        (DAD, KID, True),
        (ADMIN, KID, True),
        (MOM, KID, False),  # every personal detail, but not the technical side
        (OTHER_MOM, TEEN, False),
        (SIB, KID, False),
        (KID, KID, True),  # their own phone's details are their own
        (DAD, GROWN, False),  # an adult's devices are not the father's
        (DAD, MOM, True),  # the father sees the mother's devices too
        (MOM, DAD, False),  # the mother sees his data, not its technical side
        (GROWN, GROWN, True),
        (None, KID, False),
    ],
)
def test_who_may_see_the_technical_side(viewer, subject, allowed):
    assert may_view_technical_data(viewer, subject) is allowed


# Calls, texts and message notifications: the parents (every admin is one).

@pytest.mark.parametrize("viewer, subject, expected", [
    (DAD, KID, True),
    (MOM, KID, True),
    (OTHER_MOM, KID, False),   # a mother not linked to this child
    (ADMIN, KID, True),        # every admin is a parent
    (KID, KID, False),         # a child does not see their own logs
    (SIB, KID, False),
    (GROWN, GROWN, True),      # a grown member's are their own
    (DAD, GROWN, False),
    (MOM, DAD, True),          # the father and the mother share theirs
    (DAD, MOM, True),
    (ADMIN, DAD, False),
])
def test_only_parents_see_calls_and_messages(viewer, subject, expected):
    assert may_view_comms(viewer, subject) is expected
