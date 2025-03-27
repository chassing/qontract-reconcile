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

from reconcile.gql_definitions.fragments.jumphost_common_fields import CommonJumphostFields
from reconcile.gql_definitions.fragments.vault_secret import VaultSecret


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

fragment VaultSecret on VaultSecret_v1 {
    path
    field
    version
    format
}

query Projects {
  glitchtip_projects: glitchtip_projects_v1 {
    name
    platform
    projectId
    eventThrottleRate
    teams {
      name
      roles {
        glitchtip_roles {
          organization {
            name
          }
          role
        }
        users {
          name
          org_username
        }
      }
      ldapGroups
      membersOrganizationRole
    }
    organization {
      name
      instance {
        name
      }
      # for glitchtip access revalidation
      owners
    }
    # for glitchtip-project-dsn
    namespaces {
      name
      delete
      clusterAdmin
      cluster {
        name
        serverUrl
        insecureSkipTLSVerify
        jumpHost {
          ...CommonJumphostFields
        }
        spec {
          private
        }
        automationToken {
          ...VaultSecret
        }
        clusterAdminAutomationToken {
          ...VaultSecret
        }
        internal
        disable {
          integrations
        }
      }
    }
    app {
    # for glitchtip access revalidation
      path
      # for the qontract-cli glitchtip subcommands
      escalationPolicy {
        channels {
          jiraBoard {
            name
          }
        }
      }
    }
  }
}
"""


class ConfiguredBaseModel(BaseModel):
    class Config:
        smart_union=True
        extra=Extra.forbid


class GlitchtipOrganizationV1(ConfiguredBaseModel):
    name: str = Field(..., alias="name")


class GlitchtipRoleV1(ConfiguredBaseModel):
    organization: GlitchtipOrganizationV1 = Field(..., alias="organization")
    role: str = Field(..., alias="role")


class UserV1(ConfiguredBaseModel):
    name: str = Field(..., alias="name")
    org_username: str = Field(..., alias="org_username")


class RoleV1(ConfiguredBaseModel):
    glitchtip_roles: Optional[list[GlitchtipRoleV1]] = Field(..., alias="glitchtip_roles")
    users: list[UserV1] = Field(..., alias="users")


class GlitchtipTeamV1(ConfiguredBaseModel):
    name: str = Field(..., alias="name")
    roles: list[RoleV1] = Field(..., alias="roles")
    ldap_groups: Optional[list[str]] = Field(..., alias="ldapGroups")
    members_organization_role: Optional[str] = Field(..., alias="membersOrganizationRole")


class GlitchtipInstanceV1(ConfiguredBaseModel):
    name: str = Field(..., alias="name")


class GlitchtipProjectV1_GlitchtipOrganizationV1(ConfiguredBaseModel):
    name: str = Field(..., alias="name")
    instance: GlitchtipInstanceV1 = Field(..., alias="instance")
    owners: Optional[list[str]] = Field(..., alias="owners")


class ClusterSpecV1(ConfiguredBaseModel):
    private: bool = Field(..., alias="private")


class DisableClusterAutomationsV1(ConfiguredBaseModel):
    integrations: Optional[list[str]] = Field(..., alias="integrations")


class ClusterV1(ConfiguredBaseModel):
    name: str = Field(..., alias="name")
    server_url: str = Field(..., alias="serverUrl")
    insecure_skip_tls_verify: Optional[bool] = Field(..., alias="insecureSkipTLSVerify")
    jump_host: Optional[CommonJumphostFields] = Field(..., alias="jumpHost")
    spec: Optional[ClusterSpecV1] = Field(..., alias="spec")
    automation_token: Optional[VaultSecret] = Field(..., alias="automationToken")
    cluster_admin_automation_token: Optional[VaultSecret] = Field(..., alias="clusterAdminAutomationToken")
    internal: Optional[bool] = Field(..., alias="internal")
    disable: Optional[DisableClusterAutomationsV1] = Field(..., alias="disable")


class NamespaceV1(ConfiguredBaseModel):
    name: str = Field(..., alias="name")
    delete: Optional[bool] = Field(..., alias="delete")
    cluster_admin: Optional[bool] = Field(..., alias="clusterAdmin")
    cluster: ClusterV1 = Field(..., alias="cluster")


class JiraBoardV1(ConfiguredBaseModel):
    name: str = Field(..., alias="name")


class AppEscalationPolicyChannelsV1(ConfiguredBaseModel):
    jira_board: list[JiraBoardV1] = Field(..., alias="jiraBoard")


class AppEscalationPolicyV1(ConfiguredBaseModel):
    channels: AppEscalationPolicyChannelsV1 = Field(..., alias="channels")


class AppV1(ConfiguredBaseModel):
    path: str = Field(..., alias="path")
    escalation_policy: AppEscalationPolicyV1 = Field(..., alias="escalationPolicy")


class GlitchtipProjectV1(ConfiguredBaseModel):
    name: str = Field(..., alias="name")
    platform: str = Field(..., alias="platform")
    project_id: Optional[str] = Field(..., alias="projectId")
    event_throttle_rate: Optional[int] = Field(..., alias="eventThrottleRate")
    teams: list[GlitchtipTeamV1] = Field(..., alias="teams")
    organization: GlitchtipProjectV1_GlitchtipOrganizationV1 = Field(..., alias="organization")
    namespaces: list[NamespaceV1] = Field(..., alias="namespaces")
    app: AppV1 = Field(..., alias="app")


class ProjectsQueryData(ConfiguredBaseModel):
    glitchtip_projects: Optional[list[GlitchtipProjectV1]] = Field(..., alias="glitchtip_projects")


def query(query_func: Callable, **kwargs: Any) -> ProjectsQueryData:
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
        ProjectsQueryData: queried data parsed into generated classes
    """
    raw_data: dict[Any, Any] = query_func(DEFINITION, **kwargs)
    return ProjectsQueryData(**raw_data)
