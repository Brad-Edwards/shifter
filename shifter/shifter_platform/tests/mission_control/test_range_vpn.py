"""Mission Control range OpenVPN profile delivery (#1696)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from cms.models import RangeInstance
from cms.models import Request as CmsRequest
from cms.services import (
    CtfOpenVpnProfileConflict,
    CtfOpenVpnProfileNotFound,
    get_own_mission_control_openvpn_profile,
    has_own_mission_control_openvpn_profile,
)
from engine.models import Instance, Range
from engine.models import Request as EngineRequest
from shared.enums import RangeSource, RequestType, ResourceStatus
from tests.engine.services.conftest import boto3_secrets, make_secrets_client

pytestmark = pytest.mark.django_db

PROFILE = (
    "client\n"
    "dev tun\n"
    "proto udp\n"
    "remote vpn.example.test 1194\n"
    "nobind\n"
    "persist-key\n"
    "persist-tun\n"
    "remote-cert-tls server\n"
    "auth-nocache\n"
    "verb 3\n"
    "<ca>\nTEST-CA\n</ca>\n"
    "<cert>\nTEST-CERT\n</cert>\n"
    "<key>\nTEST-KEY\n</key>\n"
    "<tls-crypt>\nTEST-TLS-CRYPT\n</tls-crypt>\n"
)


def _mc_range(user, *, status="ready", with_binding=True):
    request_id = uuid4()
    cms_request = CmsRequest.objects.create(request_id=request_id, request_type=RequestType.RANGE.value, user=user)
    cms_range = RangeInstance.objects.create(
        request=cms_request,
        scenario_id="basic",
        user_id=user.id,
        status=status,
        range_source=RangeSource.MISSION_CONTROL.value,
    )
    if with_binding:
        engine_request = EngineRequest.objects.create(
            request_id=request_id, request_type=RequestType.RANGE.value, user=user
        )
        target_ref = uuid4()
        Instance.objects.create(
            uuid=target_ref,
            request=engine_request,
            role=Instance.Role.ATTACKER,
            os_type=Instance.OSType.KALI,
            status=Range.Status.READY,
        )
        Range.objects.create(
            request=engine_request,
            user=user,
            status=Range.Status.READY,
            vpn_access_binding={
                "version": "openvpn-binding-v1",
                "channel": "openvpn",
                "generation": str(request_id),
                "owner_user_id": user.id,
                "target_ref": str(target_ref),
                "endpoint": "vpn.example.test",
                "port": 1194,
                "profile_version": "openvpn-profile-v1",
                "secret_ref": f"arn:aws:secretsmanager:eu-central-1:123:secret:range-vpn-{request_id}",
                "ready": True,
            },
        )
    return cms_range


@pytest.fixture
def mc_user(django_user_model):
    return django_user_model.objects.create_user(username="mc@test.com", email="mc@test.com", is_staff=True)


class TestMissionControlVpnGate:
    def test_serves_own_ready_mission_control_range(self, settings, mc_user):
        settings.CLOUD_PROVIDER = "aws"
        _mc_range(mc_user)
        with boto3_secrets(make_secrets_client(PROFILE)):
            profile = get_own_mission_control_openvpn_profile(mc_user)
        assert profile.content == PROFILE.encode()

    def test_ignores_ctf_sourced_ranges(self, mc_user):
        request = CmsRequest.objects.create(request_id=uuid4(), request_type=RequestType.RANGE.value, user=mc_user)
        RangeInstance.objects.create(
            request=request,
            scenario_id="basic",
            user_id=mc_user.id,
            status=ResourceStatus.READY.value,
            range_source=RangeSource.CTF.value,
        )
        with pytest.raises(CtfOpenVpnProfileNotFound):
            get_own_mission_control_openvpn_profile(mc_user)

    def test_not_ready_range_conflicts(self, mc_user):
        _mc_range(mc_user, status="provisioning", with_binding=False)
        with pytest.raises(CtfOpenVpnProfileConflict):
            get_own_mission_control_openvpn_profile(mc_user)

    def test_capability_bit_is_safe(self, settings, mc_user):
        settings.CLOUD_PROVIDER = "aws"
        assert has_own_mission_control_openvpn_profile(mc_user) is False
        _mc_range(mc_user)
        assert has_own_mission_control_openvpn_profile(mc_user) is True


class TestVpnProfileEndpoint:
    def test_download_and_not_found(self, settings, client, mc_user):
        settings.CLOUD_PROVIDER = "aws"
        client.force_login(mc_user)
        response = client.post("/api/v1/mission-control/range/vpn-profile/")
        assert response.status_code == 404

        _mc_range(mc_user)
        with boto3_secrets(make_secrets_client(PROFILE)):
            response = client.post("/api/v1/mission-control/range/vpn-profile/")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/x-openvpn-profile"
        assert response.content == PROFILE.encode()
        assert response["Cache-Control"] == "private, no-store"
