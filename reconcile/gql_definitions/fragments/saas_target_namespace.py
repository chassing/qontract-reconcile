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

from reconcile.gql_definitions.fragments.oc_connection_cluster import (
    OcConnectionCluster,
)
from reconcile.gql_definitions.fragments.vault_secret import VaultSecret


class ConfiguredBaseModel(BaseModel):
    class Config:
        smart_union = True
        extra = Extra.forbid


class SaasSecretParametersV1(ConfiguredBaseModel):
    name: str = Field(..., alias="name")
    secret: VaultSecret = Field(..., alias="secret")


class EnvironmentV1(ConfiguredBaseModel):
    name: str = Field(..., alias="name")
    labels: Optional[Json] = Field(..., alias="labels")
    parameters: Optional[Json] = Field(..., alias="parameters")
    secret_parameters: Optional[list[SaasSecretParametersV1]] = Field(
        ..., alias="secretParameters"
    )


class AppV1_AppV1(ConfiguredBaseModel):
    name: str = Field(..., alias="name")


class AppV1(ConfiguredBaseModel):
    name: str = Field(..., alias="name")
    parent_app: Optional[AppV1_AppV1] = Field(..., alias="parentApp")
    labels: Optional[Json] = Field(..., alias="labels")


class NamespaceSkupperSiteConfigV1(ConfiguredBaseModel):
    delete: Optional[bool] = Field(..., alias="delete")


class SaasTargetNamespace(ConfiguredBaseModel):
    name: str = Field(..., alias="name")
    labels: Optional[Json] = Field(..., alias="labels")
    delete: Optional[bool] = Field(..., alias="delete")
    path: str = Field(..., alias="path")
    environment: EnvironmentV1 = Field(..., alias="environment")
    app: AppV1 = Field(..., alias="app")
    cluster: OcConnectionCluster = Field(..., alias="cluster")
    skupper_site: Optional[NamespaceSkupperSiteConfigV1] = Field(
        ..., alias="skupperSite"
    )
