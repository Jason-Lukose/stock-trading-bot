from bot import killswitch


def test_is_halted_false_when_no_file(tmp_path):
    assert not killswitch.is_halted(repo_root=str(tmp_path))


def test_is_halted_true_when_file_present(tmp_path):
    (tmp_path / killswitch.DEFAULT_HALT_FILENAME).write_text("stop\n")
    assert killswitch.is_halted(repo_root=str(tmp_path))


def test_halt_file_path_uses_repo_root(tmp_path):
    path = killswitch.halt_file_path(repo_root=str(tmp_path))
    assert path == str(tmp_path / "HALT")
