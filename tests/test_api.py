"""Tests for the SEUR account client."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.seur.api import SEURApiClient, SEURApiError, SEURAuthError

from .payloads import EMAIL, PROFILE, TOKEN, inbox, shipment


def response(status: int, body: object) -> MagicMock:
    """Build a minimal aiohttp response context manager."""
    result = AsyncMock()
    result.status = status
    result.json = AsyncMock(return_value=body)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=result)
    context.__aexit__ = AsyncMock(return_value=False)
    return context


async def test_login_and_inbox_use_confirmed_shapes():
    session = MagicMock()
    session.post = MagicMock(
        side_effect=[
            response(200, TOKEN),
            response(200, PROFILE),
            response(
                200, inbox(received=[shipment()], sent=[shipment("SEUR-TEST-OUT")])
            ),
        ]
    )
    client = SEURApiClient(EMAIL, "not-a-real-password", session)
    assert await client.async_login() == {"uuid": PROFILE["usuario"]["uuid"]}
    result = await client.async_get_parcels()
    assert [item["clave_envio"] for item in result["incoming"]] == ["SEUR-TEST-0001"]
    assert [item["clave_envio"] for item in result["outgoing"]] == ["SEUR-TEST-OUT"]
    assert session.post.call_args_list[0].kwargs["data"]["grant_type"] == "password"
    assert (
        session.post.call_args_list[1].kwargs["headers"]["Access-Control-Allow-Origin"]
        == "www.seur.com"
    )
    for call in session.post.call_args_list[1:]:
        assert call.kwargs["headers"]["Authorization"] == "Bearer synthetic-token"


async def test_expired_token_logs_in_again_once():
    session = MagicMock()
    session.post = MagicMock(
        side_effect=[
            response(200, TOKEN),
            response(200, PROFILE),
            response(401, None),
            response(200, TOKEN),
            response(200, PROFILE),
            response(200, inbox(received=[shipment()], sent=[])),
        ]
    )
    client = SEURApiClient(EMAIL, "password", session)
    await client.async_login()
    result = await client.async_get_parcels()
    assert len(result["incoming"]) == 1
    assert session.post.call_count == 6


async def test_inbox_401_after_fresh_login_is_api_error():
    session = MagicMock()
    session.post = MagicMock(
        side_effect=[
            response(200, TOKEN),
            response(200, PROFILE),
            response(401, None),
            response(200, TOKEN),
            response(200, PROFILE),
            response(401, None),
        ]
    )
    client = SEURApiClient(EMAIL, "password", session)
    with pytest.raises(SEURApiError) as err:
        await client.async_get_parcels()
    assert not isinstance(err.value, SEURAuthError)
    assert err.value.status_code == 401


async def test_invalid_grant_is_auth_error():
    session = MagicMock()
    session.post = MagicMock(return_value=response(401, {"error": "invalid_grant"}))
    with pytest.raises(SEURAuthError):
        await SEURApiClient(EMAIL, "wrong", session).async_login()


@pytest.mark.parametrize("body", [{}, {"access_token": ""}, []])
async def test_invalid_token_shape_is_api_error(body):
    session = MagicMock()
    session.post = MagicMock(return_value=response(200, body))
    with pytest.raises(SEURApiError):
        await SEURApiClient(EMAIL, "wrong", session).async_login()


async def test_inbox_requires_both_collections():
    session = MagicMock()
    session.post = MagicMock(
        side_effect=[
            response(200, TOKEN),
            response(200, PROFILE),
            response(200, {"codigo_error": None, "msg_error": None, "recibidos": []}),
        ]
    )
    client = SEURApiClient(EMAIL, "password", session)
    with pytest.raises(SEURApiError):
        await client.async_get_parcels()


@pytest.mark.parametrize(
    ("profile", "error"),
    [
        ({"codigo_error": None, "msg_error": None, "usuario": {}}, SEURApiError),
        (
            {
                "codigo_error": None,
                "msg_error": None,
                "usuario": {"uuid": "id", "username": "other@example.test"},
            },
            SEURAuthError,
        ),
    ],
)
async def test_login_rejects_missing_uuid_and_other_account(profile, error):
    session = MagicMock()
    session.post = MagicMock(side_effect=[response(200, TOKEN), response(200, profile)])
    with pytest.raises(error):
        await SEURApiClient(EMAIL, "password", session).async_login()


async def test_login_rejects_carrier_error_and_non_json():
    session = MagicMock()
    session.post = MagicMock(
        side_effect=[response(200, TOKEN), response(200, {"codigo_error": "x"})]
    )
    with pytest.raises(SEURApiError) as err:
        await SEURApiClient(EMAIL, "password", session).async_login()
    assert err.value.response_keys == ("codigo_error",)


def test_api_error_str_includes_status_and_keys_for_default_log_level():
    err = SEURApiError("shipment inbox failed", status_code=500, response_keys=("x",))
    assert str(err) == "shipment inbox failed, status=500, keys=('x',)"


async def test_json_parser_failure_is_safe_api_error():
    session = MagicMock()
    context = response(200, {})
    context.__aenter__.return_value.json = AsyncMock(side_effect=ValueError())
    session.post = MagicMock(return_value=context)
    with pytest.raises(SEURApiError, match="non-JSON"):
        await SEURApiClient(EMAIL, "password", session).async_login()
