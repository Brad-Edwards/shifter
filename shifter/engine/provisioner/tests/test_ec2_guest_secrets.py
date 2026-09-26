"""Credential convergence must preserve existing identity and fail closed on ownership."""

from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

from ec2_guest_secrets import Ec2GuestSecrets, Ec2SecretError


def error(code):
    return ClientError({"Error": {"Code": code, "Message": "private response must not escape"}}, "SecretOperation")


def store():
    api = Mock()
    return Ec2GuestSecrets(api, environment="test", kms_key="arn:aws:kms:us-east-2:123456789012:key/test"), api


def description(name, **extra):
    return {
        "Name": name,
        "ARN": f"arn:aws:secretsmanager:us-east-2:123456789012:secret:{name}-abcdef",
        "KmsKeyId": "arn:aws:kms:us-east-2:123456789012:key/test",
        "Tags": [
            {"Key": "shifter:system", "Value": "shifter"},
            {"Key": "shifter:range_id", "Value": "7"},
            {"Key": "shifter:credential", "Value": "raes"},
        ],
        **extra,
    }


def test_new_secret_uses_deployment_key_and_reads_back_winner_after_create_race():
    secrets, api = store()
    name = secrets.name(7, "account-password", "node.a#0", "student")
    api.describe_secret.side_effect = [error("ResourceNotFoundException"), description(name)]
    api.create_secret.side_effect = error("ResourceExistsException")
    api.get_secret_value.return_value = {"SecretString": "winning-password"}
    ref, value = secrets.ensure(7, "account-password", ("node.a#0", "student"), lambda: "losing-password")
    assert value == "winning-password"
    assert ref == description(name)["ARN"]
    assert api.create_secret.call_args.kwargs["KmsKeyId"] == description(name)["KmsKeyId"]
    api.put_secret_value.assert_not_called()


@pytest.mark.parametrize("changes", [{"Tags": []}, {"DeletedDate": "scheduled"}, {"KmsKeyId": "other-key"}])
def test_foreign_or_retiring_secret_is_never_read_or_replaced(changes):
    secrets, api = store()
    name = secrets.name(7, "host-ssh", "node.a#0")
    api.describe_secret.return_value = description(name, **changes)
    factory = Mock(return_value="new-secret")
    with pytest.raises(Ec2SecretError):
        secrets.ensure(7, "host-ssh", ("node.a#0",), factory)
    api.get_secret_value.assert_not_called()
    api.create_secret.assert_not_called()
    factory.assert_not_called()


def test_denied_read_does_not_create_and_error_does_not_include_provider_response():
    secrets, api = store()
    api.describe_secret.side_effect = error("AccessDeniedException")
    with pytest.raises(Ec2SecretError) as caught:
        secrets.ensure(7, "host-ssh", ("node.a#0",), lambda: "secret")
    assert "private response" not in str(caught.value)
    api.create_secret.assert_not_called()


def test_names_preserve_range_and_purpose_but_hash_subject_without_collisions():
    secrets, _ = store()
    names = {secrets.name(7, "account-password", *subject) for subject in (("a/b", "c"), ("a", "b/c"), ("A", "b/c"))}
    assert len(names) == 3
    assert all(name.startswith("shifter/test/range/7/raes/account-password/") for name in names)
    assert secrets.name(8, "host-ssh", "a") != secrets.name(7, "host-ssh", "a")


def test_cleanup_never_deletes_a_foreign_secret():
    secrets, api = store()
    name = secrets.name(7, "host-ssh", "node.a#0")
    api.describe_secret.return_value = description(name, Tags=[])
    with pytest.raises(Ec2SecretError):
        secrets.delete(7, "host-ssh", ("node.a#0",))
    api.delete_secret.assert_not_called()


@pytest.mark.parametrize("method", ["host_ssh", "host_identity"])
def test_existing_guest_cannot_regenerate_a_missing_management_identity(method):
    secrets, api = store()
    api.describe_secret.side_effect = error("ResourceNotFoundException")
    with pytest.raises(Ec2SecretError):
        getattr(secrets, method)(7, "node.a#0", create=False)
    api.create_secret.assert_not_called()
