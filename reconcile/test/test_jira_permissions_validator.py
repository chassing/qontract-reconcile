from collections.abc import Callable, Mapping
from typing import Any
from unittest.mock import Mock

import pytest
from jira import JIRAError
from pytest_mock import MockerFixture

from reconcile.gql_definitions.jira_permissions_validator.jira_boards_for_permissions_validator import (
    JiraBoardV1,
)
from reconcile.jira_permissions_validator import (
    ValidationError,
    board_is_valid,
    get_jira_boards,
    validate_boards,
)
from reconcile.test.fixtures import Fixtures
from reconcile.utils import metrics
from reconcile.utils.jira_client import (
    CustomFieldOption,
    FieldOption,
    IssueField,
    IssueType,
    JiraClient,
)


@pytest.fixture
def fx() -> Fixtures:
    return Fixtures("jira_permissions_validator")


@pytest.fixture
def raw_fixture_data(fx: Fixtures) -> dict[str, Any]:
    return fx.get_anymarkup("boards.yml")


@pytest.fixture
def query_func(
    data_factory: Callable[[type[JiraBoardV1], Mapping[str, Any]], Mapping[str, Any]],
    raw_fixture_data: dict[str, Any],
) -> Callable:
    return lambda *args, **kwargs: {
        "jira_boards": [
            data_factory(JiraBoardV1, item) for item in raw_fixture_data["jira_boards"]
        ]
    }


@pytest.fixture
def boards(query_func: Callable) -> list[JiraBoardV1]:
    return get_jira_boards(query_func)


def test_jira_permissions_validator_get_jira_boards(
    query_func: Callable, gql_class_factory: Callable
) -> None:
    default = {
        "name": "jira-board-default",
        "server": {
            "serverUrl": "https://jira-server.com",
            "token": {"path": "vault/path/token", "field": "token"},
        },
        "issueResolveState": "Closed",
        "severityPriorityMappings": {
            "name": "major-major",
            "mappings": [
                {"priority": "Minor"},
                {"priority": "Major"},
                {"priority": "Critical"},
            ],
        },
        "escalationPolicies": [
            {
                "name": "escalation-1",
                "channels": {"jiraComponents": None},
            },
            {
                "name": "escalation-2",
                "channels": {"jiraComponents": None},
            },
        ],
    }
    custom = {
        "name": "jira-board-custom",
        "server": {
            "serverUrl": "https://jira-server.com",
            "token": {"path": "vault/path/token", "field": "token"},
        },
        "issueType": "bug",
        "issueResolveState": "Closed",
        "issueReopenState": "Open",
        "issueFields": [{"name": "Security Level", "value": "fake"}],
        "severityPriorityMappings": {
            "name": "major-major",
            "mappings": [
                {"priority": "Minor"},
                {"priority": "Major"},
                {"priority": "Major"},
                {"priority": "Critical"},
            ],
        },
        "escalationPolicies": [
            {
                "name": "escalation-1",
                "channels": {"jiraComponents": ["component-1", "component-2"]},
            },
            {
                "name": "escalation-2",
                "channels": {"jiraComponents": None},
            },
        ],
    }
    assert get_jira_boards(query_func) == [
        gql_class_factory(JiraBoardV1, default),
        gql_class_factory(JiraBoardV1, custom),
    ]


@pytest.mark.parametrize(
    "board_is_valid, dry_run, error_returned, metric_set",
    [
        (0, True, False, False),
        (ValidationError.CANT_CREATE_ISSUE, True, True, False),
        (ValidationError.CANT_TRANSITION_ISSUES, True, True, False),
        (ValidationError.INVALID_ISSUE_TYPE, True, True, False),
        (ValidationError.INVALID_ISSUE_STATE, True, True, False),
        (ValidationError.INVALID_ISSUE_FIELD, True, True, False),
        (ValidationError.INVALID_PRIORITY, True, True, False),
        (ValidationError.PUBLIC_PROJECT_NO_SECURITY_LEVEL, True, True, False),
        (ValidationError.INVALID_COMPONENT, True, True, False),
        (ValidationError.PERMISSION_ERROR, True, True, True),
        (ValidationError.PROJECT_ARCHIVED, True, True, False),
        # no dry-run
        (ValidationError.CANT_CREATE_ISSUE, False, False, False),
        (ValidationError.PERMISSION_ERROR, False, False, True),
        (ValidationError.PROJECT_ARCHIVED, False, False, False),
        # test with another error
        (
            ValidationError.INVALID_PRIORITY | ValidationError.PERMISSION_ERROR,
            True,
            True,
            False,
        ),
        (
            ValidationError.INVALID_PRIORITY | ValidationError.PERMISSION_ERROR,
            False,
            True,
            False,
        ),
    ],
)
def test_jira_permissions_validator_validate_boards(
    mocker: MockerFixture,
    boards: list[JiraBoardV1],
    secret_reader: Mock,
    s3_state_builder: Callable,
    board_is_valid: ValidationError,
    dry_run: bool,
    error_returned: bool,
    metric_set: bool,
) -> None:
    board_is_valid_mock = mocker.patch(
        "reconcile.jira_permissions_validator.board_is_valid"
    )
    board_is_valid_mock.return_value = (board_is_valid, {})
    metrics_container_mock = mocker.create_autospec(spec=metrics.MetricsContainer)
    jira_client_class = mocker.create_autospec(spec=JiraClient)
    state = s3_state_builder({})
    assert (
        validate_boards(
            metrics_container=metrics_container_mock,
            secret_reader=secret_reader,
            jira_client_settings=None,
            jira_boards=boards,
            default_issue_type="task",
            default_reopen_state="new",
            board_check_interval_sec=60,
            dry_run=dry_run,
            state=state,
            jira_client_class=jira_client_class,
        )
        == error_returned
    )
    if metric_set:
        metrics_container_mock.set_gauge.assert_called()
    else:
        metrics_container_mock.set_gauge.assert_not_called()


def test_jira_permissions_validator_board_is_valid_happy_path(
    mocker: MockerFixture, gql_class_factory: Callable
) -> None:
    board = gql_class_factory(
        JiraBoardV1,
        {
            "name": "jira-board-default",
            "server": {
                "serverUrl": "https://jira-server.com",
                "token": {"path": "vault/path/token", "field": "token"},
            },
            "issueType": "bug",
            "issueResolveState": "Closed",
            "issueReopenState": "Open",
            "issueFields": [
                {"name": "Security Level", "value": "foo"},
                {"name": "Another Field", "value": "bar"},
            ],
            "severityPriorityMappings": {
                "name": "major-major",
                "mappings": [
                    {"priority": "Minor"},
                    {"priority": "Major"},
                    {"priority": "Critical"},
                ],
            },
            "escalationPolicies": [
                {
                    "name": "acs-fleet-manager-escalation",
                    "channels": {"jiraComponents": ["component1"]},
                },
            ],
        },
    )
    jira_client = mocker.create_autospec(spec=JiraClient)
    jira_client.is_archived = False
    jira_client.can_create_issues.return_value = True
    jira_client.can_transition_issues.return_value = True
    jira_client.get_issue_type.return_value = IssueType(
        id="2", name="bug", statuses=["open", "closed"]
    )
    jira_client.project_issue_field.side_effect = [
        IssueField(
            name="Security Level",
            id="security",
            options=[FieldOption(name="foo"), FieldOption(name="foo2")],
        ),
        IssueField(
            name="Another Field",
            id="field",
            options=[CustomFieldOption(value="bar"), FieldOption(name="bar2")],
        ),
    ]
    jira_client.project_priority_scheme.return_value = ["1", "2", "3"]
    jira_client.components.return_value = ["component1", "component2"]
    assert board_is_valid(
        jira=jira_client,
        board=board,
        default_issue_type="task",
        default_reopen_state="new",
        jira_server_priorities={"Minor": "1", "Major": "2", "Critical": "3"},
        public_projects=[],
    ) == (ValidationError(0), {"security": {"name": "foo"}, "field": {"value": "bar"}})


def test_jira_permissions_validator_board_is_valid_all_errors(
    mocker: MockerFixture, gql_class_factory: Callable
) -> None:
    board = gql_class_factory(
        JiraBoardV1,
        {
            "name": "jira-board-default",
            "server": {
                "serverUrl": "https://jira-server.com",
                "token": {"path": "vault/path/token", "field": "token"},
            },
            "issueType": "bug",
            "issueResolveState": "Closed",
            "issueReopenState": "Open",
            "issueFields": [{"name": "unknown-field", "value": "unknown"}],
            "severityPriorityMappings": {
                "name": "major-major",
                "mappings": [
                    {"priority": "Minor"},
                    {"priority": "Major"},
                    {"priority": "Critical"},
                ],
            },
            "escalationPolicies": [
                {
                    "name": "acs-fleet-manager-escalation",
                    "channels": {"jiraComponents": ["bad-component"]},
                },
            ],
        },
    )
    jira_client = mocker.create_autospec(spec=JiraClient)
    jira_client.is_archived = False
    jira_client.can_create_issues.return_value = False
    jira_client.can_transition_issues.return_value = False
    jira_client.get_issue_type.return_value = None
    jira_client.project_issue_types = Mock()
    jira_client.project_issue_types.return_value = []
    jira_client.project_priority_scheme.return_value = ["1", "2"]
    jira_client.components.return_value = ["component1", "component2"]
    assert board_is_valid(
        jira=jira_client,
        board=board,
        default_issue_type="task",
        default_reopen_state="new",
        jira_server_priorities={"Minor": "1", "Major": "2", "Critical": "3"},
        public_projects=[],
    ) == (
        ValidationError.CANT_CREATE_ISSUE
        | ValidationError.CANT_TRANSITION_ISSUES
        | ValidationError.INVALID_ISSUE_TYPE
        | ValidationError.INVALID_PRIORITY
        | ValidationError.INVALID_COMPONENT,
        {},
    )


def test_jira_permissions_validator_board_is_valid_bad_issue_field_name(
    mocker: MockerFixture, gql_class_factory: Callable
) -> None:
    board = gql_class_factory(
        JiraBoardV1,
        {
            "name": "jira-board-default",
            "server": {
                "serverUrl": "https://jira-server.com",
                "token": {"path": "vault/path/token", "field": "token"},
            },
            "issueType": "bug",
            "issueResolveState": "Closed",
            "issueReopenState": "Open",
            "issueFields": [{"name": "unknown-field", "value": "fake"}],
            "severityPriorityMappings": {
                "name": "major-major",
                "mappings": [
                    {"priority": "Minor"},
                    {"priority": "Major"},
                    {"priority": "Critical"},
                ],
            },
        },
    )
    jira_client = mocker.create_autospec(spec=JiraClient)
    jira_client.is_archived = False
    jira_client.can_create_issues.return_value = True
    jira_client.can_transition_issues.return_value = True
    jira_client.get_issue_type.return_value = IssueType(
        id="2", name="bug", statuses=["open", "closed"]
    )
    jira_client.project_issue_field.return_value = None
    jira_client.project_priority_scheme.return_value = ["1", "2", "3"]
    assert board_is_valid(
        jira=jira_client,
        board=board,
        default_issue_type="task",
        default_reopen_state="new",
        jira_server_priorities={"Minor": "1", "Major": "2", "Critical": "3"},
        public_projects=[],
    ) == (ValidationError.INVALID_ISSUE_FIELD, {})


def test_jira_permissions_validator_board_is_valid_bad_issue_field_value(
    mocker: MockerFixture, gql_class_factory: Callable
) -> None:
    board = gql_class_factory(
        JiraBoardV1,
        {
            "name": "jira-board-default",
            "server": {
                "serverUrl": "https://jira-server.com",
                "token": {"path": "vault/path/token", "field": "token"},
            },
            "issueType": "bug",
            "issueResolveState": "Closed",
            "issueReopenState": "Open",
            "issueFields": [{"name": "Security Level", "value": "unknown"}],
            "severityPriorityMappings": {
                "name": "major-major",
                "mappings": [
                    {"priority": "Minor"},
                    {"priority": "Major"},
                    {"priority": "Critical"},
                ],
            },
        },
    )
    jira_client = mocker.create_autospec(spec=JiraClient)
    jira_client.is_archived = False
    jira_client.can_create_issues.return_value = True
    jira_client.can_transition_issues.return_value = True
    jira_client.get_issue_type.return_value = IssueType(
        id="2", name="bug", statuses=["open", "closed"]
    )
    jira_client.project_issue_field.return_value = IssueField(
        name="Security Level",
        id="security",
        options=[FieldOption(name="fake"), FieldOption(name="fake2")],
    )
    jira_client.project_priority_scheme.return_value = ["1", "2", "3"]
    assert board_is_valid(
        jira=jira_client,
        board=board,
        default_issue_type="task",
        default_reopen_state="new",
        jira_server_priorities={"Minor": "1", "Major": "2", "Critical": "3"},
        public_projects=[],
    ) == (ValidationError.INVALID_ISSUE_FIELD, {})


def test_jira_permissions_validator_board_is_valid_bad_issue_status(
    mocker: MockerFixture, gql_class_factory: Callable
) -> None:
    board = gql_class_factory(
        JiraBoardV1,
        {
            "name": "jira-board-default",
            "server": {
                "serverUrl": "https://jira-server.com",
                "token": {"path": "vault/path/token", "field": "token"},
            },
            "issueType": "bug",
            "issueResolveState": "Closed",
            "issueReopenState": "BadState",
            "issueFields": None,
            "severityPriorityMappings": {
                "name": "major-major",
                "mappings": [
                    {"priority": "Minor"},
                    {"priority": "Major"},
                    {"priority": "Critical"},
                ],
            },
        },
    )
    jira_client = mocker.create_autospec(spec=JiraClient)
    jira_client.is_archived = False
    jira_client.can_create_issues.return_value = True
    jira_client.can_transition_issues.return_value = True
    jira_client.get_issue_type.return_value = IssueType(
        id="2", name="bug", statuses=["open", "closed"]
    )
    jira_client._project_issue_fields.return_value = []
    jira_client.project_priority_scheme.return_value = ["1", "2", "3"]
    assert board_is_valid(
        jira=jira_client,
        board=board,
        default_issue_type="task",
        default_reopen_state="new",
        jira_server_priorities={"Minor": "1", "Major": "2", "Critical": "3"},
        public_projects=[],
    ) == (ValidationError.INVALID_ISSUE_STATE, {})


def test_jira_permissions_validator_board_is_valid_bad_component(
    mocker: MockerFixture, gql_class_factory: Callable
) -> None:
    board = gql_class_factory(
        JiraBoardV1,
        {
            "name": "jira-board-default",
            "server": {
                "serverUrl": "https://jira-server.com",
                "token": {"path": "vault/path/token", "field": "token"},
            },
            "issueType": "bug",
            "issueResolveState": "Closed",
            "issueReopenState": "Open",
            "issueFields": None,
            "severityPriorityMappings": {
                "name": "major-major",
                "mappings": [
                    {"priority": "Minor"},
                    {"priority": "Major"},
                    {"priority": "Critical"},
                ],
            },
            "escalationPolicies": [
                {
                    "name": "acs-fleet-manager-escalation",
                    "channels": {"jiraComponents": ["bad-component"]},
                },
            ],
        },
    )
    jira_client = mocker.create_autospec(spec=JiraClient)
    jira_client.is_archived = False
    jira_client.can_create_issues.return_value = True
    jira_client.can_transition_issues.return_value = True
    jira_client.get_issue_type.return_value = IssueType(
        id="2", name="bug", statuses=["open", "closed"]
    )
    jira_client._project_issue_fields.return_value = []
    jira_client.project_priority_scheme.return_value = ["1", "2", "3"]
    jira_client.components.return_value = ["component1", "component2"]
    assert board_is_valid(
        jira=jira_client,
        board=board,
        default_issue_type="task",
        default_reopen_state="new",
        jira_server_priorities={"Minor": "1", "Major": "2", "Critical": "3"},
        public_projects=[],
    ) == (ValidationError.INVALID_COMPONENT, {})


def test_jira_permissions_validator_board_is_valid_public_project(
    mocker: MockerFixture, gql_class_factory: Callable
) -> None:
    board = gql_class_factory(
        JiraBoardV1,
        {
            "name": "jira-board-default",
            "server": {
                "serverUrl": "https://jira-server.com",
                "token": {"path": "vault/path/token", "field": "token"},
            },
            "issueType": "bug",
            "issueResolveState": "Closed",
            "issueReopenState": "Open",
            "issueFields": None,
            "severityPriorityMappings": {
                "name": "major-major",
                "mappings": [
                    {"priority": "Minor"},
                    {"priority": "Major"},
                    {"priority": "Critical"},
                ],
            },
        },
    )
    jira_client = mocker.create_autospec(spec=JiraClient)
    jira_client.is_archived = False
    jira_client.can_create_issues.return_value = True
    jira_client.can_transition_issues.return_value = True
    jira_client.get_issue_type.return_value = IssueType(
        id="2", name="bug", statuses=["open", "closed"]
    )
    jira_client._project_issue_fields.return_value = []
    jira_client.project_priority_scheme.return_value = ["1", "2", "3"]
    assert board_is_valid(
        jira=jira_client,
        board=board,
        default_issue_type="task",
        default_reopen_state="new",
        jira_server_priorities={"Minor": "1", "Major": "2", "Critical": "3"},
        public_projects=["jira-board-default"],
    ) == (ValidationError.PUBLIC_PROJECT_NO_SECURITY_LEVEL, {})


def test_jira_permissions_validator_board_is_valid_permission_error(
    mocker: MockerFixture, gql_class_factory: Callable
) -> None:
    board = gql_class_factory(
        JiraBoardV1,
        {
            "name": "jira-board-default",
            "server": {
                "serverUrl": "https://jira-server.com",
                "token": {"path": "vault/path/token", "field": "token"},
            },
            "issueType": "bug",
            "issueResolveState": "Closed",
            "issueReopenState": "Open",
            "issueFields": None,
            "severityPriorityMappings": {
                "name": "major-major",
                "mappings": [
                    {"priority": "Minor"},
                    {"priority": "Major"},
                    {"priority": "Critical"},
                ],
            },
        },
    )
    jira_client = mocker.create_autospec(spec=JiraClient)
    jira_client.is_archived = False
    jira_client.can_create_issues.side_effect = JIRAError(status_code=403)
    assert board_is_valid(
        jira=jira_client,
        board=board,
        default_issue_type="task",
        default_reopen_state="new",
        jira_server_priorities={"Minor": "1", "Major": "2", "Critical": "3"},
        public_projects=[],
    ) == (ValidationError.PERMISSION_ERROR, {})


def test_jira_permissions_validator_board_is_valid_exception(
    mocker: MockerFixture, gql_class_factory: Callable
) -> None:
    board = gql_class_factory(
        JiraBoardV1,
        {
            "name": "jira-board-default",
            "server": {
                "serverUrl": "https://jira-server.com",
                "token": {"path": "vault/path/token", "field": "token"},
            },
            "issueType": "bug",
            "issueResolveState": "Closed",
            "issueReopenState": "Open",
            "issueFields": None,
            "severityPriorityMappings": {
                "name": "major-major",
                "mappings": [
                    {"priority": "Minor"},
                    {"priority": "Major"},
                    {"priority": "Critical"},
                ],
            },
        },
    )
    jira_client = mocker.create_autospec(spec=JiraClient)
    jira_client.is_archived = False
    jira_client.can_create_issues.side_effect = JIRAError(status_code=500)
    with pytest.raises(JIRAError):
        board_is_valid(
            jira=jira_client,
            board=board,
            default_issue_type="task",
            default_reopen_state="new",
            jira_server_priorities={"Minor": "1", "Major": "2", "Critical": "3"},
            public_projects=[],
        )


def test_jira_permissions_validator_board_is_valid_exception_401(
    mocker: MockerFixture, gql_class_factory: Callable
) -> None:
    board = gql_class_factory(
        JiraBoardV1,
        {
            "name": "jira-board-default",
            "server": {
                "serverUrl": "https://jira-server.com",
                "token": {"path": "vault/path/token", "field": "token"},
            },
            "issueType": "bug",
            "issueResolveState": "Closed",
            "issueReopenState": "Open",
            "issueFields": None,
            "severityPriorityMappings": {
                "name": "major-major",
                "mappings": [
                    {"priority": "Minor"},
                    {"priority": "Major"},
                    {"priority": "Critical"},
                ],
            },
        },
    )
    jira_client = mocker.create_autospec(spec=JiraClient)
    jira_client.is_archived = False
    jira_client.can_create_issues.side_effect = JIRAError(status_code=401)
    # no error for 401
    board_is_valid(
        jira=jira_client,
        board=board,
        default_issue_type="task",
        default_reopen_state="new",
        jira_server_priorities={"Minor": "1", "Major": "2", "Critical": "3"},
        public_projects=[],
    )


def test_jira_permissions_validator_board_is_valid_archived(
    mocker: MockerFixture, gql_class_factory: Callable
) -> None:
    board = gql_class_factory(
        JiraBoardV1,
        {
            "name": "jira-board-default",
            "server": {
                "serverUrl": "https://jira-server.com",
                "token": {"path": "vault/path/token", "field": "token"},
            },
            "issueType": "bug",
            "issueResolveState": "Closed",
            "issueReopenState": "Open",
            "issueFields": None,
            "severityPriorityMappings": {
                "name": "major-major",
                "mappings": [
                    {"priority": "Minor"},
                    {"priority": "Major"},
                    {"priority": "Critical"},
                ],
            },
        },
    )
    jira_client = mocker.create_autospec(spec=JiraClient)
    jira_client.is_archived = True
    assert board_is_valid(
        jira=jira_client,
        board=board,
        default_issue_type="task",
        default_reopen_state="new",
        jira_server_priorities={"Minor": "1", "Major": "2", "Critical": "3"},
        public_projects=[],
    ) == (ValidationError.PROJECT_ARCHIVED, {})
