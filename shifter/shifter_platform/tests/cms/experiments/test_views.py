"""Behavior tests for the experiment views.

Drives the real views through the Django test client against real users/groups,
real ``Experiment``/``ScriptAsset`` rows, the real services, and the real
templates — instead of calling view functions with ``RequestFactory`` + mock
users and patching ``render`` / ``services.*`` / the scenario registry.
"""

import json

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse

from cms.experiments.models import Experiment
from cms.experiments.schemas import ExperimentStatus
from shared.auth import THREAT_RESEARCH_GROUP

pytestmark = pytest.mark.django_db

User = get_user_model()

LIST_URL = reverse("experiments:experiment_list")
CREATE_URL = reverse("experiments:experiment_create")
SCRIPTS_URL = reverse("experiments:script_list")


def _user(suffix, *, is_staff=False, threat_research=False):
    user = User.objects.create_user(username=f"v-{suffix}@e.com", email=f"v-{suffix}@e.com", is_staff=is_staff)
    if threat_research:
        group, _ = Group.objects.get_or_create(name=THREAT_RESEARCH_GROUP)
        user.groups.add(group)
    return user


def _experiment(user, *, name="Exp", status=ExperimentStatus.DRAFT.value):
    return Experiment.objects.create(user=user, name=name, scenario_id="basic", status=status)


class TestAccessControl:
    def test_regular_user_redirected_from_experiment_list(self, authenticated_client):
        client, _ = authenticated_client(user=_user("acc-reg"))
        resp = client.get(LIST_URL)
        assert resp.status_code == 302
        assert "mission-control" in resp["Location"]

    def test_regular_user_redirected_from_script_list(self, authenticated_client):
        client, _ = authenticated_client(user=_user("acc-reg2"))
        resp = client.get(SCRIPTS_URL)
        assert resp.status_code == 302
        assert "mission-control" in resp["Location"]

    def test_staff_can_access_experiment_list(self, authenticated_client):
        client, _ = authenticated_client(user=_user("acc-staff", is_staff=True))
        assert client.get(LIST_URL).status_code == 200

    def test_threat_research_can_access_experiment_list(self, authenticated_client):
        client, _ = authenticated_client(user=_user("acc-tr", threat_research=True))
        assert client.get(LIST_URL).status_code == 200

    def test_staff_can_access_script_list(self, authenticated_client):
        client, _ = authenticated_client(user=_user("acc-staff2", is_staff=True))
        assert client.get(SCRIPTS_URL).status_code == 200


class TestExperimentListView:
    def test_shows_own_experiments(self, authenticated_client):
        staff = _user("list-staff", is_staff=True)
        _experiment(staff, name="Mine One")
        _experiment(staff, name="Mine Two")
        client, _ = authenticated_client(user=staff)

        resp = client.get(LIST_URL)
        assert resp.status_code == 200
        names = {e.name for e in resp.context["experiments"]}
        assert {"Mine One", "Mine Two"} <= names

    def test_excludes_other_users_experiments(self, authenticated_client):
        staff = _user("list-owner", is_staff=True)
        other = _user("list-other", is_staff=True)
        _experiment(other, name="Theirs")
        client, _ = authenticated_client(user=staff)

        resp = client.get(LIST_URL)
        assert [e.name for e in resp.context["experiments"]] == []


class TestExperimentDetailView:
    def test_detail_shows_owned_experiment(self, authenticated_client):
        staff = _user("detail-staff", is_staff=True)
        exp = _experiment(staff, name="Detail Test")
        client, _ = authenticated_client(user=staff)

        resp = client.get(reverse("experiments:experiment_detail", kwargs={"experiment_id": exp.pk}))
        assert resp.status_code == 200
        assert resp.context["experiment"].pk == exp.pk

    def test_detail_other_users_experiment_redirects(self, authenticated_client):
        staff = _user("detail-staff2", is_staff=True)
        other = _user("detail-other", is_staff=True)
        exp = _experiment(other)
        client, _ = authenticated_client(user=staff)

        resp = client.get(reverse("experiments:experiment_detail", kwargs={"experiment_id": exp.pk}))
        assert resp.status_code == 302


class TestExperimentStartCancelViews:
    def test_start_requires_post(self, authenticated_client):
        staff = _user("start-staff", is_staff=True)
        exp = _experiment(staff)
        client, _ = authenticated_client(user=staff)
        assert client.get(reverse("experiments:experiment_start", kwargs={"experiment_id": exp.pk})).status_code == 405

    def test_start_draft_experiment_queues_it(self, authenticated_client, settings):
        settings.SQS_QUEUE_CONFIG = {}  # publish is best-effort; unconfigured is fine
        staff = _user("start-staff2", is_staff=True)
        exp = _experiment(staff, status=ExperimentStatus.DRAFT.value)
        client, _ = authenticated_client(user=staff)

        resp = client.post(reverse("experiments:experiment_start", kwargs={"experiment_id": exp.pk}))
        assert resp.status_code == 302
        exp.refresh_from_db()
        assert exp.status == ExperimentStatus.QUEUED.value

    def test_cancel_requires_post(self, authenticated_client):
        staff = _user("cancel-staff", is_staff=True)
        exp = _experiment(staff)
        client, _ = authenticated_client(user=staff)
        assert client.get(reverse("experiments:experiment_cancel", kwargs={"experiment_id": exp.pk})).status_code == 405

    def test_cancel_queued_experiment(self, authenticated_client):
        staff = _user("cancel-staff2", is_staff=True)
        exp = _experiment(staff, status=ExperimentStatus.QUEUED.value)
        client, _ = authenticated_client(user=staff)

        resp = client.post(reverse("experiments:experiment_cancel", kwargs={"experiment_id": exp.pk}))
        assert resp.status_code == 302
        exp.refresh_from_db()
        assert exp.status == ExperimentStatus.CANCELLED.value


class TestExperimentCreateView:
    def _form(self, **overrides):
        data = {
            "name": "View Exp",
            "scenario_id": "basic",
            "total_runs": "2",
            "max_parallel_runs": "1",
            "scripts_json": "[]",
        }
        data.update(overrides)
        return data

    def test_get_renders_form(self, authenticated_client):
        client, _ = authenticated_client(user=_user("create-staff", is_staff=True))
        resp = client.get(CREATE_URL)
        assert resp.status_code == 200

    def test_post_valid_creates_experiment(self, authenticated_client):
        staff = _user("create-staff2", is_staff=True)
        client, _ = authenticated_client(user=staff)

        resp = client.post(CREATE_URL, data=self._form(name="Created Via View"))
        assert resp.status_code == 302
        assert Experiment.objects.filter(name="Created Via View", user=staff).exists()

    @pytest.mark.parametrize(
        "overrides",
        [
            {"scenario_id": "nonexistent"},
            {"scripts_json": "{not valid json"},
            {"name": ""},
            {"total_runs": "1", "max_parallel_runs": "5"},
        ],
        ids=["bad-scenario", "malformed-json", "empty-name", "parallel-exceeds-total"],
    )
    def test_post_invalid_redirects_without_creating(self, authenticated_client, overrides):
        staff = _user(f"create-bad-{overrides.get('scenario_id', 'x')[:4]}-{len(overrides)}", is_staff=True)
        client, _ = authenticated_client(user=staff)

        before = Experiment.objects.count()
        resp = client.post(CREATE_URL, data=self._form(**overrides))
        assert resp.status_code == 302
        assert Experiment.objects.count() == before

    def test_threat_research_user_blocked_from_hidden_scenario(self, authenticated_client):
        """Regression #771: a non-staff Threat Research user must not create an
        experiment for a scenario the service rejects; the view surfaces the
        denial as a redirect, not a created experiment.
        """
        tr = _user("create-tr-hidden", threat_research=True)
        client, _ = authenticated_client(user=tr)

        before = Experiment.objects.count()
        resp = client.post(CREATE_URL, data=self._form(scenario_id="hidden-internal-xyz"))
        assert resp.status_code == 302
        assert Experiment.objects.count() == before


class TestScenarioInstancesView:
    def _url(self, scenario_id):
        return reverse("experiments:scenario_instances", kwargs={"scenario_id": scenario_id})

    def test_returns_instances(self, authenticated_client):
        client, _ = authenticated_client(user=_user("scn-staff", is_staff=True))
        resp = client.get(self._url("basic"))
        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert {i["name"] for i in data["instances"]} == {"Attacker", "Workstation"}

    def test_invalid_scenario_returns_400_without_leaking_detail(self, authenticated_client):
        client, _ = authenticated_client(user=_user("scn-staff2", is_staff=True))
        resp = client.get(self._url("nonexistent_xyz"))
        assert resp.status_code == 400
        # The body is an authored, classified message; the raw scenario id (and any
        # internal exception detail) must not reach the client.
        body = resp.content.decode()
        assert "nonexistent_xyz" not in body
        assert json.loads(body)["error"]  # non-empty authored message
