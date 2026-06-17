"""Behavior tests for experiment services (scripts + experiment creation).

Drives ``list_scripts`` / ``delete_script`` / ``create_experiment`` /
``get_scenario_instances`` against real ``ScriptAsset`` / ``Experiment`` /
``ExperimentScript`` / ``AuditLog`` rows, real users/groups for the CMS-authoring
authorization policy, and the real scenario registry (the built-in ``basic``
template, whose instances are ``Attacker`` + ``Workstation``) — instead of
patching ``ScriptAsset`` / ``Experiment`` / ``ExperimentScript`` / ``audit_log`` /
``check_scenario_access`` / ``load_scenario_template`` / ``transaction`` /
``_check_result_type``.
"""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from cms.experiments import services
from cms.experiments.exceptions import ExperimentValidationError, ScriptUploadError
from cms.experiments.models import Experiment, ExperimentScript, ScriptAsset
from cms.experiments.schemas import ExperimentCreateInput, ExperimentStatus
from shared.auth import THREAT_RESEARCH_GROUP

pytestmark = pytest.mark.django_db

User = get_user_model()


def _user(suffix, *, is_staff=False, is_active=True, threat_research=False):
    user = User.objects.create_user(
        username=f"exp-svc-{suffix}@e.com", email=f"exp-svc-{suffix}@e.com", is_staff=is_staff, is_active=is_active
    )
    if threat_research:
        group, _ = Group.objects.get_or_create(name=THREAT_RESEARCH_GROUP)
        user.groups.add(group)
    return user


def _script(user, *, name="script"):
    return ScriptAsset.objects.create(
        user=user,
        name=name,
        s3_key=f"scripts/{user.id}/{name}.py",
        original_filename=f"{name}.py",
        file_size_bytes=100,
    )


class TestListScripts:
    def test_returns_only_active_for_user(self):
        user = _user("list1", is_staff=True)
        _script(user, name="Active")
        deleted = _script(user, name="Deleted")
        deleted.deleted_at = timezone.now()
        deleted.save(update_fields=["deleted_at"])

        scripts = services.list_scripts(user)
        assert [s.name for s in scripts] == ["Active"]

    def test_other_user_sees_own(self):
        owner = _user("list-owner", is_staff=True)
        other = _user("list-other", is_staff=True)
        _script(owner, name="OwnerScript")
        _script(other, name="OtherScript")

        assert [s.name for s in services.list_scripts(other)] == ["OtherScript"]


class TestDeleteScript:
    def test_soft_deletes_own_script(self):
        user = _user("del", is_staff=True)
        script = _script(user, name="ToDelete")
        services.delete_script(user, script.pk)

        script.refresh_from_db()
        assert script.deleted_at is not None
        assert not ScriptAsset.objects.filter(pk=script.pk).exists()

    def test_cannot_delete_other_users_script(self):
        user = _user("del2", is_staff=True)
        other = _user("del2-other", is_staff=True)
        script = _script(other, name="Theirs")
        with pytest.raises(ScriptUploadError, match="not found"):
            services.delete_script(user, script.pk)


class TestCreateExperiment:
    def test_create_basic_experiment(self):
        user = _user("create", is_staff=True)
        data = ExperimentCreateInput(name="Test Experiment", scenario_id="basic", total_runs=3, max_parallel_runs=2)

        exp = services.create_experiment(user, data)

        persisted = Experiment.objects.get(pk=exp.pk)
        assert persisted.name == "Test Experiment"
        assert persisted.status == ExperimentStatus.DRAFT.value
        assert persisted.user_id == user.id
        assert persisted.total_runs == 3

    def test_create_with_script_assignments(self):
        user = _user("create-scripts", is_staff=True)
        script = _script(user, name="VictimScript")
        data = ExperimentCreateInput(
            name="With Scripts",
            scenario_id="basic",
            total_runs=1,
            scripts=[
                {"instance_name": "Workstation", "script_type": "python", "script_id": script.pk, "execution_order": 0},
                {
                    "instance_name": "Attacker",
                    "script_type": "claude_code",
                    "claude_prompt": "Attack {{Workstation.ip}}",
                    "execution_order": 100,
                },
            ],
        )

        exp = services.create_experiment(user, data)
        assert ExperimentScript.objects.filter(experiment=exp).count() == 2

    def test_invalid_scenario_raises(self):
        user = _user("create-badscn", is_staff=True)
        data = ExperimentCreateInput(name="Bad Scenario", scenario_id="nonexistent")
        with pytest.raises(ExperimentValidationError, match="Invalid scenario"):
            services.create_experiment(user, data)

    def test_invalid_instance_name_raises(self):
        user = _user("create-badinst", is_staff=True)
        script = _script(user)
        data = ExperimentCreateInput(
            name="Bad Instance",
            scenario_id="basic",
            scripts=[{"instance_name": "NonExistentBox", "script_type": "python", "script_id": script.pk}],
        )
        with pytest.raises(ExperimentValidationError, match="not found in scenario"):
            services.create_experiment(user, data)

    def test_invalid_template_variable_rejected(self):
        """Pure Pydantic validation - no DB needed."""
        from pydantic import ValidationError as PydanticValidationError

        input_data = {
            "name": "Bad Template Var",
            "scenario_id": "basic",
            "scripts": [
                {
                    "instance_name": "Attacker",
                    "script_type": "claude_code",
                    "claude_prompt": "Attack {{NonExistent.ip}}",
                    "execution_order": 100,
                }
            ],
        }
        with pytest.raises(PydanticValidationError, match="Unknown instance"):
            ExperimentCreateInput.model_validate(input_data, context={"instance_names": {"Workstation", "Attacker"}})

    def test_invalid_template_property_rejected(self):
        """Pure Pydantic validation - no DB needed."""
        from pydantic import ValidationError as PydanticValidationError

        input_data = {
            "name": "Bad Template Prop",
            "scenario_id": "basic",
            "scripts": [
                {
                    "instance_name": "Attacker",
                    "script_type": "claude_code",
                    "claude_prompt": "Attack {{Workstation.password}}",
                    "execution_order": 100,
                }
            ],
        }
        with pytest.raises(PydanticValidationError, match="Unknown property"):
            ExperimentCreateInput.model_validate(input_data, context={"instance_names": {"Workstation", "Attacker"}})

    def test_valid_template_variable_accepted(self):
        user = _user("create-goodtpl", is_staff=True)
        input_data = {
            "name": "Good Template",
            "scenario_id": "basic",
            "scripts": [
                {
                    "instance_name": "Attacker",
                    "script_type": "claude_code",
                    "claude_prompt": "Attack {{Workstation.ip}} named {{Workstation.name}}",
                    "execution_order": 100,
                }
            ],
        }
        data = ExperimentCreateInput.model_validate(input_data, context={"instance_names": {"Workstation", "Attacker"}})
        exp = services.create_experiment(user, data)
        assert Experiment.objects.filter(pk=exp.pk).exists()


class TestCreateExperimentAccess:
    """create_experiment enforces shared.auth.can_edit_cms_authoring (active staff
    or active Threat Research member); per-scenario availability is then enforced
    by check_scenario_access.
    """

    def test_unrelated_user_blocked(self):
        user = _user("acc-unrelated")
        data = ExperimentCreateInput(name="Blocked", scenario_id="basic")
        with pytest.raises(PermissionDenied, match="Active staff or Threat Research"):
            services.create_experiment(user, data)

    def test_inactive_threat_research_user_blocked(self):
        user = _user("acc-tr-inactive", is_active=False, threat_research=True)
        data = ExperimentCreateInput(name="Blocked", scenario_id="basic")
        with pytest.raises(PermissionDenied, match="Active staff or Threat Research"):
            services.create_experiment(user, data)

    def test_staff_user_allowed(self):
        user = _user("acc-staff", is_staff=True)
        exp = services.create_experiment(user, ExperimentCreateInput(name="Allowed", scenario_id="basic"))
        assert Experiment.objects.filter(pk=exp.pk).exists()

    def test_threat_research_user_allowed(self):
        user = _user("acc-tr", threat_research=True)
        exp = services.create_experiment(user, ExperimentCreateInput(name="Allowed", scenario_id="basic"))
        assert Experiment.objects.filter(pk=exp.pk, user=user).exists()

    def test_threat_research_user_propagates_scenario_rejection(self):
        """A Threat Research user passes auth (no PermissionDenied) and a
        scenario-level denial surfaces as ExperimentValidationError, not a generic
        auth denial.
        """
        user = _user("acc-tr-scn", threat_research=True)
        data = ExperimentCreateInput(name="Hidden", scenario_id="nonexistent-internal")
        with pytest.raises(ExperimentValidationError, match="Invalid scenario"):
            services.create_experiment(user, data)

    def test_scenario_instances_blocks_unrelated_user(self):
        user = _user("acc-scn-unrelated")
        with pytest.raises(PermissionDenied, match="Active staff or Threat Research"):
            services.get_scenario_instances("basic", user)

    def test_scenario_instances_allows_threat_research_user(self):
        user = _user("acc-scn-tr", threat_research=True)
        result = services.get_scenario_instances("basic", user)
        assert {i["name"] for i in result} == {"Attacker", "Workstation"}
