"""audit CLI must never silently write nothing."""
import pytest

from india import audit


def test_zero_gauges_is_a_loud_error(tmp_path, capsys):
    g = tmp_path / "g.csv"
    g.write_text("site_no,lid,action,minor,moderate,major\n1,AAA,,,,\n")
    out = tmp_path / "o.csv"
    with pytest.raises(SystemExit) as e:
        audit.main(["--gauges", str(g), "--out", str(out), "--data-dir", str(tmp_path)])
    assert "0 gauges audited" in str(e.value) and not out.exists()
    assert "skip 1 AAA" in capsys.readouterr().out
