import pytest

from bot import state


def test_tripped_flag_starts_untripped(tmp_path):
    flag = state.TrippedFlag(path=str(tmp_path / "TRIPPED"))
    assert not flag.is_tripped()


def test_tripped_flag_trip_creates_file(tmp_path):
    path = tmp_path / "TRIPPED"
    flag = state.TrippedFlag(path=str(path))
    flag.trip(reason="test breach")
    assert flag.is_tripped()
    assert "test breach" in path.read_text()


def test_tripped_flag_requires_manual_delete(tmp_path):
    path = tmp_path / "TRIPPED"
    flag = state.TrippedFlag(path=str(path))
    flag.trip()
    assert flag.is_tripped()
    path.unlink()
    assert not flag.is_tripped()


def test_tripped_flag_trip_is_idempotent(tmp_path):
    path = tmp_path / "TRIPPED"
    flag = state.TrippedFlag(path=str(path))
    flag.trip(reason="first")
    flag.trip(reason="second")  # must not overwrite the original reason
    assert "first" in path.read_text()
    assert "second" not in path.read_text()


def test_reconcile_matching_positions_returns_broker():
    local = {"SPY": state.Position("SPY", 10, 1)}
    broker = {"SPY": state.Position("SPY", 10, 1)}
    assert state.reconcile(local, broker) == broker


def test_reconcile_mismatch_raises():
    local = {"SPY": state.Position("SPY", 10, 1)}
    broker = {"SPY": state.Position("SPY", 5, 1)}
    with pytest.raises(state.ReconciliationError):
        state.reconcile(local, broker)


def test_reconcile_symbol_only_on_one_side_raises():
    with pytest.raises(state.ReconciliationError):
        state.reconcile({}, {"SPY": state.Position("SPY", 10, 1)})


def test_save_and_load_local_state_roundtrip(tmp_path):
    path = str(tmp_path / "positions.json")
    positions = {"SPY": state.Position("SPY", 10, 1)}
    state.save_local_state(positions, path)
    assert state.load_local_state(path) == positions


def test_load_local_state_missing_file_returns_empty(tmp_path):
    assert state.load_local_state(str(tmp_path / "nope.json")) == {}
