from collections import namedtuple
from collections.abc import Mapping
from typing import Any, TypedDict
from unittest.mock import (
    MagicMock,
    call,
    patch,
)

import pytest
from pytest_httpserver import HTTPServer
from pytest_mock import MockerFixture
from slack_sdk.errors import SlackApiError
from slack_sdk.web import SlackResponse

import reconcile
from reconcile.test.fixtures import Fixtures
from reconcile.utils.slack_api import (
    MAX_RETRIES,
    TIMEOUT,
    SlackApi,
    SlackApiConfig,
    UserNotFoundError,
)

SlackApiMock = namedtuple("SlackApiMock", "client mock_slack_client")


@pytest.fixture
def slack_api(mocker: MockerFixture) -> SlackApiMock:
    mock_slack_client = mocker.patch.object(
        reconcile.utils.slack_api, "WebClient", autospec=True
    )

    # autospec doesn't know about instance attributes
    mock_slack_client.return_value.retry_handlers = []

    slack_api = SlackApi("some-workspace", "token")

    return SlackApiMock(slack_api, mock_slack_client)


@pytest.fixture
def conversation_history(slack_api: SlackApiMock) -> SlackResponse:
    fixture = Fixtures("slack_api").get_anymarkup("conversations_history_messages.yaml")
    response = new_slack_response({
        "ok": True,
        "messages": fixture,
        "has_more": False,
        "pin_count": 1,
        "channel_actions_ts": None,
        "channel_actions_count": 0,
        "response_metadata": {"next_cursor": "bmV4dF90czoxNjg3NTIyMjA5MDMyMTM5"},
    })
    slack_api.mock_slack_client.return_value.conversations_history.side_effect = (
        response
    )

    return response


def test_slack_api_config_defaults() -> None:
    slack_api_config = SlackApiConfig()

    assert slack_api_config.max_retries == MAX_RETRIES
    assert slack_api_config.timeout == TIMEOUT


def test_slack_api_config_from_dict() -> None:
    data = {
        "global": {"max_retries": 1, "timeout": 5},
        "methods": [
            {"name": "users.list", "args": '{"limit":1000}'},
            {"name": "conversations.list", "args": '{"limit":500}'},
        ],
    }

    slack_api_config = SlackApiConfig.from_dict(data)

    assert isinstance(slack_api_config, SlackApiConfig)

    assert slack_api_config.get_method_config("users.list") == {"limit": 1000}
    assert slack_api_config.get_method_config("conversations.list") == {"limit": 500}
    assert slack_api_config.get_method_config("doesntexist") is None

    assert slack_api_config.max_retries == 1
    assert slack_api_config.timeout == 5


def new_slack_response(data: dict[str, Any]) -> SlackResponse:
    return SlackResponse(
        client="",
        http_verb="",
        api_url="",
        req_args={},
        data=data,
        headers={},
        status_code=0,
    )


def test_instantiate_slack_api_with_config(mocker: MockerFixture) -> None:
    """
    When SlackApiConfig is passed into SlackApi, the constructor shouldn't
    create a default configuration object.
    """
    mock_slack_client = mocker.patch.object(
        reconcile.utils.slack_api, "WebClient", autospec=True
    )

    # autospec doesn't know about instance attributes
    mock_slack_client.return_value.retry_handlers = []

    config = SlackApiConfig()

    slack_api = SlackApi("some-workspace", "token", config)

    assert slack_api.config is config


def test__get_default_args(slack_api: SlackApiMock) -> None:
    """
    There shouldn't be any extra params passed to the client if config is
    unset.
    """
    slack_api.mock_slack_client.return_value.api_call.return_value = {
        "channels": [],
        "response_metadata": {"next_cursor": ""},
    }

    slack_api.client._get("channels")

    assert slack_api.mock_slack_client.return_value.api_call.call_args == call(
        "conversations.list", http_verb="GET", params={"cursor": ""}
    )


def test__get_with_matching_method_config(slack_api: SlackApiMock) -> None:
    """Passing in a SlackApiConfig object with a matching method name."""
    slack_api.mock_slack_client.return_value.api_call.return_value = {
        "channels": [],
        "response_metadata": {"next_cursor": ""},
    }

    api_config = SlackApiConfig()
    api_config.set_method_config("conversations.list", {"limit": 500})
    slack_api.client.config = api_config

    slack_api.client._get("channels")

    assert slack_api.mock_slack_client.return_value.api_call.call_args == call(
        "conversations.list", http_verb="GET", params={"limit": 500, "cursor": ""}
    )


def test__get_without_matching_method_config(slack_api: SlackApiMock) -> None:
    """Passing in a SlackApiConfig object without a matching method name."""
    slack_api.mock_slack_client.return_value.api_call.return_value = {
        "something": [],
        "response_metadata": {"next_cursor": ""},
    }

    api_config = SlackApiConfig()
    api_config.set_method_config("conversations.list", {"limit": 500})
    slack_api.client.config = api_config

    slack_api.client._get("something")

    assert slack_api.mock_slack_client.return_value.api_call.call_args == call(
        "something.list", http_verb="GET", params={"cursor": ""}
    )


def test__get_uses_cache(slack_api: SlackApiMock) -> None:
    """The API is never called when the results are already cached."""
    # Reset the mock to clear any calls during __init__
    slack_api.mock_slack_client.return_value.api_call.reset_mock()

    slack_api.client._results["channels"] = ["some", "data"]

    assert slack_api.client._get("channels") == ["some", "data"]
    slack_api.mock_slack_client.return_value.api_call.assert_not_called()


def test_chat_post_message(slack_api: SlackApiMock) -> None:
    """Don't raise an exception when the channel is set."""
    slack_api.client.channel = "some-channel"
    slack_api.client.chat_post_message("test")


def test_chat_post_message_missing_channel(slack_api: SlackApiMock) -> None:
    """Raises an exception when channel isn't set."""
    slack_api.client.channel = None
    with pytest.raises(ValueError):
        slack_api.client.chat_post_message("test")


def test_chat_post_message_channel_not_found(
    mocker: MockerFixture, slack_api: SlackApiMock
) -> None:
    slack_api.client.channel = "test"
    mock_join = mocker.patch(
        "reconcile.utils.slack_api.SlackApi.join_channel", autospec=True
    )
    nf_resp = new_slack_response({"ok": False, "error": "not_in_channel"})
    slack_api.mock_slack_client.return_value.chat_postMessage.side_effect = [
        SlackApiError("error", nf_resp),
        None,
    ]
    slack_api.client.chat_post_message("foo")
    assert slack_api.mock_slack_client.return_value.chat_postMessage.call_count == 2
    mock_join.assert_called_once()


def test_chat_post_message_ok(slack_api: SlackApiMock) -> None:
    slack_api.client.channel = "test"
    ok_resp = new_slack_response({"ok": True})
    slack_api.mock_slack_client.return_value.chat_postMessage.side_effect = ok_resp
    slack_api.client.chat_post_message("foo")
    slack_api.mock_slack_client.return_value.chat_postMessage.assert_called_once()


def test_chat_post_message_raises_other(slack_api: SlackApiMock) -> None:
    slack_api.client.channel = "test"
    err_resp = new_slack_response({"ok": False, "error": "no_text"})
    slack_api.mock_slack_client.return_value.chat_postMessage.side_effect = (
        SlackApiError("error", err_resp)
    )
    with pytest.raises(SlackApiError):
        slack_api.client.chat_post_message("foo")
    slack_api.mock_slack_client.return_value.chat_postMessage.assert_called_once()


def test_join_channel_missing_channel(slack_api: SlackApiMock) -> None:
    """Raises an exception when the channel is not set."""
    slack_api.client.channel = None
    with pytest.raises(ValueError):
        slack_api.client.join_channel()


@pytest.mark.parametrize("joined", [True, False])
def test_join_channel_already_joined(
    slack_api: SlackApiMock, mocker: MockerFixture, joined: bool
) -> None:
    mocker.patch(
        "reconcile.utils.slack_api.SlackApi.get_channels_by_names",
        return_value={"123": "test", "456": "foo"},
    )
    slack_api.client.channel = "test"
    slack_response = MagicMock(SlackResponse)
    slack_response.data = {"channel": {"is_member": joined}}
    slack_api.mock_slack_client.return_value.conversations_info.return_value = (
        slack_response
    )
    slack_api.mock_slack_client.return_value.conversations_join.return_value = None
    slack_api.client.join_channel()
    slack_api.mock_slack_client.return_value.conversations_info.assert_called_once_with(
        channel="123"
    )
    if joined:
        slack_api.mock_slack_client.return_value.conversations_join.assert_not_called()
    else:
        slack_api.mock_slack_client.return_value.conversations_join.assert_called_once_with(
            channel="123"
        )


def test_create_usergroup(slack_api: SlackApiMock) -> None:
    slack_api.client.create_usergroup("ABCD")

    assert slack_api.mock_slack_client.return_value.usergroups_create.call_args == call(
        name="ABCD", handle="ABCD"
    )


@pytest.mark.parametrize(
    "user,ids,expected",
    [
        (
            {
                "id": "ID_A",
                "name": "user_a",
                "profile": {"email": "user_a@example.com"},
            },
            ["ID_A"],
            {"ID_A": "user_a"},
        ),
        (
            {
                "id": "ID_A",
                "name": "user_a",
                "profile": {"email": "user_a@example.com"},
                "enterprise_user": {"id": "ENTERPRISE_ID_A"},
            },
            ["ENTERPRISE_ID_A"],
            {"ID_A": "user_a"},
        ),
        (
            {
                "id": "ID_A",
                "name": "user_a",
                "profile": {"email": "user_a@example.com"},
                "enterprise_user": {"id": "ENTERPRISE_ID_A"},
            },
            ["ID_A"],
            {"ID_A": "user_a"},
        ),
        (
            {
                "id": "ID_A",
                "name": "user_a",
                "profile": {"email": "user_a@example.com"},
                "enterprise_user": {"id": "ENTERPRISE_ID_A"},
            },
            ["ID_NOT_FOUND"],
            {},
        ),
    ],
)
def test_get_users_by_ids(
    slack_api: SlackApiMock, user: dict, ids: list[str], expected: dict
) -> None:
    slack_response = new_slack_response({
        "members": [user],
        "response_metadata": {"next_cursor": ""},
    })
    slack_api.mock_slack_client.return_value.api_call.return_value = slack_response

    assert slack_api.client.get_users_by_ids(ids) == expected


@pytest.mark.parametrize(
    "user_data, input_names, expected_output",
    [
        (
            {
                "ID_A": {
                    "deleted": False,
                    "name": "not_user_a",
                    "profile": {"email": "user_a@example.com"},
                }
            },
            ["user_a"],
            {"ID_A": "user_a"},
        ),
        (
            {
                "ID_A": {
                    "deleted": True,
                    "name": "user_a",
                    "profile": {"email": "user_a@example.com"},
                }
            },
            ["user_a"],
            {},
        ),
        (
            {
                "ID_A": {
                    "deleted": False,
                    "name": "user_a",
                    "profile": {},
                }
            },
            ["user_a"],
            {},
        ),
        (
            {
                "ID_A": {
                    "deleted": False,
                    "name": "user_a",
                    "profile": {"email": "other@example.com"},
                }
            },
            ["user_a"],
            {},
        ),
        (
            {
                "ID_A": {
                    "deleted": False,
                    "name": "testuser_1",
                    "profile": {"email": "testuser@redhat.com"},
                },
                "ID_B": {
                    "deleted": False,
                    "name": "testuser_2",
                    "profile": {"email": "testuser@gmail.com"},
                },
            },
            ["testuser"],
            {"ID_A": "testuser", "ID_B": "testuser"},
        ),
    ],
)
def test_get_active_users_by_names(
    slack_api: SlackApiMock,
    user_data: dict[str, dict[str, Any]],
    input_names: list[str],
    expected_output: dict[str, str],
) -> None:
    slack_api.client._results["users"] = user_data
    result = slack_api.client.get_active_users_by_names(input_names)
    assert result == expected_output


def test_update_usergroup_users(slack_api: SlackApiMock) -> None:
    slack_api.client.update_usergroup_users("ABCD", ["USERA", "USERB"])

    assert (
        slack_api.mock_slack_client.return_value.usergroups_users_update.call_args
        == call(usergroup="ABCD", users=["USERA", "USERB"])
    )


@patch.object(SlackApi, "get_random_deleted_user", autospec=True)
def test_update_usergroup_users_empty_list(
    mock_get_deleted: MagicMock, slack_api: SlackApiMock
) -> None:
    """Passing in an empty list supports removing all users from a group."""
    mock_get_deleted.return_value = "a-deleted-user"

    slack_api.client.update_usergroup_users("ABCD", [])

    assert (
        slack_api.mock_slack_client.return_value.usergroups_users_update.call_args
        == call(usergroup="ABCD", users=["a-deleted-user"])
    )


def test_get_user_id_by_name_user_not_found(slack_api: SlackApiMock) -> None:
    """
    Check that UserNotFoundException will be raised under expected conditions.
    """
    slack_api.mock_slack_client.return_value.users_lookupByEmail.side_effect = (
        SlackApiError("Some error message", {"error": "users_not_found"})
    )

    with pytest.raises(UserNotFoundError):
        slack_api.client.get_user_id_by_name("someuser", "redhat.com")


def test_get_user_id_by_name_reraise(slack_api: SlackApiMock) -> None:
    """
    Check that SlackApiError is re-raised when not otherwise handled as a user
    not found error.
    """
    slack_api.mock_slack_client.return_value.users_lookupByEmail.side_effect = (
        SlackApiError("Some error message", {"error": "internal_error"})
    )

    with pytest.raises(SlackApiError):
        slack_api.client.get_user_id_by_name("someuser", "redhat.com")


def test_update_usergroups_users_empty_no_raise(
    mocker: MockerFixture, slack_api: SlackApiMock
) -> None:
    """
    invalid_users errors shouldn't be raised because providing an empty
    list is actually removing users from the usergroup.
    """
    mocker.patch.object(SlackApi, "get_random_deleted_user", autospec=True)

    slack_api.mock_slack_client.return_value.usergroups_users_update.side_effect = (
        SlackApiError("Some error message", {"error": "invalid_users"})
    )

    slack_api.client.update_usergroup_users("ABCD", [])


def test_update_usergroups_users_raise(slack_api: SlackApiMock) -> None:
    """
    Any errors other than invalid_users should result in an exception being
    raised.
    """
    slack_api.mock_slack_client.return_value.usergroups_users_update.side_effect = (
        SlackApiError("Some error message", {"error": "internal_error"})
    )

    with pytest.raises(SlackApiError):
        slack_api.client.update_usergroup_users("ABCD", ["USERA"])


def test_get_flat_conversation_history_no_messages(
    slack_api: SlackApiMock, mocker: MockerFixture, conversation_history: SlackResponse
) -> None:
    mocker.patch(
        "reconcile.utils.slack_api.SlackApi.get_channels_by_names",
        return_value={"123": "channel"},
    )
    fixture_messages = conversation_history["messages"]
    first_ts = fixture_messages[0]["ts"]

    # No messages
    from_timestamp = int(float(first_ts)) + 900
    to_timestamp = int(float(first_ts)) + 1000

    slack_api.client.channel = "channel"
    messages = slack_api.client.get_flat_conversation_history(
        from_timestamp, to_timestamp
    )
    slack_api.mock_slack_client.return_value.conversations_history.assert_called_once()
    assert len(messages) == 0


def test_get_flat_conversation_history(
    slack_api: SlackApiMock, mocker: MockerFixture, conversation_history: SlackResponse
) -> None:
    mocker.patch(
        "reconcile.utils.slack_api.SlackApi.get_channels_by_names",
        return_value={"123": "channel"},
    )
    fixture_messages = conversation_history["messages"]
    first_ts = fixture_messages[0]["ts"]
    last_ts = fixture_messages[-1]["ts"]

    # all messages but the last one
    from_timestamp = int(float(last_ts)) + 1
    to_timestamp = int(float(first_ts)) + 1

    slack_api.client.channel = "channel"
    messages = slack_api.client.get_flat_conversation_history(
        from_timestamp, to_timestamp
    )
    slack_api.mock_slack_client.return_value.conversations_history.assert_called_once()
    assert len(messages) == len(fixture_messages) - 1


#
# Slack WebClient retry tests
#
# These tests are meant to ensure that the built-in retry functionality is
# working as expected in the Slack WebClient. This provides some verification
# that the handlers are configured properly, as well as testing the custom
# ServerErrorRetryHandler handler.
#
@pytest.fixture
def slack_client(httpserver: HTTPServer) -> SlackApi:
    return SlackApi(
        "workspace",
        "token",
        init_usergroups=False,
        slack_url=httpserver.url_for("/api/"),
    )


class JsonResponse(TypedDict):
    response_json: Any
    status: int
    headers: Mapping[str, str] | None


def test_slack_api__client_throttle_raise(
    patch_sleep: None, httpserver: HTTPServer, slack_client: SlackApi
) -> None:
    """Raise an exception if the max retries is exceeded."""
    httpserver.expect_request("/api/users.list", method="post").respond_with_json(
        {"ok": "false", "error": "ratelimited"},
        headers={"Retry-After": "1"},
        status=429,
    )

    with pytest.raises(SlackApiError):
        slack_client._sc.api_call("users.list")

    assert len(httpserver.log) == MAX_RETRIES + 1


def test_slack_api__client_throttle_doesnt_raise(
    patch_sleep: None, httpserver: HTTPServer, slack_client: SlackApi
) -> None:
    """Don't raise an exception if the max retries aren't reached."""
    uri_args = ("/api/users.list", "post")
    uri_kwargs_failure: JsonResponse = {
        "headers": {"Retry-After": "1"},
        "response_json": {"ok": "false", "error": "ratelimited"},
        "status": 429,
    }
    uri_kwargs_success: JsonResponse = {
        "response_json": {"ok": "true"},
        "status": 200,
        "headers": None,
    }

    httpserver.expect_ordered_request(*uri_args).respond_with_json(**uri_kwargs_failure)
    httpserver.expect_ordered_request(*uri_args).respond_with_json(**uri_kwargs_failure)
    httpserver.expect_ordered_request(*uri_args).respond_with_json(**uri_kwargs_failure)
    httpserver.expect_ordered_request(*uri_args).respond_with_json(**uri_kwargs_success)

    slack_client._sc.api_call("users.list")

    assert len(httpserver.log) == 4


def test_slack_api__client_5xx_raise(
    patch_sleep: None, httpserver: HTTPServer, slack_client: SlackApi
) -> None:
    """Raise an exception if the max retries is exceeded."""
    httpserver.expect_request("/api/users.list", method="post").respond_with_json(
        {"ok": "false", "error": "internal_error"},
        status=500,
    )
    with pytest.raises(SlackApiError):
        slack_client._sc.api_call("users.list")

    assert len(httpserver.log) == MAX_RETRIES + 1


def test_slack_api__client_5xx_doesnt_raise(
    patch_sleep: None, httpserver: HTTPServer, slack_client: SlackApi
) -> None:
    """Don't raise an exception if the max retries aren't reached."""
    uri_args = ("/api/users.list", "post")
    uri_kwargs_failure: JsonResponse = {
        "response_json": {"ok": "false", "error": "internal_error"},
        "status": 500,
        "headers": None,
    }
    uri_kwargs_success: JsonResponse = {
        "response_json": {"ok": "true"},
        "status": 200,
        "headers": None,
    }

    httpserver.expect_ordered_request(*uri_args).respond_with_json(**uri_kwargs_failure)
    httpserver.expect_ordered_request(*uri_args).respond_with_json(**uri_kwargs_failure)
    httpserver.expect_ordered_request(*uri_args).respond_with_json(**uri_kwargs_failure)
    httpserver.expect_ordered_request(*uri_args).respond_with_json(**uri_kwargs_success)

    slack_client._sc.api_call("users.list")

    assert len(httpserver.log) == 4


def test_slack_api__client_dont_retry(
    patch_sleep: None, httpserver: HTTPServer, slack_client: SlackApi
) -> None:
    """Don't retry client-side errors that aren't 429s."""
    httpserver.expect_request("/api/users.list", method="post").respond_with_json(
        {"ok": "false", "error": "internal_error"},
        status=401,
    )
    with pytest.raises(SlackApiError):
        slack_client._sc.api_call("users.list")

    assert len(httpserver.log) == 1
