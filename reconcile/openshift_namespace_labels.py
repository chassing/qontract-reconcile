import logging
import sys
from collections.abc import (
    Callable,
    Generator,
    Sequence,
)
from threading import Lock
from typing import Any

from kubernetes.client.exceptions import ApiException
from sretoolbox.utils import threaded

import reconcile.openshift_base as ob
from reconcile.gql_definitions.common.namespaces import NamespaceV1
from reconcile.typed_queries.app_interface_vault_settings import (
    get_app_interface_vault_settings,
)
from reconcile.typed_queries.namespaces import get_namespaces
from reconcile.utils.constants import DEFAULT_THREAD_POOL_SIZE
from reconcile.utils.defer import defer
from reconcile.utils.oc import (
    StatusCodeError,
    validate_labels,
)
from reconcile.utils.oc_map import (
    OCLogMsg,
    OCMap,
    init_oc_map_from_namespaces,
)
from reconcile.utils.secret_reader import create_secret_reader
from reconcile.utils.sharding import is_in_shard
from reconcile.utils.state import (
    State,
    init_state,
)

_LOG = logging.getLogger(__name__)

QONTRACT_INTEGRATION = "openshift-namespace-labels"

DESIRED = "desired"
MANAGED = "managed"
CURRENT = "current"
CHANGED = "changed"
UPDATED_MANAGED = "updated-managed"

Labels = dict[str, str | None]
LabelKeys = list[str]
LabelsOrKeys = Labels | LabelKeys
Types = dict[str, LabelsOrKeys]

InternalLabelInventory = dict[str, dict[str, Types]]


class LabelInventory:
    """
    This inventory class will contain for each cluster / namespace:
    - DESIRED: the list of *desired* label key:value
    - CURRENT: the list of labels *current*ly set on the cluster/namespace
    - MANAGED: the list of label keys which are knowned to be *managed* by us
    Then the self.reconcile() function is called to evaluate
    - UPDATED_MANAGED: the new list of label keys to be stored in the managed
      state. If there is no change in the key list, this will be empty
    - CHANGED: the list of labels to be applied or removed.
      Label to be removed will have a value of None
    This inventory also holds a list of encountered errors for each
    cluster/namespace
    """

    def __init__(self) -> None:
        super().__init__()
        self._inv: InternalLabelInventory = {}
        self._errors: dict[str, dict[str, list[str]]] = {}
        self._lock = Lock()

    def errors(self, cluster: str, namespace: str) -> list[str]:
        """Get the registered errors for the given cluster / namespace.
        Defaults to []"""
        return self._errors.setdefault(cluster, {}).setdefault(namespace, [])

    def add_error(self, cluster: str, namespace: str, err: str) -> None:
        """Add an error to the given cluster / namespace"""
        self.errors(cluster, namespace).append(err)

    def has_any_error(self) -> bool:
        """Checks if any cluster / namespace has any error registered"""
        return any(e[2] for e in self.iter_errors())

    def iter_errors(self) -> Generator[tuple[str, str, list[str]], None, None]:
        """yields (cluster, namespace, errors) items"""
        for cluster, namespaces in self._errors.items():
            for namespace, errors in namespaces.items():
                if errors:
                    yield cluster, namespace, errors

    def _ns(self, cluster: str, namespace: str) -> Types:
        return self._inv.setdefault(cluster, {}).setdefault(namespace, {})

    def get(
        self,
        cluster: str,
        namespace: str,
        type: str,
        default: LabelsOrKeys | None = None,
    ) -> LabelsOrKeys | None:
        """Get the labels or keys for the given cluster / namespace / type"""
        return self._inv.get(cluster, {}).get(namespace, {}).get(type, default)

    def setdefault(
        self, cluster: str, namespace: str, type: str, default: LabelsOrKeys
    ) -> LabelsOrKeys:
        """Get the labels or keys for the given cluster / namespace / type,
        setting it to default if it does not exists"""
        with self._lock:
            return self._ns(cluster, namespace).setdefault(type, default)

    def set(self, cluster: str, namespace: str, type: str, labels: Labels) -> Labels:
        """Sets the given cluster / namespace / type to 'labels'"""
        with self._lock:
            self._ns(cluster, namespace)[type] = labels
            return labels

    def delete(self, cluster: str, namespace: str) -> None:
        """Delete the given cluster / namespace from the inventory"""
        with self._lock:
            self._inv.get(cluster, {}).pop(namespace, None)

    def __iter__(self) -> Generator[tuple[str, str, Types], None, None]:
        """Makes the inventory iterable by yielding (cluster, namespace, types)
        items. Types here is a Dict of {type: labelsOrKeys}"""
        for cluster, namespaces in self._inv.items():
            for namespace, types in namespaces.items():
                yield cluster, namespace, types

    def update_managed_keys(self, cluster: str, namespace: str, key: str) -> None:
        """
        Add or remove a key from the managed key list.
        This actually handles a copy of the managed keys dict and updates it.
        If the key was managed, it will get removed.
        If it was not, it will get added
        """
        managed = self.get(cluster, namespace, MANAGED, [])
        if managed is None:
            managed = []
        upd_managed = self.setdefault(
            cluster, namespace, UPDATED_MANAGED, managed.copy()
        )

        assert isinstance(upd_managed, list)  # we never get a Dict here
        if key in managed:
            upd_managed.remove(key)
        else:
            upd_managed.append(key)

    def reconcile(self) -> None:
        """
        Finds new/old/modify labels and sets them in in the inventory under the
        CHANGED key. The managed key store updates are recorded under the
        UPDATED_MANAGED type
        """
        for cluster, ns, types in self:
            if self.errors(cluster, ns):
                continue

            desired = types[DESIRED]
            if not isinstance(desired, dict):
                raise TypeError(
                    f"Expected dict for desired labels, got {type(desired)}"
                )
            managed = self.get(cluster, ns, MANAGED) or []
            current = self.get(cluster, ns, CURRENT, {})
            if not isinstance(current, dict):
                raise TypeError(
                    f"Expected dict for current labels, got {type(current)}"
                )
            changed = self.setdefault(cluster, ns, CHANGED, {})  # noqa: B909
            if not isinstance(changed, dict):
                raise TypeError(
                    f"Expected dict for changed labels, got {type(changed)}"
                )

            # cleanup managed items
            for k in managed:
                # remove old labels from managed once they have been removed on
                # the namespace
                if k not in desired and k not in current:
                    self.update_managed_keys(cluster, ns, k)

            for k, v in desired.items():
                if k not in current:  # new label
                    if k not in managed:
                        self.update_managed_keys(cluster, ns, k)
                    changed[k] = v

                elif k not in managed:  # conflicting labels
                    self.add_error(
                        cluster,
                        ns,
                        "Label conflict:"
                        + f"desired {k}={v} vs "
                        + f"current {k}={current[k]}",
                    )

                elif v != current[k]:
                    changed[k] = v

            # remove old labels
            for k in current:
                if k in managed and k not in desired:
                    changed[k] = None


def get_names_for_namespace(namespace: NamespaceV1) -> tuple[str, str]:
    """
    Get the cluster and namespace names from the provided
    namespace qontract info
    """
    return namespace.cluster.name, namespace.name


def get_gql_namespaces_in_shard() -> list[NamespaceV1]:
    """
    Get all namespaces from qontract-server and filter those which are in
    our shard
    """
    all_namespaces = get_namespaces()

    return [
        ns
        for ns in all_namespaces
        if not ob.is_namespace_deleted(ns.dict(by_alias=True))
        and is_in_shard(f"{ns.cluster.name}/{ns.name}")
    ]


def get_desired(
    inventory: LabelInventory, oc_map: OCMap, namespaces: Sequence[NamespaceV1]
) -> None:
    """
    Fill the provided label inventory with every desired info from the
    input namespaces. Ocm_map is used to not register clusters which are
    unreachable or not configured (due to --internal / --external)
    """
    to_be_ignored = []
    for ns in namespaces:
        if not ns.labels:
            continue

        cluster, ns_name = get_names_for_namespace(ns)
        # Skip unreachable / non-hanlded clusters
        # eg: internal settings may not match --internal / --external param
        if cluster not in oc_map.clusters():
            continue

        validation_errors = validate_labels(ns.labels)
        for err in validation_errors:
            inventory.add_error(cluster=cluster, namespace=ns_name, err=err)
        if inventory.errors(cluster=cluster, namespace=ns_name):
            continue

        if inventory.get(cluster=cluster, namespace=ns_name, type=DESIRED) is not None:
            # delete at the end of the loop to avoid having a reinsertion at
            # the third/fifth/.. occurrences
            to_be_ignored.append((cluster, ns_name))
            continue

        inventory.set(
            cluster=cluster, namespace=ns_name, type=DESIRED, labels=ns.labels
        )

    for cluster, ns_name in to_be_ignored:
        # Log only a warning here and do not report errors nor fail the
        # integration.
        # A dedicated integration or PR check will be done to ensure this
        # case does not occur (anymore)
        _LOG.debug(
            f"Found several namespace definitions for {cluster}/{ns_name}. Ignoring"
        )
        inventory.delete(cluster=cluster, namespace=ns_name)


def state_key(cluster: str, namespace: str) -> str:
    return f"{cluster}/{namespace}-managed-labels"


def get_managed(inventory: LabelInventory, state: State) -> None:
    """
    Fill the label inventory with the list of currently managed labels
    for each cluster & namespace. This information is retrieved from the state
    store provided in input
    """
    keys = state.ls()
    # We could run threaded here: probably faster but more parallel requests.
    for cluster, ns_name, types in inventory:
        if types.get(DESIRED) is None:
            continue
        # cluster, ns_name = get_names_for_namespace(namespace)
        key = state_key(cluster, ns_name)
        if f"/{key}" not in keys:
            continue
        managed = state.get(key, [])
        inventory.set(cluster=cluster, namespace=ns_name, type=MANAGED, labels=managed)


def lookup_namespaces(
    cluster: str, oc_map: OCMap
) -> tuple[str, list[dict[str, Any]] | None]:
    """
    Retrieve all namespaces from the given cluster
    """
    try:
        oc = oc_map.get(cluster)
        if isinstance(oc, OCLogMsg):
            # cluster is not reachable (may be used --internal / --external ?)
            _LOG.debug(f"Skipping not-handled cluster: {cluster}")
            logging.debug(msg=oc.message)
            return cluster, None
        _LOG.debug(f"Looking up namespaces on {cluster}")
        namespaces = oc.get_all("Namespace")
        if namespaces:
            return cluster, namespaces["items"]
    except StatusCodeError as e:
        msg = "cluster: {}, exception: {}"
        msg = msg.format(cluster, str(e))
        _LOG.error(msg)
    except ApiException as e:
        _LOG.error(
            f"Cluster {cluster} skipped: APIException [{e.status}:{e.reason}] {e.body}"
        )

    return cluster, None


def get_current(
    inventory: LabelInventory, oc_map: OCMap, thread_pool_size: int
) -> None:
    """
    Fill the provided label inventory with every current info from the
    reachable namespaces. Only namespaces already registered in the inventory
    will be updated. This avoids registering unhandled namespaces.
    """
    results = threaded.run(
        lookup_namespaces, oc_map.clusters(), thread_pool_size, oc_map=oc_map
    )

    for cluster, ns_list in results:
        if ns_list is None:
            continue
        for ns in ns_list:
            ns_meta = ns["metadata"]
            ns_name = ns_meta["name"]
            # ignore namespaces which are not in our desired list
            if inventory.get(cluster, ns_name, DESIRED) is None:
                continue
            labels = ns_meta.get("labels", {})
            inventory.set(
                cluster=cluster, namespace=ns_name, type=CURRENT, labels=labels
            )


def label(
    inv_item: tuple[str, str, Types],
    oc_map: OCMap,
    dry_run: bool,
    inventory: LabelInventory,
) -> None:
    cluster, namespace, types = inv_item
    if inventory.errors(cluster, namespace):
        return
    changed = types.get(CHANGED, {})
    if changed:
        prefix = "[dry-run] " if dry_run else ""
        _LOG.info(prefix + f"Updating labels on {cluster}/{namespace}: {changed}")
        if not dry_run:
            oc = oc_map.get(cluster)
            if isinstance(oc, OCLogMsg):
                logging.log(level=oc.log_level, msg=oc.message)
                return
            oc.label(None, "Namespace", namespace, changed, overwrite=True)


def realize(
    inventory: LabelInventory,
    state: State,
    oc_map: OCMap,
    dry_run: bool,
    thread_pool_size: int,
) -> None:
    """
    Apply the changes in the state store and on the namespaces
    """
    for cluster, namespace, types in inventory:
        if inventory.errors(cluster, namespace):
            continue
        upd_managed = types.get(UPDATED_MANAGED, [])
        if upd_managed:
            key = state_key(cluster, namespace)
            _LOG.debug(f"Updating state store: {key}: {upd_managed}")
            if not dry_run:
                state.add(key, upd_managed, force=True)

    # Potential exceptions will get raised up
    threaded.run(
        label,
        inventory,
        thread_pool_size,
        oc_map=oc_map,
        dry_run=dry_run,
        inventory=inventory,
    )


class NamespaceLabelError(Exception):
    pass


@defer
def run(
    dry_run: bool,
    thread_pool_size: int = DEFAULT_THREAD_POOL_SIZE,
    internal: bool | None = None,
    use_jump_host: bool = True,
    defer: Callable | None = None,
    raise_errors: bool = False,
) -> None:
    _LOG.debug("Collecting GQL data ...")
    namespaces = get_gql_namespaces_in_shard()

    inventory = LabelInventory()

    _LOG.debug("Initializing OC_Map ...")
    vault_settings = get_app_interface_vault_settings()
    secret_reader = create_secret_reader(use_vault=vault_settings.vault)
    oc_map = init_oc_map_from_namespaces(
        namespaces=namespaces,
        integration=QONTRACT_INTEGRATION,
        secret_reader=secret_reader,
        internal=internal,
        use_jump_host=use_jump_host,
        thread_pool_size=thread_pool_size,
        init_projects=True,
    )

    if defer:
        defer(oc_map.cleanup)

    _LOG.debug("Collecting desired state ...")
    get_desired(inventory, oc_map, namespaces)

    state = init_state(integration=QONTRACT_INTEGRATION)
    if defer:
        defer(state.cleanup)
    _LOG.debug("Collecting managed state ...")
    get_managed(inventory, state)

    _LOG.debug("Collecting current state ...")
    get_current(inventory, oc_map, thread_pool_size)

    inventory.reconcile()

    realize(inventory, state, oc_map, dry_run, thread_pool_size)

    if inventory.has_any_error():
        error_messages = []
        for cluster, namespace, errs in inventory.iter_errors():
            for err in errs:
                msg = f"{cluster}/{namespace}: {err}"
                _LOG.error(msg)
                error_messages.append(msg)
        if raise_errors:
            raise NamespaceLabelError("\n".join(error_messages))
        sys.exit(1)
