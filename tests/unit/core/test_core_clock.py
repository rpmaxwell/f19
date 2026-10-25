from f19.core.clock import Clock, ManualClock, WallClock


def test_manual():
    c = ManualClock(5)
    assert isinstance(c, Clock) and c.now() == 5
    assert c.advance(2.5) == 7.5 and c.now() == 7.5
    c.set(1); assert c.now() == 1


def test_wall_ratio():
    t = [100.0]
    c = WallClock(ratio=2.5, t0=10, time_fn=lambda: t[0])
    assert isinstance(c, Clock) and c.now() == 10
    t[0] = 104; assert c.now() == 20
    c.ratio = 1.0                                 # continuous across a ratio change
    assert c.now() == 20 and c.ratio == 1.0
    t[0] = 106; assert c.now() == 22
