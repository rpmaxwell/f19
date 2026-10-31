import pytest

from f19.rules.loadout import CAMERA, EXTRA_FUEL, MAVERICK, choose_loadout


def test_both_destroy_is_one_maverick_bay_and_three_tanks():
    lo = choose_loadout(["destroy", "destroy"])
    assert lo.bays == (MAVERICK, EXTRA_FUEL, EXTRA_FUEL, EXTRA_FUEL) and lo.needs_mavericks


def test_both_photo_is_one_camera_bay_and_three_tanks():
    lo = choose_loadout(["photograph", "photograph"])
    assert lo.bays == (CAMERA, EXTRA_FUEL, EXTRA_FUEL, EXTRA_FUEL) and not lo.needs_mavericks


@pytest.mark.parametrize("tasks", [["photograph", "destroy"], ["supply drop", "destroy"], []])
def test_unsupported(tasks):
    with pytest.raises(ValueError): choose_loadout(tasks)
