import logging
import os
from collections.abc import Callable, Iterable, KeysView
from typing import Any

from github import Github
from github.GithubObject import NotSet  # type: ignore
from github.Organization import Organization
from github.Team import Team
from sretoolbox.utils import retry

from reconcile import (
    openshift_users,
    queries,
)
from reconcile.utils import (
    expiration,
    gql,
)
from reconcile.utils.aggregated_list import (
    AggregatedDiffRunner,
    AggregatedList,
)
from reconcile.utils.raw_github_api import RawGithubApi
from reconcile.utils.secret_reader import SecretReader

GH_BASE_URL = os.environ.get("GITHUB_API", "https://api.github.com")

ORGS_QUERY = """
{
  orgs: githuborg_v1 {
    name
    token {
      path
      field
      version
      format
    }
    default
    managedTeams
  }
}
"""

ROLES_QUERY = """
{
  roles: roles_v1 {
    name
    users {
      github_username
    }
    bots {
      github_username
    }
    permissions {
      service
      ...on PermissionGithubOrg_v1 {
        org
      }
      ...on PermissionGithubOrgTeam_v1 {
        org
        team
      }
    }
    expirationDate
  }
}
"""


CLUSTERS_QUERY = """
{
  clusters: clusters_v1 {
    name
    serverUrl
    auth {
      service
      ... on ClusterAuthGithubOrg_v1 {
        org
      }
      ... on ClusterAuthGithubOrgTeam_v1 {
        org
        team
      }
      # ... on ClusterAuthOIDC_v1 {
      # }
    }
    automationToken {
      path
      field
      version
      format
    }
  }
}
"""

QONTRACT_INTEGRATION = "github"


def get_orgs() -> list[dict[str, Any]]:
    gqlapi = gql.get_api()
    return gqlapi.query(ORGS_QUERY)["orgs"]


def get_config(default: bool = False) -> dict[str, Any]:
    orgs = get_orgs()
    secret_reader = SecretReader(queries.get_secret_reader_settings())
    config: dict[str, Any] = {"github": {}}
    found_defaults = []
    for org in orgs:
        org_name = org["name"]
        if org.get("default"):
            found_defaults.append(org_name)
        elif default:
            continue
        token = secret_reader.read(org["token"])
        org_config = {"token": token, "managed_teams": org["managedTeams"]}
        config["github"][org_name] = org_config

    if default:
        if len(found_defaults) == 0:
            raise KeyError("default github org config not found")
        if len(found_defaults) > 1:
            raise KeyError(
                f"multiple default github org configs found: {found_defaults}"
            )

    return config


def get_default_config() -> dict[str, Any]:
    github_config = get_config(default=True)
    return next(iter(github_config["github"].values()))


@retry()
def get_org_and_teams(
    github: Github, org_name: str
) -> tuple[Organization, Iterable[Team]]:
    org = github.get_organization(org_name)
    teams = org.get_teams()
    return org, teams


@retry()
def get_members(unit: Organization) -> list[str]:
    return [member.login for member in unit.get_members()]


class GHApiStore:
    _orgs: dict[str, Any] = {}

    def __init__(self, config: dict) -> None:
        for org_name, org_config in config["github"].items():
            token = org_config["token"]
            managed_teams = org_config.get("managed_teams", None)
            self._orgs[org_name] = (
                Github(token, base_url=GH_BASE_URL),
                RawGithubApi(token),
                managed_teams,
            )

    def orgs(self) -> KeysView[str]:
        return self._orgs.keys()

    def github(self, org_name: str) -> Github:
        return self._orgs[org_name][0]

    def raw_github_api(self, org_name: str) -> RawGithubApi:
        return self._orgs[org_name][1]

    def managed_teams(self, org_name: str) -> list[str] | None:
        return self._orgs[org_name][2]


def fetch_current_state(gh_api_store: GHApiStore) -> AggregatedList:
    state = AggregatedList()

    for org_name in gh_api_store.orgs():
        g = gh_api_store.github(org_name)
        raw_gh_api = gh_api_store.raw_github_api(org_name)
        managed_teams = gh_api_store.managed_teams(org_name)
        # if 'managedTeams' is not specified
        # we manage all teams
        is_managed = managed_teams is None or len(managed_teams) == 0

        org, teams = get_org_and_teams(g, org_name)

        org_members = None
        if is_managed:
            org_members = get_members(org)
            org_members.extend(raw_gh_api.org_invitations(org_name))
            org_members = [m.lower() for m in org_members]

        all_team_members = []
        for team in teams:
            if not is_managed and team.name not in (managed_teams or []):
                continue

            members = get_members(team)
            members.extend(raw_gh_api.team_invitations(org.id, team.id))
            members = [m.lower() for m in members]
            all_team_members.extend(members)

            state.add(
                {"service": "github-org-team", "org": org_name, "team": team.name},
                members,
            )
        all_team_members = list(set(all_team_members))

        members = org_members or all_team_members
        state.add(
            {
                "service": "github-org",
                "org": org_name,
            },
            members,
        )

    return state


def fetch_desired_state(infer_clusters: bool = True) -> AggregatedList:
    gqlapi = gql.get_api()
    state = AggregatedList()

    roles: list[dict[str, Any]] = expiration.filter(gqlapi.query(ROLES_QUERY)["roles"])
    for role in roles:
        permissions = list(
            filter(
                lambda p: p.get("service") in {"github-org", "github-org-team"},
                role["permissions"],
            )
        )

        if not permissions:
            continue

        user_members = [user["github_username"] for user in role["users"]]
        bot_members = [
            bot["github_username"] for bot in role["bots"] if "github_username" in bot
        ]
        members = [m.lower() for m in user_members + bot_members]

        for permission in permissions:
            if permission["service"] == "github-org":
                state.add(permission, members)
            elif permission["service"] == "github-org-team":
                state.add(permission, members)
                state.add(
                    {
                        "service": "github-org",
                        "org": permission["org"],
                    },
                    members,
                )

    if not infer_clusters:
        return state

    clusters = gqlapi.query(CLUSTERS_QUERY)["clusters"]
    openshift_users_desired_state = openshift_users.fetch_desired_state(
        oc_map=None, enforced_user_keys=["github_username"]
    )
    for cluster in clusters:
        for auth in cluster["auth"]:
            if auth["service"] not in {"github-org", "github-org-team"}:
                continue

            cluster_name = cluster["name"]
            members = [
                ou["user"].lower()
                for ou in openshift_users_desired_state
                if ou["cluster"] == cluster_name
            ]

            state.add(
                {
                    "service": "github-org",
                    "org": auth["org"],
                },
                members,
            )
            if auth["service"] == "github-org-team":
                state.add(
                    {
                        "service": "github-org-team",
                        "org": auth["org"],
                        "team": auth["team"],
                    },
                    members,
                )

    return state


class RunnerAction:
    def __init__(self, dry_run: bool, gh_api_store: GHApiStore) -> None:
        self.dry_run = dry_run
        self.gh_api_store = gh_api_store

    def add_to_team(self) -> Callable:
        label = "add_to_team"

        def action(params: dict, items: dict) -> None:
            org = params["org"]
            team = params["team"]

            if self.dry_run:
                for member in items:
                    logging.info([label, member, org, team])
            else:
                g = self.gh_api_store.github(org)
                gh_org = g.get_organization(org)
                teams = {team.name: team.id for team in gh_org.get_teams()}
                gh_team = gh_org.get_team(teams[team])

                for member in items:
                    logging.info([label, member, org, team])
                    gh_user = g.get_user(member)
                    gh_team.add_membership(gh_user, "member")

        return action

    def del_from_team(self) -> Callable:
        label = "del_from_team"

        def action(params: dict, items: dict) -> None:
            org = params["org"]
            team = params["team"]

            if self.dry_run:
                for member in items:
                    logging.info([label, member, org, team])
            else:
                g = self.gh_api_store.github(org)
                gh_org = g.get_organization(org)
                teams = {team.name: team.id for team in gh_org.get_teams()}
                gh_team = gh_org.get_team(teams[team])

                for member in items:
                    logging.info([label, member, org, team])
                    gh_user = g.get_user(member)
                    gh_team.remove_membership(gh_user)

                # members = gh_team.get_members()
                # if len(list(members)) == 0:
                #     logging.info(["del_team", org, team])
                #     gh_team.delete()

        return action

    def create_team(self) -> Callable:
        label = "create_team"

        def action(params: dict, items: dict) -> None:
            org = params["org"]
            team = params["team"]

            logging.info([label, org, team])

            if not self.dry_run:
                g = self.gh_api_store.github(org)
                gh_org = g.get_organization(org)

                repo_names = NotSet
                permission = NotSet
                privacy = "secret"

                gh_org.create_team(team, repo_names, permission, privacy)

        return action

    def add_to_org(self) -> Callable:
        label = "add_to_org"

        def action(params: dict, items: dict) -> None:
            org = params["org"]

            if self.dry_run:
                for member in items:
                    logging.info([label, member, org])
            else:
                g = self.gh_api_store.github(org)
                gh_org = g.get_organization(org)

                for member in items:
                    logging.info([label, member, org])
                    gh_user = g.get_user(member)
                    gh_org.add_to_members(gh_user, "member")

        return action

    def del_from_org(self) -> Callable:
        label = "del_from_org"

        def action(params: dict, items: dict) -> None:
            org = params["org"]

            if self.dry_run:
                for member in items:
                    logging.info([label, member, org])
            else:
                g = self.gh_api_store.github(org)
                gh_org = g.get_organization(org)

                for member in items:
                    logging.info([label, member, org])

                    if not self.dry_run:
                        gh_user = g.get_user(member)
                        gh_org.remove_from_membership(gh_user)

        return action

    @staticmethod
    def raise_exception(msg: str) -> Callable:
        def _raise_exception(params: dict, items: dict) -> None:
            raise Exception(msg)

        return _raise_exception


def service_is(service: str) -> Callable:
    return lambda params: params.get("service") == service


def run(dry_run: bool) -> None:
    config = get_config()
    gh_api_store = GHApiStore(config)

    current_state = fetch_current_state(gh_api_store)
    desired_state = fetch_desired_state()

    # Ensure current_state and desired_state match orgs
    current_orgs = {item["params"]["org"] for item in current_state.dump()}
    desired_orgs = {item["params"]["org"] for item in desired_state.dump()}

    assert current_orgs == desired_orgs, (
        f"Current orgs ({current_orgs}) don't match desired orgs ({desired_orgs})"
    )

    # Calculate diff
    diff = current_state.diff(desired_state)

    # Run actions
    runner_action = RunnerAction(dry_run, gh_api_store)
    runner = AggregatedDiffRunner(diff)

    # insert github-org
    runner.register(
        "insert",
        runner_action.raise_exception("Cannot create a Github Org"),
        service_is("github-org"),
    )

    # insert github-org-team
    runner.register(
        "insert",
        runner_action.create_team(),
        service_is("github-org-team"),
    )
    runner.register(
        "insert",
        runner_action.add_to_team(),
        service_is("github-org-team"),
    )

    # delete github-org
    runner.register(
        "delete",
        runner_action.raise_exception("Cannot delete a Github Org"),
        service_is("github-org"),
    )

    # delete github-org-team
    runner.register(
        "delete",
        runner_action.del_from_team(),
        service_is("github-org-team"),
    )

    # update-insert github-org
    runner.register(
        "update-insert",
        runner_action.add_to_org(),
        service_is("github-org"),
    )

    # update-insert github-org-team
    runner.register(
        "update-insert",
        runner_action.add_to_team(),
        service_is("github-org-team"),
    )

    # update-delete github-org
    runner.register(
        "update-delete",
        runner_action.del_from_org(),
        service_is("github-org"),
    )

    # update-delete github-org-team
    runner.register(
        "update-delete",
        runner_action.del_from_team(),
        service_is("github-org-team"),
    )

    runner.run()


def early_exit_desired_state(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return {
        "github_orgs": get_orgs(),
        "github_org_members": fetch_desired_state().dump(),
    }
