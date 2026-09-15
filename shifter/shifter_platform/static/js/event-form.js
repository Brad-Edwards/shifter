/* global ShifterDropdown */
// CTF admin event create/edit form: field collection, client-side
// validation, submit, scenario dropdown population, and (in edit mode)
// pre-populating fields from the API. Extracted from the inline
// <script> in templates/ctf/admin/event_form.html so the template stays
// within Sonar Web:LongJavaScriptCheck and Web:FileLengthCheck limits.
// Behavior is unchanged; edit-mode/CSRF/API configuration is read from
// the #event-form-config element's data-* attributes and the scenario
// list from the #scenarios-data json_script block.

document.addEventListener('DOMContentLoaded', function () {
    var cfg = document.getElementById('event-form-config').dataset;
    var IS_EDIT = cfg.isEdit === 'true';
    var CSRF_TOKEN = cfg.csrfToken;
    var API_URL = cfg.apiUrl;
    var EVENT_ID = cfg.eventId || null;

    // --- DOM refs ---
    var alertEl = document.getElementById('form-alert');
    var submitBtn = document.getElementById('submit-btn');

    var FIELDS = [
        'name', 'description', 'event_start', 'event_end',
        'registration_deadline', 'scoreboard_visible', 'scoreboard_freeze_at', 'scenario_id',
        'range_spinup_minutes', 'auto_cleanup', 'ngfw_enabled',
        'cleanup_delay_hours', 'max_participants', 'team_mode',
        'team_size_limit', 'submission_cooldown_seconds',
        'attempt_limit_mode', 'attempt_limit_cooldown_seconds',
        'rating_visibility', 'scoring_mode'
    ];

    // --- Helpers ---
    function getField(name) {
        return document.getElementById('field-' + name);
    }

    function getError(name) {
        return document.getElementById('error-' + name);
    }

    function clearErrors() {
        alertEl.textContent = '';
        alertEl.classList.remove('visible');
        FIELDS.forEach(function(name) {
            var el = getError(name);
            if (el) el.textContent = '';
        });
    }

    function toggleCooldownVisibility() {
        var mode = getField('attempt_limit_mode').value;
        var group = document.getElementById('cooldown-group');
        if (group) group.style.display = mode === 'timeout' ? '' : 'none';
    }

    getField('attempt_limit_mode').addEventListener('change', toggleCooldownVisibility);
    toggleCooldownVisibility();

    function showFieldError(name, msg) {
        var el = getError(name);
        if (el) el.textContent = msg;
    }

    function showAlert(msg) {
        alertEl.textContent = msg;
        alertEl.classList.add('visible');
        alertEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    /**
     * Convert a datetime-local value (YYYY-MM-DDTHH:MM) to ISO-8601
     * with seconds appended for Django parse_datetime.
     */
    function toISODatetime(val) {
        if (!val) return null;
        return val.length === 16 ? val + ':00' : val;
    }

    /**
     * Convert an ISO datetime string to datetime-local value (YYYY-MM-DDTHH:MM).
     */
    function toDatetimeLocal(iso) {
        if (!iso) return '';
        return iso.substring(0, 16);
    }

    // --- Build form data ---
    function collectFormData() {
        var data = {
            name: getField('name').value.trim(),
            description: getField('description').value,
            event_start: toISODatetime(getField('event_start').value),
            event_end: toISODatetime(getField('event_end').value),
            auto_cleanup: getField('auto_cleanup').checked,
            scoreboard_visible: getField('scoreboard_visible').checked,
            team_mode: getField('team_mode').checked,
        };

        var regDeadline = getField('registration_deadline').value;
        data.registration_deadline = regDeadline ? toISODatetime(regDeadline) : null;

        var freezeAt = getField('scoreboard_freeze_at').value;
        data.scoreboard_freeze_at = freezeAt ? toISODatetime(freezeAt) : null;

        var scenarioId = getField('scenario_id').value;
        data.scenario_id = scenarioId || null;

        var spinup = getField('range_spinup_minutes').value;
        data.range_spinup_minutes = spinup ? parseInt(spinup, 10) : 30;

        var cleanupHours = getField('cleanup_delay_hours').value;
        data.cleanup_delay_hours = cleanupHours ? parseInt(cleanupHours, 10) : 0;

        var maxParticipants = getField('max_participants').value;
        data.max_participants = maxParticipants ? parseInt(maxParticipants, 10) : null;

        var teamSizeLimit = getField('team_size_limit').value;
        data.team_size_limit = teamSizeLimit ? parseInt(teamSizeLimit, 10) : null;

        var submissionCooldown = getField('submission_cooldown_seconds').value;
        data.submission_cooldown_seconds = submissionCooldown ? parseInt(submissionCooldown, 10) : 0;

        data.attempt_limit_mode = getField('attempt_limit_mode').value;

        var attemptCooldown = getField('attempt_limit_cooldown_seconds').value;
        data.attempt_limit_cooldown_seconds = attemptCooldown ? parseInt(attemptCooldown, 10) : 300;

        data.rating_visibility = getField('rating_visibility').value;

        data.scoring_mode = getField('scoring_mode').value;

        // Pack ngfw_enabled into range_config
        data.range_config = {
            ngfw_enabled: getField('ngfw_enabled').checked
        };

        return data;
    }

    // --- Client-side validation ---
    function validate(data) {
        var valid = true;
        clearErrors();

        if (!data.name) {
            showFieldError('name', 'Event name is required.');
            valid = false;
        }
        if (!data.event_start) {
            showFieldError('event_start', 'Event start is required.');
            valid = false;
        }
        if (!data.event_end) {
            showFieldError('event_end', 'Event end is required.');
            valid = false;
        }
        if (data.event_start && data.event_end && data.event_end <= data.event_start) {
            showFieldError('event_end', 'Event end must be after event start.');
            valid = false;
        }
        if (data.registration_deadline && data.event_start && data.registration_deadline >= data.event_start) {
            showFieldError('registration_deadline', 'Registration deadline must be before event start.');
            valid = false;
        }
        if (data.team_mode && (!data.team_size_limit || data.team_size_limit < 2 || data.team_size_limit > 10)) {
            showFieldError('team_size_limit', 'Team size must be between 2 and 10 when team mode is enabled.');
            valid = false;
        }
        if (data.scoreboard_freeze_at) {
            if (data.event_start && data.scoreboard_freeze_at <= data.event_start) {
                showFieldError('scoreboard_freeze_at', 'Scoreboard freeze time must be after event start.');
                valid = false;
            }
            if (data.event_end && data.scoreboard_freeze_at >= data.event_end) {
                showFieldError('scoreboard_freeze_at', 'Scoreboard freeze time must be before event end.');
                valid = false;
            }
        }

        if (!valid) {
            showAlert('Please fix the errors below.');
        }
        return valid;
    }

    // --- Submit ---
    function submitForm() {
        var data = collectFormData();
        if (!validate(data)) return;

        submitBtn.disabled = true;
        submitBtn.textContent = IS_EDIT ? 'Saving...' : 'Creating...';

        var method = IS_EDIT ? 'PUT' : 'POST';

        fetch(API_URL, {
            method: method,
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': CSRF_TOKEN,
            },
            body: JSON.stringify(data),
        })
        .then(function(resp) {
            return resp.json().then(function(json) {
                return { ok: resp.ok, status: resp.status, data: json };
            });
        })
        .then(function(result) {
            if (result.ok) {
                var eventId = IS_EDIT ? EVENT_ID : result.data.id;
                window.location.href = '/ctf/admin/events/' + eventId + '/';
            } else {
                clearErrors();
                var errorMsg = result.data.error || 'An error occurred.';

                // Try to map field-specific errors
                if (result.data.details && result.data.details.field_errors) {
                    var fieldErrors = result.data.details.field_errors;
                    Object.entries(fieldErrors).forEach(function(entry) {
                        showFieldError(entry[0], entry[1]);
                    });
                }

                showAlert(errorMsg);
                submitBtn.disabled = false;
                submitBtn.textContent = IS_EDIT ? 'Save Changes' : 'Create Event';
            }
        })
        .catch(function(err) {
            clearErrors();
            showAlert('Network error: ' + err.message);
            submitBtn.disabled = false;
            submitBtn.textContent = IS_EDIT ? 'Save Changes' : 'Create Event';
        });
    }

    submitBtn.addEventListener('click', submitForm);

    // --- Load scenarios into dropdown ---
    function loadScenarios() {
        var scenarios = JSON.parse(document.getElementById('scenarios-data').textContent);
        var itemsEl = document.getElementById('scenario-items');

        // Clear existing items
        while (itemsEl.firstChild) {
            itemsEl.removeChild(itemsEl.firstChild);
        }

        // Add empty option
        var emptyLi = document.createElement('li');
        emptyLi.className = 'shifter-dropdown-item';
        emptyLi.dataset.value = '';
        emptyLi.textContent = '(None)';
        itemsEl.appendChild(emptyLi);

        scenarios.forEach(function(s) {
            var li = document.createElement('li');
            li.className = 'shifter-dropdown-item';
            li.dataset.value = s.id;
            li.textContent = s.name;
            itemsEl.appendChild(li);
        });

        // Initialize the dropdown
        return ShifterDropdown.init(document.getElementById('scenario-dropdown'));
    }

    var scenarioDropdown = loadScenarios();

    // --- Edit mode: populate fields from API ---
    function populateForm() {
        fetch(API_URL, {
            headers: { 'X-CSRFToken': CSRF_TOKEN },
        })
        .then(function(resp) { return resp.json(); })
        .then(function(event) {
            getField('name').value = event.name || '';
            getField('description').value = event.description || '';
            getField('event_start').value = toDatetimeLocal(event.event_start);
            getField('event_end').value = toDatetimeLocal(event.event_end);
            getField('registration_deadline').value = toDatetimeLocal(event.registration_deadline);
            getField('scoreboard_visible').checked = event.scoreboard_visible !== false;
            getField('scoreboard_freeze_at').value = toDatetimeLocal(event.scoreboard_freeze_at);
            getField('range_spinup_minutes').value = event.range_spinup_minutes || 30;
            getField('auto_cleanup').checked = !!event.auto_cleanup;
            getField('cleanup_delay_hours').value = event.cleanup_delay_hours || 0;
            getField('max_participants').value = event.max_participants || '';
            getField('team_mode').checked = !!event.team_mode;
            getField('team_size_limit').value = event.team_size_limit || '';
            getField('submission_cooldown_seconds').value = event.submission_cooldown_seconds || 0;
            getField('attempt_limit_mode').value = event.attempt_limit_mode || 'lockout';
            getField('attempt_limit_cooldown_seconds').value = event.attempt_limit_cooldown_seconds || 300;
            getField('rating_visibility').value = event.rating_visibility || 'public';
            getField('scoring_mode').value = event.scoring_mode || 'standard';
            toggleCooldownVisibility();

            // ngfw_enabled from range_config
            var rc = event.range_config || {};
            getField('ngfw_enabled').checked = !!rc.ngfw_enabled;

            // Set scenario dropdown value
            if (event.scenario_id && scenarioDropdown) {
                scenarioDropdown.setValue(event.scenario_id);
            }
        })
        .catch(function(err) {
            showAlert('Failed to load event data: ' + err.message);
        });
    }

    if (IS_EDIT) {
        populateForm();
    }
});
