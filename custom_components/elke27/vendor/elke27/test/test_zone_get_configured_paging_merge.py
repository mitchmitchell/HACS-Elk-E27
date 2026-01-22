from elke27_lib.dispatcher import PagedBlock
from elke27_lib.handlers.zone import make_zone_configured_merge
from elke27_lib.states import PanelState


def test_zone_get_configured_paging_merge_accumulates_blocks():
    state = PanelState()
    merge = make_zone_configured_merge(state)

    blocks = [
        PagedBlock(
            block_id=1, payload={"block_id": 1, "block_count": 4, "zones": list(range(1, 17))}
        ),
        PagedBlock(
            block_id=2, payload={"block_id": 2, "block_count": 4, "zones": list(range(17, 33))}
        ),
        PagedBlock(
            block_id=3, payload={"block_id": 3, "block_count": 4, "zones": list(range(33, 49))}
        ),
        PagedBlock(block_id=4, payload={"block_id": 4, "block_count": 4, "zones": [49, 50]}),
    ]

    merged = merge(blocks, 4)

    assert merged["zones"] == list(range(1, 51))
    assert all(zone_id not in merged["zones"] for zone_id in range(51, 65))


def test_zone_get_configured_paging_merge_out_of_order_and_duplicates():
    state = PanelState()
    merge = make_zone_configured_merge(state)

    blocks = [
        PagedBlock(
            block_id=3, payload={"block_id": 3, "block_count": 4, "zones": list(range(33, 49))}
        ),
        PagedBlock(
            block_id=1, payload={"block_id": 1, "block_count": 4, "zones": list(range(1, 17))}
        ),
        PagedBlock(
            block_id=2, payload={"block_id": 2, "block_count": 4, "zones": list(range(17, 33))}
        ),
        PagedBlock(
            block_id=2, payload={"block_id": 2, "block_count": 4, "zones": list(range(17, 33))}
        ),
        PagedBlock(block_id=4, payload={"block_id": 4, "block_count": 4, "zones": [49, 50]}),
    ]

    merged = merge(blocks, 4)

    assert merged["zones"] == list(range(1, 51))
