from collections.abc import (
    Callable,
    Iterable,
    Mapping,
)

from reconcile.saas_auto_promotions_manager.utils.saas_files_inventory import (
    SaasFilesInventory,
)
from reconcile.typed_queries.saas_files import SaasFile
from reconcile.utils.secret_reader import SecretReaderBase


def test_single_channel(
    saas_files_builder: Callable[[Iterable[Mapping]], list[SaasFile]],
    secret_reader: SecretReaderBase,
):
    saas_files = saas_files_builder([
        {
            "path": "/saas1.yml",
            "name": "saas_1",
            "resourceTemplates": [
                {
                    "name": "template_1",
                    "url": "repo1/url",
                    "targets": [
                        {
                            "ref": "main",
                            "namespace": {"path": "/namespace1.yml"},
                            "promotion": {
                                "publish": ["channel-a"],
                            },
                        }
                    ],
                }
            ],
        },
        {
            "path": "/saas2.yml",
            "name": "saas_2",
            "resourceTemplates": [
                {
                    "name": "template_2",
                    "url": "repo2/url",
                    "targets": [
                        {
                            "ref": "main",
                            "namespace": {"path": "/namespace2.yml"},
                            "promotion": {
                                "subscribe": ["channel-a"],
                                "auto": True,
                                "promotion_data": [
                                    {
                                        "channel": "channel-a",
                                        "data": [
                                            {
                                                "target_config_hash": "channel-a-hash",
                                                "parent_saas": "saas_1",
                                                "type": "type",
                                            }
                                        ],
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        },
    ])
    inventory = SaasFilesInventory(
        saas_files=saas_files, secret_reader=secret_reader, thread_pool_size=1
    )
    assert len(inventory.publishers) == 2
    assert len(inventory.publishers_with_subscribers) == 1
    assert len(inventory.subscribers) == 1
    assert not inventory.subscribers[0]._use_target_config_hash
