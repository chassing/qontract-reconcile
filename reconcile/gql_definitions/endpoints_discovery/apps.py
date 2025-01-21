"""
Generated by qenerate plugin=pydantic_v1. DO NOT MODIFY MANUALLY!
"""
from collections.abc import Callable  # noqa: F401 # pylint: disable=W0611
from datetime import datetime  # noqa: F401 # pylint: disable=W0611
from enum import Enum  # noqa: F401 # pylint: disable=W0611
from typing import (  # noqa: F401 # pylint: disable=W0611
    Any,
    Optional,
    Union,
)

from pydantic import (  # noqa: F401 # pylint: disable=W0611
    BaseModel,
    Extra,
    Field,
    Json,
)

from reconcile.gql_definitions.fragments.oc_connection_cluster import OcConnectionCluster


DEFINITION = """
fragment CommonJumphostFields on ClusterJumpHost_v1 {
  hostname
  knownHosts
  user
  port
  remotePort
  identity {
    ... VaultSecret
  }
}

fragment OcConnectionCluster on Cluster_v1 {
  name
  serverUrl
  internal
  insecureSkipTLSVerify
  jumpHost {
    ...CommonJumphostFields
  }
  automationToken {
    ...VaultSecret
  }
  clusterAdminAutomationToken {
    ...VaultSecret
  }
  disable {
    integrations
  }
}

fragment VaultSecret on VaultSecret_v1 {
    path
    field
    version
    format
}

query EndPointsDiscoveryApps {
  apps: apps_v1 {
    path
    name
    labels
    endPoints {
      name
      url
    }
    namespaces {
      name
      labels
      delete
      clusterAdmin
      cluster {
        ...OcConnectionCluster
      }
    }
  }
}
"""


class ConfiguredBaseModel(BaseModel):
    class Config:
        smart_union=True
        extra=Extra.forbid


class AppEndPointsV1(ConfiguredBaseModel):
    name: str = Field(..., alias="name")
    url: str = Field(..., alias="url")


class NamespaceV1(ConfiguredBaseModel):
    name: str = Field(..., alias="name")
    labels: Optional[Json] = Field(..., alias="labels")
    delete: Optional[bool] = Field(..., alias="delete")
    cluster_admin: Optional[bool] = Field(..., alias="clusterAdmin")
    cluster: OcConnectionCluster = Field(..., alias="cluster")


class AppV1(ConfiguredBaseModel):
    path: str = Field(..., alias="path")
    name: str = Field(..., alias="name")
    labels: Optional[Json] = Field(..., alias="labels")
    end_points: Optional[list[AppEndPointsV1]] = Field(..., alias="endPoints")
    namespaces: Optional[list[NamespaceV1]] = Field(..., alias="namespaces")


class EndPointsDiscoveryAppsQueryData(ConfiguredBaseModel):
    apps: Optional[list[AppV1]] = Field(..., alias="apps")


def query(query_func: Callable, **kwargs: Any) -> EndPointsDiscoveryAppsQueryData:
    """
    This is a convenience function which queries and parses the data into
    concrete types. It should be compatible with most GQL clients.
    You do not have to use it to consume the generated data classes.
    Alternatively, you can also mime and alternate the behavior
    of this function in the caller.

    Parameters:
        query_func (Callable): Function which queries your GQL Server
        kwargs: optional arguments that will be passed to the query function

    Returns:
        EndPointsDiscoveryAppsQueryData: queried data parsed into generated classes
    """
    raw_data: dict[Any, Any] = query_func(DEFINITION, **kwargs)
    return EndPointsDiscoveryAppsQueryData(**raw_data)
