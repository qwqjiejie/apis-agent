import json

import pytest
from fastapi import FastAPI, Request
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.api.routes.agent import (
    GatewaySwitchRequest,
    gateway_status,
    gateway_switch,
)
from app.bootstrap.container import (
    ApplicationContainer,
    clear_application_container,
    get_application_container,
    set_application_container,
)
from app.common.llm import build_llm
from app.gateway.middleware import GatewayModelWrapper
from app.gateway.model_gateway import ModelGateway


@pytest.mark.asyncio
async def test_build_llm_uses_the_registered_runtime_gateway():
    gateway = ModelGateway()
    await gateway.register(
        "runtime-model",
        FakeListChatModel(responses=["ok"]),
        is_primary=True,
    )
    container = ApplicationContainer(model_gateway=gateway)
    set_application_container(container)

    try:
        llm = build_llm()
        assert isinstance(llm, GatewayModelWrapper)
        assert llm._gateway is gateway
        assert get_application_container() is container
    finally:
        clear_application_container(container)

    assert get_application_container(required=False) is None


@pytest.mark.asyncio
async def test_gateway_admin_endpoints_operate_on_the_request_container():
    gateway = ModelGateway()
    await gateway.register(
        "primary",
        FakeListChatModel(responses=["primary"]),
        is_primary=True,
    )
    await gateway.register(
        "fallback",
        FakeListChatModel(responses=["fallback"]),
        is_primary=False,
    )
    test_app = FastAPI()
    test_app.state.container = ApplicationContainer(model_gateway=gateway)
    request = Request({"type": "http", "headers": [], "app": test_app})

    before = json.loads((await gateway_status(request)).body)
    assert before["data"]["primary"]["active"] is True

    switched = json.loads((await gateway_switch(
        GatewaySwitchRequest(modelName="fallback"),
        request,
    )).body)

    assert switched["code"] == 200
    assert gateway.get_active_name() == "fallback"
    after = json.loads((await gateway_status(request)).body)
    assert after["data"]["fallback"]["active"] is True
