from __future__ import annotations

import copy
import logging
from typing import TYPE_CHECKING, Any

from reconcile import queries
from reconcile.jenkins_job_builder import init_jjb
from reconcile.utils.defer import defer
from reconcile.utils.gitlab_api import GitLabApi
from reconcile.utils.secret_reader import SecretReader

if TYPE_CHECKING:
    from collections.abc import Callable, MutableMapping


QONTRACT_INTEGRATION = "jenkins-webhooks"


def get_gitlab_api(secret_reader: SecretReader) -> GitLabApi:
    instance = queries.get_gitlab_instance()
    return GitLabApi(instance, secret_reader=secret_reader)


def get_hooks_to_add(
    desired_state: MutableMapping, gl: GitLabApi
) -> MutableMapping[str, list[dict[str, Any]]]:
    diff = copy.deepcopy(desired_state)
    for project_url, desired_hooks in diff.items():
        try:
            current_hooks = gl.get_project_hooks(project_url)
            for h in current_hooks:
                job_url = h.url
                trigger = []
                if h.merge_requests_events:
                    trigger.append("mr")
                if h.push_events:
                    trigger.append("push")
                if h.note_events:
                    trigger.append("note")
                item = {
                    "job_url": job_url.strip("/"),
                    "trigger": trigger,
                }
                if item in desired_hooks:
                    desired_hooks.remove(item)
        except Exception:
            logging.warning("no access to project: " + project_url)
            diff[project_url] = []

    return diff


@defer
def run(dry_run: bool, defer: Callable | None = None) -> None:
    secret_reader = SecretReader(queries.get_secret_reader_settings())
    jjb = init_jjb(secret_reader)
    gl = get_gitlab_api(secret_reader)
    if defer:
        defer(gl.cleanup)

    desired_state = jjb.get_job_webhooks_data()
    diff = get_hooks_to_add(desired_state, gl)

    for project_url, hooks in diff.items():
        for h in hooks:
            logging.info(["create_hook", project_url, h["trigger"], h["job_url"]])

            if not dry_run:
                gl.create_project_hook(project_url, h)


def early_exit_desired_state(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return {
        "jenkins_configs": queries.get_jenkins_configs(),
    }
