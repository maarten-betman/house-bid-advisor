import pandas as pd
import pytest

from bidadvisor.storage.lake import Lake


def test_round_trip_and_layer_guard(tmp_path):
    lake = Lake(tmp_path)
    frame = pd.DataFrame({"a": [1, 2]})
    lake.write_table("silver", "t", frame)
    pd.testing.assert_frame_equal(lake.read_table("silver", "t"), frame)
    with pytest.raises(ValueError):
        lake.path("platinum", "t")
