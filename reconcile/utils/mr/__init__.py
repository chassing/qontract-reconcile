from reconcile.utils.mr.app_interface_reporter import CreateAppInterfaceReporter
from reconcile.utils.mr.aws_access import CreateDeleteAwsAccessKey
from reconcile.utils.mr.base import (
    MergeRequestBase,
    MergeRequestProcessingError,
)
from reconcile.utils.mr.clusters_updates import CreateClustersUpdates
from reconcile.utils.mr.notificator import CreateAppInterfaceNotificator
from reconcile.utils.mr.ocm_update_recommended_version import (
    CreateOCMUpdateRecommendedVersion,
)
from reconcile.utils.mr.ocm_upgrade_scheduler_org_updates import (
    CreateOCMUpgradeSchedulerOrgUpdates,
)
from reconcile.utils.mr.promote_qontract import (
    PromoteQontractReconcileCommercial,
    PromoteQontractSchemas,
)
from reconcile.utils.mr.user_maintenance import (
    CreateDeleteUserAppInterface,
    CreateDeleteUserInfra,
)

__all__ = [
    "CreateAppInterfaceNotificator",
    "CreateAppInterfaceReporter",
    "CreateClustersUpdates",
    "CreateDeleteAwsAccessKey",
    "CreateDeleteUserAppInterface",
    "CreateDeleteUserInfra",
    "CreateOCMUpdateRecommendedVersion",
    "CreateOCMUpgradeSchedulerOrgUpdates",
    "MergeRequestProcessingError",
    "PromoteQontractReconcileCommercial",
    "PromoteQontractSchemas",
    "UnknownMergeRequestTypeError",
    "init_from_sqs_message",
]


class UnknownMergeRequestTypeError(Exception):
    """
    Used when the message type from the SQS message is unknown
    """


def init_from_sqs_message(message) -> MergeRequestBase:
    # First, let's find the classes that are inheriting from
    # MergeRequestBase and create a map where the class.name is
    # the key and the class itself is the value.
    # Example:
    # {
    #     'create_app_interface_reporter_mr': CreateAppInterfaceReporter,
    #     'create_app_interface_notificator_mr': CreateAppInterfaceNotificator,
    #     ...
    # }
    types_map = {}
    for item in globals().values():
        if not isinstance(item, type):
            continue
        if not issubclass(item, MergeRequestBase):
            continue
        if not hasattr(item, "name"):
            continue
        types_map[item.name] = item

    # Now let's get the 'pr_type' value from the message
    # and fail early if that type is not on the map.
    msg_type = message.pop("pr_type")
    if msg_type not in types_map:
        raise UnknownMergeRequestTypeError(f"type {msg_type} no supported")

    # Finally, get the class mapped to the type
    # and create an instance with all the remaining
    # attributes from the message
    kls = types_map[msg_type]
    return kls(**message)
