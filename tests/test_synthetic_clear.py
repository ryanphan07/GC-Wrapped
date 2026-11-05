"""`make synth` empties its output directory first: stale older-generator exports can't survive."""

from pathlib import Path

import pytest

from synthetic.instagram import clear_output_dir, generate_pair


def test_clear_output_dir_removes_stale_exports_and_keeps_the_directory(tmp_path: Path) -> None:
    out = tmp_path / "landing"
    (out / "old_generator_export" / "deep").mkdir(parents=True)
    (out / "old_generator_export" / "deep" / "f.json").write_text("{}", encoding="utf-8")
    (out / "stray.txt").write_text("x", encoding="utf-8")
    (out / "link").symlink_to(tmp_path)  # a symlink is unlinked, never followed
    inode = out.stat().st_ino

    clear_output_dir(out, cwd=tmp_path / "elsewhere")

    assert list(out.iterdir()) == []
    assert out.stat().st_ino == inode  # same directory, so a bind mount on it keeps working
    assert tmp_path.exists()


def test_clear_then_generate_leaves_only_the_two_current_exports(tmp_path: Path) -> None:
    out = tmp_path / "landing"
    (out / "export_old").mkdir(parents=True)

    clear_output_dir(out, cwd=tmp_path / "elsewhere")
    generate_pair(out)

    assert sorted(p.name for p in out.iterdir()) == ["export_a", "export_b"]


def test_clear_output_dir_ignores_a_missing_directory(tmp_path: Path) -> None:
    clear_output_dir(tmp_path / "missing", cwd=tmp_path)


def test_clear_output_dir_refuses_dangerous_targets(tmp_path: Path) -> None:
    repo_like = tmp_path / "repo"
    (repo_like / ".git").mkdir(parents=True)
    (repo_like / "keep.txt").write_text("x", encoding="utf-8")
    child = tmp_path / "work" / "sub"
    child.mkdir(parents=True)

    for target, cwd in [
        (repo_like, tmp_path),  # a git checkout
        (tmp_path / "work", child),  # a parent of the working directory
        (child, child),  # the working directory itself
        (Path(tmp_path.anchor), tmp_path),  # the filesystem root
        (Path.home(), tmp_path),
    ]:
        with pytest.raises(SystemExit, match="refusing to clear"):
            clear_output_dir(target, cwd=cwd)

    assert (repo_like / "keep.txt").exists()
