from __future__ import annotations

import json
from pathlib import Path

import pytest

from mindcap import MindcapApplication
from mindcap.contracts import PrepareBookmarksCommand


@pytest.mark.parametrize("local_state", [[], None, "unavailable metadata"])
def test_nonobject_local_state_does_not_block_bookmark_capture(
    tmp_path: Path, local_state: object
) -> None:
    user_data = tmp_path / "Chrome User Data"
    profile = user_data / "Default"
    profile.mkdir(parents=True)
    (profile / "Bookmarks").write_text(
        json.dumps(
            {
                "version": 1,
                "roots": {
                    "bookmark_bar": {
                        "type": "folder",
                        "children": [
                            {
                                "type": "url",
                                "name": "Python documentation",
                                "url": "https://www.python.org/doc/",
                            }
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (user_data / "Local State").write_text(json.dumps(local_state), encoding="utf-8")
    originals = {
        path: path.read_bytes() for path in user_data.rglob("*") if path.is_file()
    }

    result = MindcapApplication.default().prepare_bookmarks(
        PrepareBookmarksCommand(
            user_data_dirs=[user_data], output_root=tmp_path / "review workspace"
        )
    )

    assert result.profile_count == result.bookmark_count == result.included_count == 1
    assert result.batch_count == 1
    assert result.first_batch is not None
    assert "<https://www.python.org/doc/>" in result.first_batch.read_text(
        encoding="utf-8"
    )
    normalized = json.loads(
        (result.source_bundle / "normalized" / "bookmarks.json").read_text(
            encoding="utf-8"
        )
    )
    assert normalized["profiles"][0]["profile_name"] is None
    assert {path: path.read_bytes() for path in originals} == originals
