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


def test_frame_clock_wrap():
    from f19.core.clock import FrameClock
    c = FrameClock(); f, w = 65500, 0.0
    assert c.feed(f, 8, w) == 0.0 and c.ratio is None             # first feed: nothing to difference
    for _ in range(20):
        f = (f + 10) & 0xFFFF; w += 0.5; c.feed(f, 8, w)           # 10 frames x 2/8 = 2.5 game s per 0.5 s wall
    assert f < 65500 and abs(c.now() - 50.0) < 1e-9                # crossed the u16 wrap
    assert abs(c.ratio - 5.0) < 1e-9


def test_frame_clock_ratio_value_and_jump():
    from f19.core.clock import FrameClock
    c = FrameClock(); f, w = 100, 0.0
    c.feed(f, 4, w)
    for _ in range(10):
        f += 5; w += 1.0; c.feed(f, 4, w)                          # 5 frames x 2/4 = 2.5 game s per 1 s wall
    assert abs(c.ratio - 2.5) < 1e-9
    g = c.gt; c.feed(f + 30000, 4, w + 1.0)                        # a save-state load: re-base, no 30000-frame leap
    assert c.gt == g and c.ratio is None
